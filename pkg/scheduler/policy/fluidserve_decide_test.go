package policy

import (
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"llumnix/pkg/consts"
	"llumnix/pkg/types"
)

// Tests for the decision itself: which instance a request goes to, when it is
// held at the gateway instead, when it is rejected, and when it is placed
// somewhere that cannot serve it well because there is nothing better left.
//
// The decision is a ladder and each rung is settled by one quantity, so the
// tests are organised the same way.

func fsPolicy(t *testing.T, budgets string, mutate func(*fluidserveConfig)) *fluidserveDispatchPolicy {
	t.Helper()
	b, err := parseClassBudgets(budgets)
	require.NoError(t, err)

	cfg := fluidserveConfig{
		horizonSteps:   100,
		zSafety:        1.65,
		ttftSafetyMs:   300,
		enablePend:     true,
		enableShed:     true,
		enableAffinity: true,
		enableFlux:     true,
	}
	if mutate != nil {
		mutate(&cfg)
	}
	lengths := testLengths()
	p := &fluidserveDispatchPolicy{
		cfg:      cfg,
		capacity: testCapacity(t),
		lengths:  lengths,
		registry: newRequestRegistry(lengths, b),
		shedIDs:  map[string]int64{},
	}
	p.baseDispatchPolicy = baseDispatchPolicy{
		consts.InferTypeNeutral: {
			metrics:   map[string]func() instanceSchedulingMetric{},
			selectors: &fluidserveSelector{policy: p},
		},
	}
	return p
}

// decide runs the same two steps the scheduling path runs: the per-request hook
// that builds the state, then the selector.
func decide(p *fluidserveDispatchPolicy, req *types.SchedulingRequest,
	views map[string]*instanceViewScheduling) *instanceViewScheduling {

	p.calculateMetrics(consts.InferTypeNeutral, req, views)
	return p.baseDispatchPolicy[consts.InferTypeNeutral].selectors.selectInstance(views, false)
}

func fsRequest(id string, tier, ttftSloMs, prompt int) *types.SchedulingRequest {
	return &types.SchedulingRequest{
		Id:              id,
		SchedulingMode:  types.SchedulingModeNeutral,
		TpotSloMs:       tier,
		TtftSloMs:       ttftSloMs,
		PromptNumTokens: prompt,
	}
}

// fill puts n requests of one tier on one instance in the registry, as if they
// had been dispatched there at step `atStep` and had not yet produced a token.
//
// The step matters: progress is recovered from the difference between the
// engine's current step counter and the counter at dispatch, so a record left
// thousands of steps behind the view is read as a request that has produced
// thousands of tokens, and is retired as complete before the decision sees it.
func fill(p *fluidserveDispatchPolicy, inst string, tier, prompt, n int,
	atStep int64, atMs int64) {
	for i := 0; i < n; i++ {
		id := fmt.Sprintf("%s-%d-%d", inst, tier, i)
		p.registry.noteArrival(id, atMs)
		p.registry.onDispatch(inst, id, tier, prompt, 8192, atStep, atMs)
	}
}

// ---------------------------------------------------------------------------
// Rung 1: a feasible instance exists
// ---------------------------------------------------------------------------

func TestAmongFeasibleInstancesTheClassGoesWhereItAlreadyIs(t *testing.T) {
	// The rule that produces a separation without assigning any instance to a
	// class. Two instances with identical free space, one already holding this
	// class. Filling that one first is what keeps the pace each instance is held
	// to uniform, because an instance's admissible occupancy is set by the
	// tightest budget on it and mixing classes wastes capacity in both
	// directions.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"mostlyChat": fsView(fsViewOpts{id: "mostlyChat", decodeReqs: 10,
			decodeTokens: 200_000, usedGpu: 40_000, stepID: 5000}),
		"mixed": fsView(fsViewOpts{id: "mixed", decodeReqs: 10,
			decodeTokens: 200_000, usedGpu: 40_000, stepID: 5000}),
	}
	fill(p, "mostlyChat", 50, 1000, 10, 4990, now)
	for i := 0; i < 10; i++ {
		id := fmt.Sprintf("b-%d", i)
		p.registry.noteArrival(id, now)
		tier := 50
		if i%2 == 1 {
			tier = 100
		}
		p.registry.onDispatch("mixed", id, tier, 1000, 8192, 4990, now)
	}
	got := decide(p, fsRequest("chat-new", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "mostlyChat", got.GetInstanceId())
}

