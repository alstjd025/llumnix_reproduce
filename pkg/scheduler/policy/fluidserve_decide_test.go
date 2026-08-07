package policy

import (
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"llumnix/pkg/cms"
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
		classHarm:      true,
		// The production default. Zero would make the gate zero and nothing
		// feasible, so the helper has to carry it rather than rely on the
		// zero value.
		gateSlack: 1.0,
		// Same reason: the zero value here means "no class preference at all",
		// which is an ablation rather than the shipped behaviour, so a test that
		// did not set it would silently measure the ablation.
		affinityWeight: 1.0,
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
		p.registry.noteArrival(id, 0, atMs)
		p.registry.onDispatch(inst, id, tier, prompt, 8192, atStep, atMs, 0, 0)
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
		p.registry.noteArrival(id, 0, now)
		tier := 50
		if i%2 == 1 {
			tier = 100
		}
		p.registry.onDispatch("mixed", id, tier, 1000, 8192, 4990, now, 0, 0)
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

func TestHeavyWorkCannotJoinRequestsThatAreUsingTheirSlack(t *testing.T) {
	// The condition that keeps a class out of an instance serving another one.
	// An agent request carries a 22k-token prompt, which makes three of the next
	// hundred iterations carry a prefill chunk and raises the mean iteration by
	// about 15 ms. That is comfortably inside the 45 ms an interactive class is
	// PROMISED, so the nominal gate on its own admits it; it is outside the
	// slack those requests still have once they have been running behind for a
	// while. Judging the placement against what the incumbents have left, rather
	// than against what they were promised, is what refuses it.
	//
	// The instance is built directly rather than driven through the registry
	// because what matters here is a state the registry only reaches after
	// several polling intervals of real time: requests that have already spent
	// part of their budget.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	behind := &instanceFlux{
		id: "chatty", chunk: 8192, kvLogical: 1_000_000, nDecode: 20,
		kvPhysical: 150_000, kvCapacity: 600_000,
		gateAllowance: 50, // the interactive class was promised 50 ms per token
		// but each of its requests has 35 ms left per remaining token, because
		// the instance has been running slower than the budget.
		tightestAllowance: 35,
		capKv:             2_000_000, capMem: 4_000_000, proj: 1_000_000,
		achievable: 20,
	}
	for i := 0; i < 20; i++ {
		behind.live = append(behind.live, liveRequest{tier: 50, allowanceMs: 35, nominalMs: 50})
	}
	behind.meanStep = p.capacity.meanStepMs(behind.kvLogical, behind.nDecode, 0,
		behind.chunk, p.cfg.horizonSteps)

	nominal, expected, isE2E, budget := p.registry.requestBudget(25)
	heavy := &fluidserveRequest{id: "swe-new", tier: 25, ttftSloMs: 11800,
		promptTokens: 22000, arrivedMs: 1000, nowMs: 1000, expectedToks: expected,
		nominalMs: nominal, isE2E: isE2E, budgetMs: budget}

	c := p.evaluate(behind, nil, heavy)
	assert.Less(t, c.meanAfter, c.gateAfter,
		"the promised pace on its own would admit this placement")
	assert.Greater(t, c.meanAfter, behind.tightestAllowance,
		"but it costs more than the incumbents have left")
	assert.False(t, c.feasible)

	// The same instance still takes another request of the class it is serving,
	// which costs a single chunk rather than three.
	light := &fluidserveRequest{id: "chat-new", tier: 50, ttftSloMs: 5000,
		promptTokens: 1000, arrivedMs: 1000, nowMs: 1000, expectedToks: 200,
		nominalMs: 50}
	assert.True(t, p.evaluate(behind, nil, light).feasible,
		"the instance is not simply closed")
}

