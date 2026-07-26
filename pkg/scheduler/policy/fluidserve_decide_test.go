package policy

import (
	"fmt"
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"llumnix/pkg/consts"
	"llumnix/pkg/types"
)

// Tests for the decision itself: which instance a request goes to, when it is
// held at the gateway instead, and when holding stops being an option.

func fsPolicy(t *testing.T, budgets string, mutate func(*fluidserveConfig)) *fluidserveDispatchPolicy {
	t.Helper()
	b, err := parseClassBudgets(budgets)
	require.NoError(t, err)

	cfg := fluidserveConfig{
		horizonSteps:            100,
		zSafety:                 1.65,
		alphaExternality:        1.0,
		enablePend:              true,
		enableExternality:       true,
		enableFlux:              true,
		enableOnlineCalibration: true,
		pendGraceMs:             200,
		ttftSafetyMs:            300,
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
	}
	p.tuner = newSafetyTuner(&p.cfg)
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

func TestRoutesToTheInstanceWithMoreRoomWithinItsOwnBudgetGroup(t *testing.T) {
	// Within a group of instances held to the same budget, free space decides,
	// and more of it is better: a fuller instance absorbs a burst less well and
	// queues longer.
	//
	// Packing into the fullest instead was tried and measured worse (35.7
	// against 43.2 equal-weight, with the routing concentration of every class
	// falling). The reason is that the fullest instance is usually the one
	// holding the heavy class, so packing pulled the interactive class onto it
	// and undid the separation. Which budget an instance is held to is now
	// decided before free space rather than weighed against it.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	views := map[string]*instanceViewScheduling{
		"busy": fsView(fsViewOpts{id: "busy", decodeReqs: 60, decodeTokens: 900000,
			usedGpu: 400000, stepID: 5000}),
		"quiet": fsView(fsViewOpts{id: "quiet", decodeReqs: 4, decodeTokens: 40000,
			usedGpu: 30000, stepID: 5000}),
	}
	got := decide(p, fsRequest("r1", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "quiet", got.GetInstanceId())
}

func TestFallsBackToMostRoomWhenNothingFits(t *testing.T) {
	// Once no instance can take the request within its budget the question is
	// no longer where it fits but where it does least damage, so the preference
	// inverts back to the emptiest.
	p := fsPolicy(t, "25:e2e:16000,50:decode", func(c *fluidserveConfig) {
		c.enablePend = false
	})
	views := map[string]*instanceViewScheduling{
		"full": fsView(fsViewOpts{id: "full", decodeReqs: 300,
			decodeTokens: 6_000_000, pendingPre: 300_000, usedGpu: 570_000,
			stepID: 9000}),
		"lessfull": fsView(fsViewOpts{id: "lessfull", decodeReqs: 200,
			decodeTokens: 4_000_000, pendingPre: 300_000, usedGpu: 500_000,
			stepID: 9000}),
	}
	got := decide(p, fsRequest("r1", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "lessfull", got.GetInstanceId())
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

func TestTightRequestsCollectWhereTightRequestsAlreadyAre(t *testing.T) {
	// The externality term at work. Two instances are equally loaded, but one
	// already serves a request with a tight budget and is therefore already
	// held to a low capacity. Sending another tight request there costs nothing
	// further, whereas sending it to the other instance would bind that one to
	// the tight budget too and remove capacity it could otherwise have used for
	// the looser class it is serving.
	//
	// No instance is reserved for a class in advance; the separation is the
	// outcome of pricing that loss.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	now := nowMillis()

	views := map[string]*instanceViewScheduling{
		"hasTight": fsView(fsViewOpts{id: "hasTight", decodeReqs: 8, decodeTokens: 80000,
			usedGpu: 60000, stepID: 5000}),
		"looseOnly": fsView(fsViewOpts{id: "looseOnly", decodeReqs: 8, decodeTokens: 80000,
			usedGpu: 60000, stepID: 5000}),
	}
	// One tight request already running on "hasTight", one loose request on
	// "looseOnly", both just started.
	p.registry.noteArrival("tight-old", now)
	p.registry.onDispatch("hasTight", "tight-old", 25, 20000, 8192, 4990, now)
	p.registry.noteArrival("loose-old", now)
	p.registry.onDispatch("looseOnly", "loose-old", 50, 1000, 8192, 4990, now)

	got := decide(p, fsRequest("tight-new", 25, 11800, 8192), views)
	require.NotNil(t, got)
	assert.Equal(t, "hasTight", got.GetInstanceId())

	// With the externality term switched off the two instances are
	// indistinguishable on headroom alone, so the tight request is free to land
	// on the loose instance and drag its capacity down. This is the ablation
	// that isolates what the term buys.
	p2 := fsPolicy(t, "25:e2e:16000,50:decode", func(c *fluidserveConfig) {
		c.enableExternality = false
	})
	views2 := map[string]*instanceViewScheduling{
		"hasTight": fsView(fsViewOpts{id: "hasTight", decodeReqs: 8, decodeTokens: 80000,
			usedGpu: 60000, stepID: 5000}),
		"looseOnly": fsView(fsViewOpts{id: "looseOnly", decodeReqs: 8, decodeTokens: 80000,
			usedGpu: 60000, stepID: 5000}),
	}
	p2.registry.noteArrival("tight-old", now)
	p2.registry.onDispatch("hasTight", "tight-old", 25, 20000, 8192, 4990, now)
	p2.registry.noteArrival("loose-old", now)
	p2.registry.onDispatch("looseOnly", "loose-old", 50, 1000, 8192, 4990, now)

	p2.calculateMetrics(consts.InferTypeNeutral,
		fsRequest("tight-new", 25, 11800, 8192), views2)
	req := views2["looseOnly"].schedulingCtx.fluidserveRequest
	require.NotNil(t, req)
	withTerm := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	loose := views2["looseOnly"]
	off := p2.evaluate(loose.schedulingCtx.fluidserveFlux, loose, req)
	assert.Zero(t, off.harm+off.mismatch,
		"the ablation must charge nothing for placing a request on this instance")
	on := withTerm.evaluate(loose.schedulingCtx.fluidserveFlux, loose, req)
	assert.Greater(t, on.harm+on.mismatch, 0.0,
		"with the terms on, that same placement is charged")
}

func TestRequestIsHeldWhenPlacingItWouldBreakOthers(t *testing.T) {
	// Holding is worth doing when the reason not to place the request is that
	// doing so damages requests that can still make their budget. Holding costs
	// nothing and keeps every option open, whereas committing it to an engine
	// puts it in a queue it cannot be taken out of and takes the incumbents
	// down with it.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 30, decodeTokens: 2_400_000,
			usedGpu: 300_000, stepID: 9000}),
		"b": fsView(fsViewOpts{id: "b", decodeReqs: 30, decodeTokens: 2_400_000,
			usedGpu: 300_000, stepID: 9000}),
	}
	for _, inst := range []string{"a", "b"} {
		for i := 0; i < 30; i++ {
			id := fmt.Sprintf("%s-chat-%d", inst, i)
			p.registry.noteArrival(id, now)
			p.registry.onDispatch(inst, id, 50, 1000, 8192, 8990, now)
		}
	}
	// A long prompt would make several of the next iterations carry a chunk,
	// which is what the incumbents cannot absorb.
	got := decide(p, fsRequest("heavy", 25, 11800, 40000), views)
	assert.Nil(t, got, "placing this would break requests that can still make it")
}