func TestAffinityNeverOverridesTheCapacityGate(t *testing.T) {
	// The property the weighted sum did not have, and the direct cause of the
	// failure it produced: one instance holding the whole of a class attracted
	// the rest of that class no matter how far past its capacity it already was,
	// ending with a queue of 5,569 requests on one engine while three others sat
	// idle. Applying the preference only inside the feasible set makes that
	// impossible -- a preference can order instances, it can never admit one.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"collapsed": fsView(fsViewOpts{id: "collapsed", decodeReqs: 200,
			decodeTokens: 60_000_000, pendingPre: 500_000, usedGpu: 570_000,
			stepID: 9000}),
		"healthy": fsView(fsViewOpts{id: "healthy", decodeReqs: 5,
			decodeTokens: 100_000, usedGpu: 20_000, stepID: 9000}),
	}
	// The collapsed instance holds the entire class being placed, so the
	// preference is pulling as hard as it can toward it.
	fill(p, "collapsed", 50, 1000, 200, 8990, now-60_000)

	got := decide(p, fsRequest("chat-new", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "healthy", got.GetInstanceId())
}

func TestQueuedPrefillPushesRequestsElsewhere(t *testing.T) {
	// Two instances holding the same KV, but one has a long prompt still to
	// prefill. Every iteration that carries a chunk is an iteration in which
	// every decoding request on that instance waits several hundred
	// milliseconds for its next token, so the instance with the queued prompt
	// should be avoided even though its occupancy is identical.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	views := map[string]*instanceViewScheduling{
		"prefilling": fsView(fsViewOpts{id: "prefilling", decodeReqs: 10, decodeTokens: 100000,
			pendingPre: 24576, usedGpu: 80000, stepID: 5000}),
		"clear": fsView(fsViewOpts{id: "clear", decodeReqs: 10, decodeTokens: 100000,
			usedGpu: 80000, stepID: 5000}),
	}
	got := decide(p, fsRequest("r1", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "clear", got.GetInstanceId())
}

func TestWithNoAffinityFreeSpaceDecides(t *testing.T) {
	// The ablation that isolates where the separation comes from. Without the
	// preference the two instances differ only in occupancy, so the emptier one
	// wins and the class spreads -- which is the uniform mixture that seven
	// measured configurations sat at.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", func(c *fluidserveConfig) {
		c.enableAffinity = false
	})
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"mostlyChat": fsView(fsViewOpts{id: "mostlyChat", decodeReqs: 10,
			decodeTokens: 300_000, usedGpu: 60_000, stepID: 5000}),
		"emptier": fsView(fsViewOpts{id: "emptier", decodeReqs: 2,
			decodeTokens: 40_000, usedGpu: 10_000, stepID: 5000}),
	}
	fill(p, "mostlyChat", 50, 1000, 10, 4990, now)
	got := decide(p, fsRequest("chat-new", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "emptier", got.GetInstanceId())
}

// ---------------------------------------------------------------------------
// The gate itself
// ---------------------------------------------------------------------------