func TestTheProjectionCountsPrefillTheEngineIsObservedCarrying(t *testing.T) {
	// The engine drains its prefill queue well inside one status pull, so what
	// it reports queued at a sampling instant is usually near zero while it
	// spends a real share of every iteration over the horizon on prompts that
	// arrive in between. Predicting from the instant rather than the interval
	// does not merely read low: the correction then absorbs the difference as a
	// multiplier on the whole mean, which scales the KV and batch terms with it
	// and shrinks the admissible occupancy far more than the missing prefill
	// would.
	//
	// The projection is therefore built from how long the engine's iterations
	// ACTUALLY took against what the decode law alone accounts for. Two
	// properties are asserted here and the second is the reason the earlier
	// rate-driven forms were replaced: the projection has to be per instance, so
	// that an instance carrying prefill is distinguished from one that is not.
	// A fleet-wide average raises every instance's prediction together, which
	// cannot order candidates and can only close the whole fleet at once.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	busyView := fsView(fsViewOpts{id: "busy", decodeReqs: 20, decodeTokens: 400_000,
		usedGpu: 120_000, stepID: 5000})
	calmView := fsView(fsViewOpts{id: "calm", decodeReqs: 20, decodeTokens: 400_000,
		usedGpu: 120_000, stepID: 5000})
	views := map[string]*instanceViewScheduling{"busy": busyView, "calm": calmView}
	probe := fsRequest("probe", 50, 5000, 1000)
	// Requests on both instances, so each is held to a budget at all and its
	// admissible occupancy is a finite number.
	now := nowMillis()
	fill(p, "busy", 50, 1000, 20, 4990, now)
	fill(p, "calm", 50, 1000, 20, 4990, now)
	for _, v := range views {
		v.cmsView.Status.TimestampMs = now
	}

	// First status: no interval has elapsed yet, so nothing has been measured
	// and nothing is projected.
	p.calculateMetrics(consts.InferTypeNeutral, probe, views)
	quiet := views["busy"].schedulingCtx.fluidserveFlux
	require.NotNil(t, quiet)
	assert.Zero(t, quiet.arrivingPrefill)
	quietStep, quietCap := quiet.meanStep, quiet.capKv

	// Second status. Both instances executed 100 iterations. The decode-only law
	// accounts for `decodeOnly` of each; "busy" took three times that, so two
	// thirds of its engine time went somewhere the decode law does not explain,
	// which is prefill. "calm" took exactly what the law predicts.
	decodeOnly := p.capacity.decodeStepMs(400_000, 20)
	const steps = 100
	for id, v := range views {
		v.cmsView.Status.StepId = 5100
		mean := decodeOnly
		if id == "busy" {
			mean = 3 * decodeOnly
		}
		v.cmsView.Status.TimestampMs = now + int64(mean*steps)
	}
	p.calculateMetrics(consts.InferTypeNeutral, probe, views)
	busy := views["busy"].schedulingCtx.fluidserveFlux
	calm := views["calm"].schedulingCtx.fluidserveFlux
	require.NotNil(t, busy)
	require.NotNil(t, calm)

	assert.Greater(t, busy.arrivingPrefill, 0.0,
		"engine time the decode law does not account for is prefill over the horizon")
	assert.Zero(t, calm.arrivingPrefill,
		"an instance running at the decode law is carrying no prefill")
	assert.Greater(t, busy.meanStep, quietStep,
		"and the predicted iteration has to reflect it")
	assert.Less(t, busy.capKv, quietCap,
		"so the instance may hold less while still meeting the same budget")
	assert.Greater(t, calm.capKv, busy.capKv,
		"the projection has to separate the two instances, not raise both together")
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
	p.registry.noteArrival("doomed", 0, now-15900)
	p.registry.onDispatch("a", "doomed", 25, 20000, 8192, 4900, now-15900, 0, 0)

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
	p.registry.noteArrival("waiting", 0, now-2500)
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
	p.registry.noteArrival("late", 0, now-4900)
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
	p.registry.noteArrival("late", 0, now-4900)
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
			{tier: 25, allowanceMs: 50},  // 200ms/token already; nothing to lose
			{tier: 25, allowanceMs: 300}, // still has room
		},
	}
	// Placing another request of the class that already owns the instance.
	harm := p.harmToIncumbents(f, 25, 200, 260)
	// Only the second request contributes: 60ms of extra delay against the
	// 100ms of slack it had. The class term is zero because nothing on the
	// instance belongs to another class.
	assert.InDelta(t, 0.6, harm, 1e-9)
}