func TestRequestIsPlacedWhenWaitingCannotHelp(t *testing.T) {
	// The complement, and the reason the rule is not simply "hold whenever the
	// request does not fit". If nothing on the instance can still make its
	// budget, holding does not protect anyone and only spends the held
	// request's own time-to-first-token budget.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5_000_000,
			pendingPre: 200_000, usedGpu: 570_000, stepID: 9000}),
	}
	got := decide(p, fsRequest("r1", 50, 5000, 1000), views)
	require.NotNil(t, got)
}

func TestHoldingStopsOnceTheFirstTokenBudgetIsSpent(t *testing.T) {
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5000000,
			pendingPre: 200000, usedGpu: 570000, stepID: 9000}),
	}
	// Pre-register the arrival far enough in the past that the time-to-first-
	// token budget no longer leaves room for the prefill this request needs.
	// Continuing to hold it would turn a request that could still be served
	// late into one certain to miss, so it is placed on the least bad instance.
	p.registry.noteArrival("late", nowMillis()-4900)
	got := decide(p, fsRequest("late", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "a", got.GetInstanceId())
}

func TestPendCanBeDisabledForAblation(t *testing.T) {
	p := fsPolicy(t, "25:e2e:16000,50:decode", func(c *fluidserveConfig) {
		c.enablePend = false
	})
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5000000,
			pendingPre: 200000, usedGpu: 570000, stepID: 9000}),
	}
	got := decide(p, fsRequest("r1", 50, 5000, 1000), views)
	require.NotNil(t, got, "with holding disabled the request must be placed immediately")
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