func TestTheGateIsSetByTheClassPaceNotByHowLateTheInstanceIs(t *testing.T) {
	// The feedback loop this removes: when the gate was the tightest REMAINING
	// budget on the instance, an instance that fell behind reported a smaller
	// allowance, which lowered its capacity, which made it refuse work and fall
	// further behind. The gate is now the tightest NOMINAL pace, which is a
	// property of the classes present and does not move as the instance ages.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 4, decodeTokens: 80_000,
			usedGpu: 20_000, stepID: 5000}),
	}
	// Four end-to-end requests that arrived 20 s ago against a 30 s budget, so
	// each has a third of its account left and needs about 25 ms per remaining
	// token. The class was promised the whole budget over its expected output,
	// which for this fixture is about 77 ms per token.
	fill(p, "a", 25, 1000, 4, 4990, now-20_000)

	p.calculateMetrics(consts.InferTypeNeutral, fsRequest("probe", 50, 5000, 1000), views)
	f := views["a"].schedulingCtx.fluidserveFlux
	require.NotNil(t, f)
	nominal, _, _, _ := p.registry.requestBudget(25)
	assert.InDelta(t, nominal, f.gateAllowance, 1e-9,
		"the gate is the pace the class was promised")
	assert.Less(t, f.tightestAllowance, 30.0,
		"while the remaining budget has indeed shrunk, and is reported separately")
}

func TestUnachievableRequestsDoNotPinInstanceCapacity(t *testing.T) {
	// A request whose remaining budget per token has fallen below the cost of
	// an iteration on an empty instance cannot be met by any placement. If it
	// still set the instance's capacity, that instance would stop accepting
	// work without the doomed request being any better off, so it is excluded
	// from the gate and served on a best-effort basis.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 2, decodeTokens: 20000,
			usedGpu: 15000, stepID: 5000}),
	}
	// An end-to-end request that has already spent nearly its whole budget.
	p.registry.noteArrival("doomed", now-15900)
	p.registry.onDispatch("a", "doomed", 25, 20000, 8192, 4900, now-15900)

	p.calculateMetrics(consts.InferTypeNeutral, fsRequest("probe", 50, 5000, 1000), views)
	f := views["a"].schedulingCtx.fluidserveFlux
	require.NotNil(t, f)
	assert.Equal(t, 1, f.unachievable)
	assert.Greater(t, f.headroom, 0.0,
		"the instance should still be usable for requests that can be met")
}

// ---------------------------------------------------------------------------
// Rung 2: nothing feasible, but there is still time to wait
// ---------------------------------------------------------------------------

func TestRequestIsHeldWhileItCanStillAffordToWait(t *testing.T) {
	// Holding costs the fleet nothing and keeps every option open, whereas
	// committing the request to an engine puts it in a queue it cannot be taken
	// out of. It is preferred for as long as a placement made later could still
	// meet the budget.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 30, decodeTokens: 2_400_000,
			usedGpu: 300_000, stepID: 9000}),
		"b": fsView(fsViewOpts{id: "b", decodeReqs: 30, decodeTokens: 2_400_000,
			usedGpu: 300_000, stepID: 9000}),
	}
	fill(p, "a", 50, 1000, 30, 8990, now)
	fill(p, "b", 50, 1000, 30, 8990, now)

	// A long prompt would make several of the next iterations carry a chunk,
	// which is what the incumbents cannot absorb.
	got := decide(p, fsRequest("heavy", 25, 11800, 40000), views)
	assert.Nil(t, got, "there is still time, so the request waits rather than breaking others")
}

func TestHoldingUsesTheWholeFirstTokenBudget(t *testing.T) {
	// An earlier version capped the hold at a quarter of the time-to-first-token
	// budget, reasoning that a request held longer was a miss whatever happened
	// next. That was wrong: the rule is time to first token AND mean time
	// between tokens, so a request held for most of its first-token budget and
	// then served immediately meets both. The cap is gone.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5_000_000,
			pendingPre: 200_000, usedGpu: 570_000, stepID: 9000}),
	}
	// Waited 2.5 s of a 5 s budget: far past the old quarter-budget cap, still
	// leaving room for the prefill of a short prompt.
	p.registry.noteArrival("waiting", now-2500)
	assert.Nil(t, decide(p, fsRequest("waiting", 50, 5000, 1000), views))
}