func TestAnInstanceBelongingToAnotherClassIsNotFreeJustBecauseItIsLate(t *testing.T) {
	// The counterpart to the test above, and the case it did not cover. The
	// asymmetry that makes overload collect where it is already lost reads only
	// the incumbents' remaining budgets, so an instance whose requests have all
	// gone past theirs reads as costing nothing -- no matter WHICH class those
	// requests belong to. An instance full of late chat requests would then be
	// the cheapest place to put an agent request, and the next chat request to
	// arrive would have nowhere clean to go. Measured with admission disabled at
	// 3000 rpm, chat fell to 54.5% that way while PolyServe held it at 100%.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	mine := &instanceFlux{
		meanStep: 200,
		live: []liveRequest{
			{tier: 25, allowanceMs: 50},
			{tier: 25, allowanceMs: 40},
		},
	}
	theirs := &instanceFlux{
		meanStep: 200,
		live: []liveRequest{
			{tier: 50, allowanceMs: 50},
			{tier: 50, allowanceMs: 40},
		},
	}
	// Every incumbent on both instances is already past its budget, so the
	// incumbent sum is zero on both and cannot separate them.
	same := p.harmToIncumbents(mine, 25, 200, 260)
	other := p.harmToIncumbents(theirs, 25, 200, 260)
	assert.Zero(t, same, "the instance this class already owns stays free")
	assert.Greater(t, other, same,
		"an instance held by another class is not free to break further")

	// An instance with nothing on it belongs to no class and is cheapest of all.
	empty := &instanceFlux{meanStep: 200}
	assert.Zero(t, p.harmToIncumbents(empty, 25, 200, 260))
}

// ---------------------------------------------------------------------------
// Bookkeeping
// ---------------------------------------------------------------------------

func TestTheInstanceViewIsRebuiltWheneverItCouldHaveChanged(t *testing.T) {
	// The view is reused between scheduling calls, which is what keeps the cost
	// of holding a request from growing with the retry rate. What it must never
	// do is reuse a view across a placement: two requests arriving between two
	// engine statuses would then each be judged against a state that does not
	// include the other, and the same capacity would be handed out twice.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	v := fsView(fsViewOpts{id: "a", decodeReqs: 4, decodeTokens: 40000,
		usedGpu: 30000, stepID: 5000})
	views := map[string]*instanceViewScheduling{"a": v}
	now := nowMillis()

	first := p.flux(v, now, 1)
	require.NotNil(t, first)
	assert.Same(t, first, p.flux(v, now, 1), "nothing changed, so nothing is rebuilt")

	// A placement changes what the instance holds even though the engine has
	// not reported anything new.
	require.NotNil(t, decide(p, fsRequest("r1", 50, 5000, 1000), views))
	after := p.flux(v, now, 1)
	assert.NotSame(t, first, after, "a placement has to invalidate the view")
	assert.Len(t, after.live, 1)

	// So does a new engine status.
	v.cmsView.Status.StepId = 5100
	assert.NotSame(t, after, p.flux(v, now, 1), "a new status has to invalidate it too")
}

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

func TestFractionalPrefillIsNotRoundedUpOverTheHorizon(t *testing.T) {
	// The quantity is how many of the next hundred iterations carry prefill work,
	// so a fractional answer is meaningful and rounding it up charges work that
	// will not happen. Measured at 4800 rpm the engines ran at 32-40 ms while the
	// model predicted 44.7-46.7 against a gate of 45, and the whole difference was
	// this rounding: the arriving prefill was 1.2-1.6 chunks and was charged as 2.
	//
	// Below one chunk the count stays at 1, because there the caller prices the
	// partial chunk itself; letting the count fall below 1 as well would discount
	// the same work twice.
	assert.InDelta(t, 1.0, prefillSteps(4096, 8192), 1e-9,
		"a partial chunk is one step; its cost is priced by the caller")
	assert.InDelta(t, 1.0, prefillSteps(8192, 8192), 1e-9)
	assert.InDelta(t, 1.401, prefillSteps(11478, 8192), 1e-3,
		"beyond one chunk the count carries the fraction rather than rounding up")
	assert.Zero(t, prefillSteps(0, 8192))

	// What that is worth on the predicted mean, at the numbers the run produced.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	const chunk, horizon = 8192.0, 100
	rounded := p.capacity.meanStepMs(400_000, 20, 2*chunk, chunk, horizon)
	actual := p.capacity.meanStepMs(400_000, 20, 11478, chunk, horizon)
	assert.Greater(t, rounded-actual, 2.0,
		"rounding 1.4 chunks up to 2 adds more than 2 ms to the predicted mean, "+
			"which is the margin the gate was being missed by")
}