func TestUnachievableRequestsDoNotPinInstanceCapacity(t *testing.T) {
	// A request whose remaining budget per token has fallen below the cost of
	// an iteration on an empty instance cannot be met by any placement. If it
	// still set the instance's capacity, that instance would stop accepting
	// work without the doomed request being any better off, so it is excluded
	// from the constraint and served on a best-effort basis.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 2, decodeTokens: 20000,
			usedGpu: 15000, stepID: 5000}),
	}
	// An end-to-end request that has already spent nearly its whole budget.
	p.registry.noteArrival("doomed", now-11900)
	p.registry.onDispatch("a", "doomed", 25, 20000, 8192, 4900, now-11900)

	p.calculateMetrics(consts.InferTypeNeutral, fsRequest("probe", 50, 5000, 1000), views)
	f := views["a"].schedulingCtx.fluidserveFlux
	require.NotNil(t, f)
	assert.Equal(t, 1, f.unachievable)
	assert.Greater(t, f.headroom, 0.0,
		"the instance should still be usable for requests that can be met")
}

func TestIterationTimeIsMeasuredAcrossStatuses(t *testing.T) {
	// The engine's own single-step duration field is published only for steps
	// that carried a prefill chunk, and only while a bounded profiling budget
	// lasts, so it cannot be used to calibrate decode cost. The step counter and
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
	// interval, which would let one measurement dominate the calibration.
	assert.Equal(t, -1.0, p.observeInstance(v))

	// A pair straddling an engine restart or a long stall describes neither
	// interval and is discarded.
	v.cmsView.Status.StepId = 1_000_050
	v.cmsView.Status.TimestampMs = 1_002_000
	assert.Equal(t, -1.0, p.observeInstance(v))
}