func TestPendCanBeDisabledForAblation(t *testing.T) {
	p := fsPolicy(t, "25:e2e:16000,50:decode", func(c *fluidserveConfig) {
		c.enablePend = false
		c.enableShed = false
	})
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5000000,
			pendingPre: 200000, usedGpu: 570000, stepID: 9000}),
	}
	got := decide(p, fsRequest("r1", 50, 5000, 1000), views)
	require.NotNil(t, got, "with holding disabled the request must be placed immediately")
}

// ---------------------------------------------------------------------------
// Rung 3: out of time, and the placement would still miss
// ---------------------------------------------------------------------------

func TestRequestIsRejectedOnceNoPlacementCanMeetItsOwnBudget(t *testing.T) {
	// The admission decision, and the one place FluidServe departs from every
	// occupancy-threshold admission rule: the test is not how full the fleet is,
	// it is what would happen to THIS request. A request that has spent its
	// first-token budget waiting is going to be a violation however it is
	// served, so the only remaining question is whether it also takes the
	// capacity that decides whether its neighbours are violations too.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5_000_000,
			pendingPre: 200_000, usedGpu: 570_000, stepID: 9000}),
	}
	p.registry.noteArrival("late", now-4900)
	assert.Nil(t, decide(p, fsRequest("late", 50, 5000, 1000), views))
	assert.True(t, p.admissionRejected("late"),
		"the decision has to reach the gateway as a rejection, not as another wait")
	assert.False(t, p.admissionRejected("late"), "and it is reported exactly once")
}

func TestShedCanBeDisabledForAblation(t *testing.T) {
	// With rejection off the same request is placed anyway. Both arms lose it;
	// the question the ablation answers is whether the capacity it consumes
	// costs the requests around it as well.
	p := fsPolicy(t, "25:e2e:16000,50:decode", func(c *fluidserveConfig) {
		c.enableShed = false
	})
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5_000_000,
			pendingPre: 200_000, usedGpu: 570_000, stepID: 9000}),
	}
	p.registry.noteArrival("late", now-4900)
	got := decide(p, fsRequest("late", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.False(t, p.admissionRejected("late"))
}

// ---------------------------------------------------------------------------
// Rung 4: out of time, but the request can still make its own budget
// ---------------------------------------------------------------------------

func TestHeavyWorkGoesWhereItCanNoLongerMakeAnythingWorse(t *testing.T) {
	// The ordering that applies once nothing is feasible. An agent request
	// carries a 22k-token prompt, which makes several of the next iterations
	// carry a prefill chunk and delays every request already decoding wherever
	// it lands. Its own per-token budget is LOOSER than an interactive
	// request's, so a rule that prices only the budget a request declares
	// charges it nothing for landing among interactive requests and pushing all
	// of them past their limit. Pricing the damage instead sends it to the
	// instance whose requests have already lost their budgets, which is what
	// keeps the damage in one place rather than spreading it over the fleet.
	p := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
		c.enablePend = false
		c.enableShed = false
	})
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		// Serving interactive requests, close to but inside their budget.
		"interactive": fsView(fsViewOpts{id: "interactive", decodeReqs: 20,
			decodeTokens: 1_600_000, usedGpu: 200_000, stepID: 5000}),
		// Already past what its requests can absorb.
		"loaded": fsView(fsViewOpts{id: "loaded", decodeReqs: 20,
			decodeTokens: 1_600_000, pendingPre: 200_000, usedGpu: 200_000,
			stepID: 5000}),
	}
	fill(p, "interactive", 50, 1000, 20, 4990, now)
	fill(p, "loaded", 25, 20000, 20, 4990, now-25_000)

	got := decide(p, fsRequest("swe-new", 25, 11800, 22000), views)
	require.NotNil(t, got)
	assert.Equal(t, "loaded", got.GetInstanceId(),
		"heavy work belongs where it can no longer make anything worse")
}