func TestTheDeadlineReservesOneReDecisionInterval(t *testing.T) {
	// A held request is only looked at on the gateway's retry cadence, so a
	// deadline that leaves less margin than one interval can be stepped over
	// without anything ever evaluating it inside its budget. Measured, the fixed
	// margin was 300 ms against a 500 ms cadence: a chat request whose deadline
	// fell at 4,650 ms was asked at 4,500 and allowed to wait, then not asked
	// again until 5,000, by which time its 5,000 ms budget was gone.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	f := &instanceFlux{chunk: 8192, meanStep: 30}
	best := candidate{flux: f, prefillMs: 50, meanAfter: 30}

	mk := func(waited, recheck float64) *fluidserveRequest {
		return &fluidserveRequest{
			id: "chat", tier: 50, ttftSloMs: 5000, promptTokens: 666,
			arrivedMs: 0, nowMs: int64(waited), recheckMs: recheck,
			expectedToks: 400, nominalMs: 50,
		}
	}
	// Deadline without the reservation is 5000 - 50 - 300 = 4,650 ms.
	assert.True(t, p.canWait(best, mk(4500, 0)),
		"with no observed cadence the deadline is unchanged")
	// With a 500 ms cadence the same instant is past the point at which the
	// request can still be looked at before its budget runs out.
	assert.False(t, p.canWait(best, mk(4500, 500)),
		"one re-decision interval has to be reserved, or the deadline is passed "+
			"in the gap between two calls")
	// It is a reservation, not a cap: earlier in the budget the request still waits.
	assert.True(t, p.canWait(best, mk(3000, 500)))
}

func TestQueuedPrefillIsPricedAtItsOwnChunkSize(t *testing.T) {
	// The work already ahead of a request is counted in units of the engine's
	// chunk, so it has to be priced at the cost of a step carrying a chunk --
	// not at the cost of a step carrying THIS request's prompt. Charging 11,478
	// tokens of backlog at the price of a 666-token chat prefill understates the
	// wait by about eight times, which is what let a request be held to within
	// 350 ms of its budget and then miss.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	chat := &fluidserveRequest{id: "c", tier: 50, ttftSloMs: 5000,
		promptTokens: 666, expectedToks: 400, nominalMs: 50}

	clear := &instanceFlux{chunk: 8192}
	backed := &instanceFlux{chunk: 8192, effectivePrefill: 11478}

	own := p.prefillEstimateMs(chat, clear)
	withQueue := p.prefillEstimateMs(chat, backed)
	queued := withQueue - own

	// 11,478 tokens is 1.40 chunks; a chunk-carrying step costs t_pre(8192).
	// The exact figure depends on the profile, so assert the scale rather than
	// the value: it must be several hundred ms, not the ~84 ms the old pricing
	// produced from this request's own 666-token step cost.
	assert.Greater(t, queued, 400.0,
		"a backlog of one and a half chunks is several hundred ms of engine time")
	assert.Greater(t, queued, 4*own,
		"and it dominates this request's own prefill, which is one short prompt")

	// An instance with no backlog is unchanged, so light load keeps its behaviour.
	assert.InDelta(t, own, p.prefillEstimateMs(chat, &instanceFlux{chunk: 8192}),
		1e-9)
}