func TestCalibrationIgnoresIntervalsContainingPrefill(t *testing.T) {
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	before := p.capacity.correctionFactor()

	v := fsView(fsViewOpts{id: "a", decodeReqs: 10, decodeTokens: 100000,
		pendingPre: 20000, usedGpu: 80000, stepID: 1000})
	v.cmsView.Status.TimestampMs = 1_000_000
	p.observeInstance(v)
	for i := 1; i <= 200; i++ {
		v.cmsView.Status.StepId = int32(1000 + 10*i)
		v.cmsView.Status.TimestampMs = int64(1_000_000 + 3000*i)
		p.observeInstance(v)
	}
	assert.InDelta(t, before, p.capacity.correctionFactor(), 1e-9,
		"an interval that carried prefill work must not be charged to the decode law")
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

func TestHeavyWorkAvoidsInstancesWhoseRequestsHaveLittleSlack(t *testing.T) {
	// The property the earlier formulation lacked, and the reason the first
	// measured run spread the damage evenly across classes instead of confining
	// it. An agent request carries a 22k-token prompt, which makes several of
	// the next iterations carry a prefill chunk and delays every request already
	// decoding on that instance. Its own per-token budget is LOOSER than an
	// interactive request's, so a rule that prices only the budget a request
	// declares charges it nothing for landing among interactive requests and
	// pushing all of them past their limit.
	p := fsPolicy(t, "25:e2e:30000,50:decode", nil)
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
	for i := 0; i < 20; i++ {
		id := fmt.Sprintf("chat-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("interactive", id, 50, 1000, 8192, 4990, now)
		id = fmt.Sprintf("swe-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("loaded", id, 25, 20000, 8192, 4990, now)
	}

	got := decide(p, fsRequest("swe-new", 25, 11800, 22000), views)
	require.NotNil(t, got)
	assert.Equal(t, "loaded", got.GetInstanceId(),
		"heavy work belongs where it can no longer make anything worse")
}

func TestRequestsAlreadyPastTheirBudgetDoNotMakeAnInstanceExpensive(t *testing.T) {
	// The asymmetry that produces the separation: an instance whose requests
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

func TestBudgetMismatchIsSymmetricAndZeroOnAnEmptyInstance(t *testing.T) {
	// An instance with nothing on it has no constraint to mismatch against, so
	// it is free to take whatever arrives first and become the home for that
	// budget. That is how the separation starts from a cold fleet.
	assert.Zero(t, budgetMismatch(math.Inf(1), 50))
	assert.Zero(t, budgetMismatch(50, 50))
	// Mixing budgets wastes capacity in both directions, so the penalty does
	// not depend on which way round the pair is.
	assert.InDelta(t, budgetMismatch(50, 100), budgetMismatch(100, 50), 1e-12)
	// And it grows with how far apart they are.
	assert.Greater(t, budgetMismatch(50, 100), budgetMismatch(50, 60))
}

func TestRequestsPreferInstancesHoldingTheirOwnBudget(t *testing.T) {
	// Two instances with the same free space, one already serving interactive
	// requests and one already serving batch requests. A batch request should
	// take the batch instance even though neither is fuller than the other,
	// because an instance's admissible occupancy is set by the tightest budget
	// on it and mixing the two wastes capacity on both.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"interactive": fsView(fsViewOpts{id: "interactive", decodeReqs: 5,
			decodeTokens: 200_000, usedGpu: 40_000, stepID: 5000}),
		"batch": fsView(fsViewOpts{id: "batch", decodeReqs: 5,
			decodeTokens: 200_000, usedGpu: 40_000, stepID: 5000}),
	}
	for i := 0; i < 5; i++ {
		id := fmt.Sprintf("chat-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("interactive", id, 50, 1000, 8192, 4990, now)
		id = fmt.Sprintf("dr-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("batch", id, 100, 4000, 8192, 4990, now)
	}
	got := decide(p, fsRequest("dr-new", 100, 10000, 4000), views)
	require.NotNil(t, got)
	assert.Equal(t, "batch", got.GetInstanceId())

	got = decide(p, fsRequest("chat-new", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "interactive", got.GetInstanceId())
}

func TestInstancePreferenceSurvivesDraining(t *testing.T) {
	// The budget in force on an instance is the minimum over the requests
	// present right now, so it disappears the moment the instance drains and
	// reappears as whatever arrives next. Measured, that made the assignment
	// churn: an instance that emptied attracted whichever class asked first and
	// no class settled anywhere. The preference is smoothed over dispatches and
	// therefore survives the gap.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()

	assert.Zero(t, p.registry.preferredBudgetMs("a"),
		"an instance that has served nothing has no preference")

	for i := 0; i < 200; i++ {
		id := fmt.Sprintf("chat-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("a", id, 50, 1000, 8192, int64(5000+i), now)
	}
	assert.InDelta(t, 50.0, p.registry.preferredBudgetMs("a"), 6.0)

	// Draining the instance does not erase what it has been serving.
	p.registry.reconcile("a", 0, 99999, now+1000)
	assert.InDelta(t, 50.0, p.registry.preferredBudgetMs("a"), 6.0)

	// A handful of requests of another class does not move it either.
	for i := 0; i < 5; i++ {
		id := fmt.Sprintf("dr-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("a", id, 100, 4000, 8192, 99999, now)
	}
	assert.Less(t, p.registry.preferredBudgetMs("a"), 60.0)

	// Sustained traffic of another class does.
	for i := 0; i < 400; i++ {
		id := fmt.Sprintf("dr-more-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("a", id, 100, 4000, 8192, 99999, now)
	}
	assert.InDelta(t, 100.0, p.registry.preferredBudgetMs("a"), 12.0)
}

func TestDrainedInstanceStillAttractsItsOwnClass(t *testing.T) {
	// The behaviour the preference exists for: two instances with identical
	// free space, one of which has been serving the interactive class and has
	// just emptied. An interactive request belongs there rather than on the one
	// that has been serving the batch class.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()
	for i := 0; i < 200; i++ {
		id := fmt.Sprintf("chat-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("wasChat", id, 50, 1000, 8192, int64(5000+i), now)
		id = fmt.Sprintf("dr-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("wasBatch", id, 100, 4000, 8192, int64(5000+i), now)
	}
	views := map[string]*instanceViewScheduling{
		"wasChat":  fsView(fsViewOpts{id: "wasChat", stepID: 99999}),
		"wasBatch": fsView(fsViewOpts{id: "wasBatch", stepID: 99999}),
	}
	got := decide(p, fsRequest("chat-new", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "wasChat", got.GetInstanceId())
}

func TestClassShareBreaksTheUniformMixture(t *testing.T) {
	// Every other term is symmetric in the instances, so a fleet where all of
	// them hold the same mixture is a fixed point: each looks identical to
	// every request and nothing pushes any class anywhere. Seven measured
	// configurations sat at exactly that point. This term is asymmetric on
	// purpose -- an instance already holding more of a class is more attractive
	// to it -- which makes the uniform mixture unstable.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"mostlyChat": fsView(fsViewOpts{id: "mostlyChat", decodeReqs: 10,
			decodeTokens: 200_000, usedGpu: 40_000, stepID: 5000}),
		"mixed": fsView(fsViewOpts{id: "mixed", decodeReqs: 10,
			decodeTokens: 200_000, usedGpu: 40_000, stepID: 5000}),
	}
	for i := 0; i < 10; i++ {
		id := fmt.Sprintf("a-%d", i)
		p.registry.noteArrival(id, now)
		p.registry.onDispatch("mostlyChat", id, 50, 1000, 8192, 4990, now)
		id = fmt.Sprintf("b-%d", i)
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

func TestClassShareIsZeroOnAnEmptyInstance(t *testing.T) {
	// An instance holding nothing belongs to no class, so it neither attracts
	// nor repels; the other terms decide, which is what lets a fresh instance
	// be claimed at all.
	assert.Zero(t, classShare(&instanceFlux{}, 50))
	f := &instanceFlux{live: []liveRequest{{tier: 50}, {tier: 50}, {tier: 25}}}
	assert.InDelta(t, 2.0/3.0, classShare(f, 50), 1e-9)
	assert.InDelta(t, 1.0/3.0, classShare(f, 25), 1e-9)
	assert.Zero(t, classShare(f, 100))
}

func TestACollapsedInstanceIsNeverChosen(t *testing.T) {
	// Two terms go quiet on an instance whose requests have already lost their
	// budget: nothing is harmed by delaying them further, and an instance
	// holding one class attracts more of it. If the free-space term saturates
	// as well, such an instance looks no worse than a healthy one and keeps
	// taking work. Measured on the dynamic trace, one instance reached a
	// projected 69 million KV tokens against a capacity of 1.9 million and was
	// running two-second iterations while still being selected.
	p := fsPolicy(t, "25:e2e:30000,50:decode,100:decode", nil)
	now := nowMillis()
	views := map[string]*instanceViewScheduling{
		"collapsed": fsView(fsViewOpts{id: "collapsed", decodeReqs: 200,
			decodeTokens: 60_000_000, pendingPre: 500_000, usedGpu: 570_000,
			stepID: 9000}),
		"healthy": fsView(fsViewOpts{id: "healthy", decodeReqs: 5,
			decodeTokens: 100_000, usedGpu: 20_000, stepID: 9000}),
	}
	// Give the collapsed instance the whole of the class being placed, so the
	// share term is pulling as hard as it can toward it.
	for i := 0; i < 200; i++ {
		id := fmt.Sprintf("chat-%d", i)
		p.registry.noteArrival(id, now-60_000)
		p.registry.onDispatch("collapsed", id, 50, 1000, 8192, 8000, now-60_000)
	}
	got := decide(p, fsRequest("chat-new", 50, 5000, 1000), views)
	require.NotNil(t, got)
	assert.Equal(t, "healthy", got.GetInstanceId())
}