func TestRequestsAlreadyPastTheirBudgetDoNotMakeAnInstanceExpensive(t *testing.T) {
	// The asymmetry that produces the concentration: an instance whose requests
	// are going to miss regardless is the cheap place for more heavy work, so
	// once one instance has absorbed it, it keeps absorbing it and the others
	// stay clean. Counting further delay to an already-lost request as a cost
	// would push the heavy work back out across the fleet.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	f := &instanceFlux{
		meanStep: 200,
		live: []liveRequest{
			{allowanceMs: 50},  // 200ms per token already; nothing left to lose
			{allowanceMs: 300}, // still has room
		},
	}
	harm := p.harmToIncumbents(f, 200, 260)
	// Only the second request contributes: 60ms of extra delay against the
	// 100ms of slack it had.
	assert.InDelta(t, 0.6, harm, 1e-9)
}

// ---------------------------------------------------------------------------
// Bookkeeping
// ---------------------------------------------------------------------------

func TestDispatchIsRecordedForTheNextDecision(t *testing.T) {
	// A request placed now does not appear in the engine's reported state for
	// up to a polling interval. Without recording it, several requests sent
	// during that interval would each be judged against a state that does not
	// include the others.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 4, decodeTokens: 40000,
			usedGpu: 30000, stepID: 5000}),
	}
	require.NotNil(t, decide(p, fsRequest("r1", 50, 5000, 1000), views))
	assert.Equal(t, 1, p.registry.instanceCount("a"))
	require.NotNil(t, decide(p, fsRequest("r2", 50, 5000, 1000), views))
	assert.Equal(t, 2, p.registry.instanceCount("a"))
}

func TestClassShareIsZeroOnAnEmptyInstance(t *testing.T) {
	// An instance holding nothing belongs to no class, so it neither attracts
	// nor repels; free space decides, which is what lets a fresh instance be
	// claimed at all.
	assert.Zero(t, classShare(&instanceFlux{}, 50))
	f := &instanceFlux{live: []liveRequest{{tier: 50}, {tier: 50}, {tier: 25}}}
	assert.InDelta(t, 2.0/3.0, classShare(f, 50), 1e-9)
	assert.InDelta(t, 1.0/3.0, classShare(f, 25), 1e-9)
	assert.Zero(t, classShare(f, 100))
}

func TestIterationTimeIsMeasuredAcrossStatuses(t *testing.T) {
	// The engine's own single-step duration field is published only for steps
	// that carried a prefill chunk, and only while a bounded profiling budget
	// lasts, so it cannot be used to measure decode cost. The step counter and
	// the status timestamp are always published, and their differences give the
	// mean iteration time over the interval between two statuses.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)

	v := fsView(fsViewOpts{id: "a", decodeReqs: 10, decodeTokens: 100000,
		usedGpu: 80000, stepID: 1000})
	v.cmsView.Status.TimestampMs = 1_000_000
	assert.Equal(t, -1.0, p.observeInstance(v), "the first status has nothing to compare to")

	// 50 iterations in 1000 ms is 20 ms each.
	v.cmsView.Status.StepId = 1050
	v.cmsView.Status.TimestampMs = 1_001_000
	assert.InDelta(t, 20.0, p.observeInstance(v), 1e-9)

	// A repeated call before the engine advances must not resample the same
	// interval, which would let one measurement dominate any average taken here.
	assert.Equal(t, -1.0, p.observeInstance(v))

	// A pair straddling an engine restart or a long stall describes neither
	// interval and is discarded.
	v.cmsView.Status.StepId = 1_000_050
	v.cmsView.Status.TimestampMs = 1_002_000
	assert.Equal(t, -1.0, p.observeInstance(v))
}

func TestIdleInstancesReportNoIterationTime(t *testing.T) {
	// An engine with nothing to decode still advances its step counter, but the
	// time between those steps is time spent waiting for work. Treating it as an
	// iteration time would report hundreds of milliseconds for an instance that
	// is in fact completely free.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	v := fsView(fsViewOpts{id: "a", decodeReqs: 0, decodeTokens: 0, stepID: 1000})
	v.cmsView.Status.TimestampMs = 1_000_000
	p.observeInstance(v)
	v.cmsView.Status.StepId = 1002
	v.cmsView.Status.TimestampMs = 1_003_000
	assert.Equal(t, -1.0, p.observeInstance(v))
}