func TestTheBatchIsCountedTheSameWayWhenPredictingAndWhenMeasuring(t *testing.T) {
	// The duty cycle is (measured - decode-only prediction) / measured, so the
	// decode-only prediction has to be for the batch the engine is actually
	// running. It was not: the measurement counted one field where the
	// prediction summed five, so part of the decode cost was left unexplained
	// and attributed to prefill.
	//
	// Measured at 80 req/s that read a duty of 0.20 on instances reporting no
	// prefill at all, added 8.7 ms to a predicted iteration of 46.3 against an
	// observed 37.6, and put every instance above the 45.0 ms gate -- so nothing
	// was feasible, 84% of decisions became holds, and deep research burned 8.5 s
	// of its 10 s budget waiting for a fleet running at 21% KV.
	st := &cms.InstanceStatus{
		SchedulerRunningToDecodeTokensNum:         100_000,
		SchedulerRunningToDecodeRequestsNum:       50,
		SchedulerWaitingToDecodeTokensNum:         40_000,
		SchedulerWaitingToDecodeRequestsNum:       20,
		HybridSchedulerWaitingToDecodeTokensNum:   10_000,
		HybridSchedulerWaitingToDecodeRequestsNum: 5,
		NumTokensLoadingRequests:                  6_000,
		NumLoadingRequests:                        3,
	}
	view := &cms.InstanceView{
		InstanceStatusLocalAccount: cms.InstanceStatusLocalAccount{
			NumTokensInflightDispatchDecodeRequests: 4_000,
			NumInflightDispatchDecodeRequests:       2,
		},
	}
	kv, n := decodeBatchOf(st, view)
	assert.InDelta(t, 160_000.0, kv, 1e-9, "every field the engine reports counts")
	assert.InDelta(t, 80.0, n, 1e-9)

	// The running-only view is 62% of the KV and 63% of the batch here, and the
	// gap between the two is what used to be charged as prefill.
	runningOnly := float64(st.SchedulerRunningToDecodeTokensNum)
	assert.Less(t, runningOnly, kv)

	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	full := p.capacity.decodeStepMs(kv, n)
	partial := p.capacity.decodeStepMs(runningOnly,
		float64(st.SchedulerRunningToDecodeRequestsNum))
	// Asserted as a share of the prediction, not in milliseconds: the test
	// profile's constants are not the production ones, and what has to hold is
	// that the understatement is large enough to matter against a gate the
	// prediction sits within a few percent of. In production this was 8.7 ms on a
	// 46.3 ms prediction against a 45.0 ms gate.
	assert.Greater(t, (full-partial)/full, 0.05,
		"counting only the running batch understates the decode cost by enough to "+
			"decide feasibility")
}

func TestTheForcedPlacementTestUsesTheSameMarginAsRouting(t *testing.T) {
	// Two places compare the same predicted quantity -- the mean time to produce
	// one token on the instance after this request is placed there -- against the
	// same request's budget, and they used different thresholds. Routing required
	// the prediction to be within fsAllowanceUtilisation of the budget; the test
	// that decides between rejecting and forcing compared against the budget
	// itself. A request predicted to land between the two was refused a routed
	// placement and then given a forced one.
	//
	// Measured at 60 req/s, chat's realised mean inter-token latency had a median
	// of 49.9 ms against a 50 ms budget and 62% of admitted chat missed on it,
	// with the prediction accurate to within 0.1 ms of the engine's own
	// observation. The threshold, not the prediction, is what let those
	// placements through.
	chat := &fluidserveRequest{
		id: "chat", tier: 50, ttftSloMs: 5000, promptTokens: 666,
		expectedToks: 400, nominalMs: 50,
	}
	// 47.5 ms sits between 45.0 (50 x 0.90) and 50.0, which is the band the two
	// thresholds disagree about. prefillMs is small enough that the
	// time-to-first-token branch above cannot be what decides the outcome.
	between := candidate{prefillMs: 50, meanAfter: 47.5}

	off := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
		c.forceMargin = false
	})
	on := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
		c.forceMargin = true
	})

	assert.False(t, off.missesOwnBudget(chat, between),
		"without the flag a prediction under the raw budget is placed, which is "+
			"the shipped behaviour EXP-42 measures against")
	assert.True(t, on.missesOwnBudget(chat, between),
		"with the flag the same prediction is refused, because routing would "+
			"also have refused it")

	// Either side of the band both settings have to agree, or the change is
	// doing something other than closing the gap between the two thresholds.
	comfortable := candidate{prefillMs: 50, meanAfter: 40}
	assert.False(t, off.missesOwnBudget(chat, comfortable))
	assert.False(t, on.missesOwnBudget(chat, comfortable))

	hopeless := candidate{prefillMs: 50, meanAfter: 60}
	assert.True(t, off.missesOwnBudget(chat, hopeless))
	assert.True(t, on.missesOwnBudget(chat, hopeless))

	// The margin applies to the pace comparison only. A request judged end to
	// end goes down a different branch, and leaving that branch alone is what
	// keeps EXP-42's result attributable to one change.
	swe := &fluidserveRequest{
		id: "swe", tier: 25, ttftSloMs: 11800, promptTokens: 6812,
		expectedToks: 500, nominalMs: 57, isE2E: true, budgetMs: 30000,
	}
	e2e := candidate{prefillMs: 400, meanAfter: 47.5}
	assert.Equal(t, off.missesOwnBudget(swe, e2e), on.missesOwnBudget(swe, e2e),
		"the end-to-end branch is untouched by this flag")
}

