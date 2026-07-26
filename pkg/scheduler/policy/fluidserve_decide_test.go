package policy

import (
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
		horizonSteps:      100,
		zSafety:           1.65,
		alphaExternality:  1.0,
		enablePend:        true,
		enableExternality: true,
		enableFlux:        true,
		pendGraceMs:       200,
		ttftSafetyMs:      300,
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

func TestRoutesToTheInstanceWithMoreHeadroom(t *testing.T) {
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
	assert.Zero(t, p2.evaluate(loose.schedulingCtx.fluidserveFlux, loose, req).externality,
		"the ablation must charge nothing for binding a loose instance to a tight budget")
	assert.Greater(t, withTerm.evaluate(loose.schedulingCtx.fluidserveFlux, loose, req).externality,
		0.0, "with the term on, that same placement is charged")
}

func TestRequestIsHeldWhenNothingCanTakeIt(t *testing.T) {
	// Both instances are far past what they can hold, so there is no placement
	// that meets the budget. Holding the request at the gateway costs nothing
	// and keeps every option open, which is preferable to committing it to an
	// engine whose queue it can no longer be taken out of.
	p := fsPolicy(t, "25:e2e:16000,50:decode", nil)
	views := map[string]*instanceViewScheduling{
		"a": fsView(fsViewOpts{id: "a", decodeReqs: 300, decodeTokens: 5000000,
			pendingPre: 200000, usedGpu: 570000, stepID: 9000}),
		"b": fsView(fsViewOpts{id: "b", decodeReqs: 300, decodeTokens: 5000000,
			pendingPre: 200000, usedGpu: 570000, stepID: 9000}),
	}
	got := decide(p, fsRequest("r1", 50, 5000, 1000), views)
	assert.Nil(t, got, "an unplaceable request should wait, not be committed")
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