func TestTheGateCanBeTheRequestsOwnBudgetRatherThanTheInstanceMinimum(t *testing.T) {
	// Candidate C. Two things are protected in the feasibility test and one of
	// them is protected twice. The arriving request must be able to run at the
	// pace its class was promised; the incumbents must not be pushed past what
	// they can still meet. The second is what tightestAllowance does, using each
	// incumbent's REMAINING budget. gateAllowance protects them again using
	// their nominal budgets, and being a minimum over every class present it
	// becomes chat's 50 ms on every instance within seconds of a run starting.
	//
	// The state below is the one measured over minutes 50-56 of EXP-41's full
	// trace: every instance reporting a gate of 50.0 while delivering 55.6 and
	// holding incumbents whose remaining allowance is 68.3.
	f := &instanceFlux{
		id: "i1", chunk: 8192, meanStep: 55.6,
		gateAllowance: 50.0, tightestAllowance: 68.3,
		capKv: 4e6, capMem: 4e6, kvLogical: 1e5, nDecode: 200,
	}
	deep := &fluidserveRequest{
		id: "dr", tier: 100, ttftSloMs: 10000, promptTokens: 4639,
		expectedToks: 840, nominalMs: 100,
	}
	chat := &fluidserveRequest{
		id: "c", tier: 50, ttftSloMs: 5000, promptTokens: 666,
		expectedToks: 400, nominalMs: 50,
	}

	off := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", func(c *fluidserveConfig) {
		c.ownBudgetGate = false
	})
	on := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", func(c *fluidserveConfig) {
		c.ownBudgetGate = true
	})

	// The gate itself, which is what the change touches.
	assert.InDelta(t, 45.0, off.evaluate(f, nil, deep).gateAfter, 0.01,
		"without the flag the instance minimum wins and even a 100 ms class is "+
			"held to chat's 50 ms")
	assert.InDelta(t, 90.0, on.evaluate(f, nil, deep).gateAfter, 0.01,
		"with the flag the request is judged on its own 100 ms budget")

	// Chat is unaffected either way: its own budget IS the instance minimum
	// here, so the change cannot let chat onto an instance that is too slow
	// for it. That is the property that makes this a deletion of redundancy
	// rather than a loosening.
	assert.InDelta(t, off.evaluate(f, nil, chat).gateAfter,
		on.evaluate(f, nil, chat).gateAfter, 0.01)
	assert.InDelta(t, 45.0, on.evaluate(f, nil, chat).gateAfter, 0.01)
}

func TestTheProjectionCanBeTheOccupancyTheEngineIsObservedMovingAt(t *testing.T) {
	// Candidate H2. The modelled balance is `current occupancy + the resident
	// set's growth over the horizon - what completions are expected to release`.
	// Occupancy one horizon ahead has a fourth term the model does not carry:
	// the requests the scheduler places during that same horizon. Measured on
	// the hour-long trace, that omission plus an over-predicted release term
	// left the projection below the occupancy the engine actually reported one
	// horizon later in 88.5% of 14,676 paired samples.
	//
	// The alternative projects from the rate the occupancy is observed to be
	// moving at, which contains all four terms because it is the difference of
	// two numbers the engine reported. Two properties are asserted, and the
	// second is why the fleet-wide offered-rate projection was removed in v19:
	// the value has to be per instance, so that an instance filling up is
	// distinguished from one that is not.
	p := fsPolicy(t, "25:e2e:16000,50:decode", func(c *fluidserveConfig) {
		c.kvSlopeProjection = true
	})
	const startKv = 400_000
	views := map[string]*instanceViewScheduling{
		"filling": fsView(fsViewOpts{id: "filling", decodeReqs: 20,
			decodeTokens: startKv, usedGpu: 120_000, stepID: 5000}),
		"steady": fsView(fsViewOpts{id: "steady", decodeReqs: 20,
			decodeTokens: startKv, usedGpu: 120_000, stepID: 5000}),
	}
	probe := fsRequest("probe", 50, 5000, 1000)
	now := nowMillis()
	for id, v := range views {
		fill(p, id, 50, 1000, 20, 4990, now)
		v.cmsView.Status.TimestampMs = now
	}

	// First status. No interval has elapsed, so no rate has been measured and
	// the projection is the occupancy itself -- the permissive direction, and
	// the first measurement arrives one status interval later.
	p.calculateMetrics(consts.InferTypeNeutral, probe, views)
	first := views["filling"].schedulingCtx.fluidserveFlux
	require.NotNil(t, first)
	assert.Equal(t, float64(startKv), first.proj,
		"nothing measured yet, so nothing is projected")

	// Second status, 500 ms later. Both engines ran the same iterations; one
	// gained 50,000 logical KV tokens over the interval and the other did not.
	decodeOnly := p.capacity.decodeStepMs(startKv, 20)
	const steps = 100
	for id, v := range views {
		v.cmsView.Status.StepId = 5100
		v.cmsView.Status.TimestampMs = now + int64(decodeOnly*steps)
		if id == "filling" {
			// Physical occupancy is raised in the same proportion. The memory
			// capacity is expressed in logical tokens by dividing through the
			// measured physical-to-logical ratio, so raising only the logical
			// count would move the limit as well as the projection and the two
			// instances would no longer differ in one thing.
			v.cmsView.Status.SchedulerRunningToDecodeTokensNum = startKv + 50_000
			v.cmsView.Status.NumUsedGpuTokens = 135_000
		}
	}
	p.calculateMetrics(consts.InferTypeNeutral, probe, views)
	filling := views["filling"].schedulingCtx.fluidserveFlux
	steady := views["steady"].schedulingCtx.fluidserveFlux
	require.NotNil(t, filling)
	require.NotNil(t, steady)

	assert.Greater(t, filling.proj, filling.kvLogical,
		"an instance whose occupancy is rising is projected above where it is now")
	assert.Equal(t, steady.kvLogical, steady.proj,
		"and one whose occupancy is not moving is projected where it is")
	assert.Less(t, filling.headroom, steady.headroom,
		"so the two instances are ordered by which of them is filling, which a "+
			"fleet-wide projection cannot do")
}

// ---------------------------------------------------------------------------
// The class preference as a degree rather than a switch (EXP-58)

// sortCandidates orders the feasible instances by w*share + (1-w)*room. The two
// endpoints have to reproduce behaviour that has already been measured, because
// the sweep between them is only interpretable if its ends coincide with the two
// arms of EXP-56: at w=1 the arm called `fluidserve` in every experiment up to
// EXP-56, and at w=0 the arm called `fsnoaff`.
func TestAffinityWeightSpansTheTwoArmsAlreadyMeasured(t *testing.T) {
	// One instance holds this class and is nearly full; the other holds none of
	// it and is nearly empty. The two orderings disagree, which is what makes
	// this pair able to tell them apart.
	mine := candidate{flux: &instanceFlux{id: "mine"}, feasible: true, share: 1.0, room: 0.10}
	other := candidate{flux: &instanceFlux{id: "other"}, feasible: true, share: 0.0, room: 0.90}

	for _, tc := range []struct {
		w    float64
		want string
		why  string
	}{
		{1.0, "mine", "at full strength the class preference decides outright"},
		{0.0, "other", "at zero the ordering is by free space alone"},
		// The crossing point: 1.0*w + 0.10*(1-w) against 0.0*w + 0.90*(1-w) is an
		// equality at w = 0.8/1.8 = 0.444..., so 0.4 still prefers space and 0.5
		// already prefers the class.
		{0.40, "other", "below the crossing point free space still wins"},
		{0.50, "mine", "above it the class preference wins"},
	} {
		c := []candidate{other, mine}
		sortCandidates(c, tc.w)
		assert.Equal(t, tc.want, c[0].flux.id, "w=%.2f: %s", tc.w, tc.why)
	}
}

// The order within the feasible set is not the only place the preference acts;
// harmToIncumbents charges an instance for the share of it that belongs to other
// classes. That term has to move on the same scale, or w would mean one thing in
// one place and another thing in the other, and w=0 would not reproduce
// --fluidserve-enable-affinity=false.
func TestAffinityWeightScalesTheClassTermInHarmToo(t *testing.T) {
	theirs := &instanceFlux{
		meanStep: 200,
		live: []liveRequest{
			{tier: 50, allowanceMs: 50}, // both already past budget, so the
			{tier: 50, allowanceMs: 40}, // incumbent sum contributes nothing
		},
	}
	full := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	half := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
		c.affinityWeight = 0.5
	})
	none := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
		c.affinityWeight = 0
	})
	off := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
		c.enableAffinity = false
	})

	hFull := full.harmToIncumbents(theirs, 25, 200, 260)
	hHalf := half.harmToIncumbents(theirs, 25, 200, 260)
	assert.InDelta(t, hFull/2, hHalf, 1e-9, "the class term scales linearly in w")
	assert.Zero(t, none.harmToIncumbents(theirs, 25, 200, 260))
	assert.Equal(t, none.harmToIncumbents(theirs, 25, 200, 260),
		off.harmToIncumbents(theirs, 25, 200, 260),
		"w=0 and the switch being off are the same configuration")
}

// The switch stays the master: whatever weight is configured, turning the
// preference off means off. Out-of-range values are clamped rather than
// rejected, because a weight above 1 would make the score fall as an instance
// gains free space, which is not a stronger preference but a different and
// meaningless ordering.
func TestAffinityWeightIsClampedAndTheSwitchWins(t *testing.T) {
	for _, tc := range []struct {
		set  float64
		on   bool
		want float64
	}{
		{1.0, true, 1.0}, {0.3, true, 0.3}, {0.0, true, 0.0},
		{1.7, true, 1.0}, {-0.2, true, 0.0}, {1.0, false, 0.0},
	} {
		p := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
			c.affinityWeight = tc.set
			c.enableAffinity = tc.on
		})
		assert.InDelta(t, tc.want, p.affinityWeight(), 1e-9,
			"configured %.2f with the switch %v", tc.set, tc.on)
	}
}

// ---------------------------------------------------------------------------
// Pinning a class to a fixed set of instances (EXP-59)

func TestParseClassPinReadsTheMapAndRefusesMalformedInput(t *testing.T) {
	got, err := parseClassPin(" 50:0 ; 100:1,2 ; 25:3 ")
	require.NoError(t, err)
	assert.Equal(t, map[int][]int{50: {0}, 100: {1, 2}, 25: {3}}, got)

	empty, err := parseClassPin("")
	require.NoError(t, err)
	assert.Nil(t, empty, "the empty string is no pinning, which is the default")

	// Each of these would otherwise become "no pinning", which is the control
	// arm of the experiment this flag exists for.
	for _, bad := range []string{"50", "x:0", "50:x", "50:-1", "50:", "50:0;50:1"} {
		_, err := parseClassPin(bad)
		assert.Error(t, err, "input %q must not be accepted", bad)
	}
}

func TestClassPinKeepsOnlyTheInstancesAClassIsAssignedTo(t *testing.T) {
	// Four instances, and the tier-50 class is assigned the second and third in
	// the sorted order of their ids. Positions are used rather than names so the
	// configuration survives a pod being recreated under a new name.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", func(c *fluidserveConfig) {
		c.classPin = map[int][]int{50: {1, 2}}
	})
	views := map[string]*instanceViewScheduling{}
	cands := []candidate{}
	for _, id := range []string{"a", "b", "c", "d"} {
		views[id] = fsView(fsViewOpts{id: id, decodeReqs: 5, decodeTokens: 100_000,
			usedGpu: 20_000, stepID: 5000})
		cands = append(cands, candidate{flux: &instanceFlux{id: id}, feasible: true})
	}

	kept := p.applyClassPin(cands, views, &fluidserveRequest{tier: 50})
	ids := []string{}
	for _, c := range kept {
		ids = append(ids, c.flux.id)
	}
	assert.Equal(t, []string{"b", "c"}, ids)

	// A class with no entry in the map is unrestricted, so one class can be
	// pinned while the others are left alone.
	assert.Len(t, p.applyClassPin(cands, views, &fluidserveRequest{tier: 100}), 4)

	// And with no map at all nothing is filtered, which is every experiment
	// before this flag existed.
	off := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	assert.Len(t, off.applyClassPin(cands, views, &fluidserveRequest{tier: 50}), 4)
}

func TestClassPinDoesNotSilentlyEmptyTheCandidateSet(t *testing.T) {
	// The configuration names an instance that is not in the fleet. Returning an
	// empty list would make the request unplaceable for a reason that has
	// nothing to do with capacity, so the unfiltered set is returned and the
	// scheduler logs it.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", func(c *fluidserveConfig) {
		c.classPin = map[int][]int{50: {7}}
	})
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 5, decodeTokens: 100_000,
			usedGpu: 20_000, stepID: 5000}),
	}
	cands := []candidate{{flux: &instanceFlux{id: "a"}, feasible: true}}
	assert.Len(t, p.applyClassPin(cands, views, &fluidserveRequest{tier: 50}), 1)
}
