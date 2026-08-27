package policy

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"

	"llumnix/pkg/consts"
)

// ladderPolicy builds the policy struct directly. The selector is exercised on
// its own, so nothing here needs a profiling table, a live CMS or the metric
// factories.
func ladderPolicy(cfg polyserveConfig) *polyserveDispatchPolicy {
	if cfg.globalTtftSloMs == 0 {
		cfg.globalTtftSloMs = 6000
	}
	if cfg.globalTpotSloMs == 0 {
		cfg.globalTpotSloMs = 50
	}
	if cfg.ttftMultiplier == 0 {
		cfg.ttftMultiplier = 1.0
	}
	if cfg.tpotMultiplier == 0 {
		cfg.tpotMultiplier = 1.0
	}
	return &polyserveDispatchPolicy{
		tierPartition: newTierPartition(),
		fleet:         newPolyserveFleet(),
		waitLog:       newPolyserveWaitLog(),
		rejected:      map[string]struct{}{},
		cfg:           cfg,
	}
}

// withRequest attaches the per-request context the selector reads, the way
// calculateMetrics does on the live path.
func withRequest(
	views map[string]*instanceViewScheduling, req *polyserveRequest,
) map[string]*instanceViewScheduling {
	for _, v := range views {
		v.schedulingCtx.polyserveRequest = req
	}
	return views
}

func viewSet(views ...*instanceViewScheduling) map[string]*instanceViewScheduling {
	out := map[string]*instanceViewScheduling{}
	for _, v := range views {
		out[v.GetInstanceId()] = v
	}
	return out
}

func request(id string, tier int, ttftSloMs float64, waitedMs float64) *polyserveRequest {
	return &polyserveRequest{
		id: id, tier: tier, ttftSloMs: ttftSloMs,
		tpotSloMs: float64(tier), waitedMs: waitedMs,
	}
}

// The ladder replaces a filter pair plus Llumnix's blanket fallback pass. With
// every switch at its default it has to decide exactly what that pair decided,
// or every PolyServe run recorded before this change becomes incomparable with
// the ones after it for a reason that has nothing to do with the paper.
//
// Both cases the old pairing produced are covered: a server that admits gets the
// request, and when none admits the request is still placed -- on the emptiest
// server of its own tier -- because the paper has no drop path.
func TestLadderDefaultsDecideWhatTheFilterPairDecided(t *testing.T) {
	p := ladderPolicy(polyserveConfig{})
	sel := &polyserveSelector{policy: p}

	// a admits and is busier; b admits and is emptier; c would admit but is not
	// in the tier.
	a := polyserveView("a", 6000, 50, 3000, 20, 30)
	b := polyserveView("b", 6000, 50, 600, 10, 12)
	c := polyserveView("c", 6000, 50, 100, 5, 6)
	p.tierPartition.set(map[int]map[string]struct{}{
		50: {"a": {}, "b": {}},
	})
	views := withRequest(viewSet(a, b, c), request("r1", 50, 6000, 0))

	chosen := sel.selectInstance(views, false)
	assert.NotNil(t, chosen)
	assert.Equal(t, "b", chosen.GetInstanceId(),
		"least loaded admissible server of the request's own tier, as before")

	// Now make both members of the tier fail admission on the steady state.
	a2 := polyserveView("a", 6000, 50, 3000, 20, 800)
	b2 := polyserveView("b", 6000, 50, 600, 10, 900)
	views = withRequest(viewSet(a2, b2, c), request("r2", 50, 6000, 0))
	chosen = sel.selectInstance(views, false)
	assert.NotNil(t, chosen, "the paper has no drop path: it is placed anyway")
	assert.Equal(t, "a", chosen.GetInstanceId(),
		"and on the emptiest of its own tier, which is what the fallback pass did")
	assert.False(t, p.admissionRejected("r2"), "nothing was refused")
}

// The whole point of letting admission bind. Held while waiting can still help,
// refused once the request's own first-token budget is gone whatever it does
// next -- so the decision is the request's deadline, not the gateway's patience.
func TestAdmissionBindsHoldsUntilTheDeadlineIsGone(t *testing.T) {
	p := ladderPolicy(polyserveConfig{admissionBinds: true})
	sel := &polyserveSelector{policy: p}
	p.tierPartition.set(map[int]map[string]struct{}{50: {"a": {}}})

	// Fails the steady-state check; predicted first token is 900 ms.
	a := polyserveView("a", 5000, 50, 900, 10, 800)

	views := withRequest(viewSet(a), request("r", 50, 5000, 0))
	assert.Nil(t, sel.selectInstance(views, false))
	assert.False(t, p.admissionRejected("r"),
		"900 ms of first token against a 5,000 ms budget: waiting can still help")

	views = withRequest(viewSet(a), request("r", 50, 5000, 4500))
	assert.Nil(t, sel.selectInstance(views, false))
	assert.True(t, p.admissionRejected("r"),
		"4,500 ms already spent plus 900 ms to come exceeds 5,000: holding is pointless")
	assert.False(t, p.admissionRejected("r"), "and it is reported exactly once")
}

// Section 4.4 promotes a request onto a tier whose budget is TIGHTER than its
// own. The guest must be judged by the host's budget: judging it by its own
// would admit it onto a server whose residents it then breaks, which is exactly
// the claim the paper makes about promotion being harmless.
func TestPromotionIsJudgedByTheHostTierBudget(t *testing.T) {
	p := ladderPolicy(polyserveConfig{admissionBinds: true, lazyPromotion: true})
	sel := &polyserveSelector{policy: p}
	p.tierPartition.set(map[int]map[string]struct{}{
		50:  {"tight": {}},
		100: {"loose": {}},
	})

	// The deepresearch request has a 100 ms budget. Its own tier's server cannot
	// take it. The chat tier's server would pass at 100 ms and fails at 50.
	loose := polyserveView("loose", 10000, 100, 500, 20, 400)
	tight := polyserveView("tight", 10000, 100, 500, 20, 70)

	views := withRequest(viewSet(loose, tight), request("r", 100, 10000, 0))
	assert.Nil(t, sel.selectInstance(views, false),
		"70 ms fits the guest's own 100 ms budget but not the host tier's 50 ms")

	// Drop the host's iteration below its own tier's budget and the guest lands.
	tight2 := polyserveView("tight", 10000, 100, 500, 20, 40)
	views = withRequest(viewSet(loose, tight2), request("r", 100, 10000, 0))
	chosen := sel.selectInstance(views, false)
	assert.NotNil(t, chosen)
	assert.Equal(t, "tight", chosen.GetInstanceId())
}

// Lazy, not eager: a request goes to a tighter tier only after its own tier has
// refused it everywhere. Otherwise it lowers the tighter tier's utilisation for
// nothing, which is the comparison section 4.4 makes.
func TestPromotionOnlyAfterTheOwnTierIsFull(t *testing.T) {
	p := ladderPolicy(polyserveConfig{admissionBinds: true, lazyPromotion: true})
	sel := &polyserveSelector{policy: p}
	p.tierPartition.set(map[int]map[string]struct{}{
		50:  {"tight": {}},
		100: {"loose": {}},
	})
	// Both would admit; the tighter one is emptier, so an eager rule would take
	// it and a lazy one must not.
	loose := polyserveView("loose", 10000, 100, 5000, 20, 90)
	tight := polyserveView("tight", 10000, 100, 100, 5, 10)

	views := withRequest(viewSet(loose, tight), request("r", 100, 10000, 0))
	chosen := sel.selectInstance(views, false)
	assert.NotNil(t, chosen)
	assert.Equal(t, "loose", chosen.GetInstanceId(),
		"its own tier can still take it, so it stays there")
}

// Section 4.3: the request pends, and that pending is itself the signal that
// takes a server out of the idle pool.
func TestPendingTakesAServerFromTheIdlePool(t *testing.T) {
	p := ladderPolicy(polyserveConfig{
		admissionBinds: true, elastic: true, preferLoaded: true,
	})
	sel := &polyserveSelector{policy: p}

	a := polyserveView("a", 5000, 50, 100, 5, 10)
	views := viewSet(a)
	p.fleet.observe(50)
	p.fleet.sync(views)
	assert.Equal(t, 0, p.fleet.tierOfInstance("a"), "starts in the pool, serving nothing")

	withRequest(views, request("r", 50, 5000, 0))
	chosen := sel.selectInstance(views, false)
	assert.NotNil(t, chosen, "the tier held nothing, so it takes a server")
	assert.Equal(t, 50, p.fleet.tierOfInstance("a"))

	// And now it is served from the tier itself, with no further scaling.
	withRequest(views, request("r2", 50, 5000, 0))
	assert.NotNil(t, sel.selectInstance(views, false))
	assert.Equal(t, 50, p.fleet.tierOfInstance("a"))
}

// With four servers and three tiers the pool empties within seconds, so a tier
// that lost the start-up race would pend every request for the rest of the run.
// A tier that has been seen and holds nothing takes one from the largest holder.
func TestATierThatHoldsNothingTakesFromTheLargestHolder(t *testing.T) {
	f := newPolyserveFleet()
	views := viewSet(
		polyserveView("i1", 5000, 50, 0, 0, 0),
		polyserveView("i2", 5000, 50, 0, 0, 0),
		polyserveView("i3", 5000, 50, 0, 0, 0),
	)
	f.observe(50)
	f.observe(100)
	f.sync(views)

	assert.NotEqual(t, "", f.claim(50, views))
	assert.NotEqual(t, "", f.claim(50, views))
	assert.NotEqual(t, "", f.claim(50, views))
	assert.Equal(t, "", f.claim(50, views), "the pool is empty and this tier already holds servers")

	taken := f.claim(100, views)
	assert.NotEqual(t, "", taken, "a tier holding nothing is not left to starve")
	assert.Equal(t, 100, f.tierOfInstance(taken))
}

// Scale-down needs a server that is both the last one of its tier and empty, and
// it needs to stay empty: a momentary gap between two batches is not an idle
// server.
func TestReleaseNeedsAnEmptyLastServerForAWholeStreak(t *testing.T) {
	f := newPolyserveFleet()
	busy := polyserveViewWithBatch("busy", 12)
	idle := polyserveViewWithBatch("idle", 0)
	views := viewSet(busy, idle)
	f.observe(50)
	f.sync(views)
	f.claim(50, views)
	f.claim(50, views)
	assert.Equal(t, 50, f.tierOfInstance("busy"))
	assert.Equal(t, 50, f.tierOfInstance("idle"))

	start := time.Now()
	f.maybeRelease(start, views)                                 // first tick only arms the timer
	f.maybeRelease(start.Add(2*repartitionPeriod), views)        // streak 1
	assert.Equal(t, 50, f.tierOfInstance("idle"), "one empty check is not enough")
	f.maybeRelease(start.Add(4*repartitionPeriod), views)        // streak 2
	assert.Equal(t, 0, f.tierOfInstance("idle"), "returned to the pool")
	assert.Equal(t, 50, f.tierOfInstance("busy"), "the tier keeps its foothold")
}

// The rule that makes scale-down possible at all: fill servers in order, so the
// last one runs empty. With least-loaded selection every server stays partly
// full and nothing is ever released.
func TestPreferLoadedPicksTheFullestAdmissibleServer(t *testing.T) {
	full := polyserveView("full", 5000, 50, 3000, 10, 40)
	empty := polyserveView("empty", 5000, 50, 100, 5, 8)
	views := viewSet(full, empty)
	req := request("r", 50, 5000, 0)

	loaded := polyserveConfig{
		globalTtftSloMs: 6000, globalTpotSloMs: 50,
		ttftMultiplier: 1, tpotMultiplier: 1, preferLoaded: true,
	}
	chosen, _ := loaded.pick(views, req, 50)
	assert.Equal(t, "full", chosen.GetInstanceId())

	least := loaded
	least.preferLoaded = false
	chosen, _ = least.pick(views, req, 50)
	assert.Equal(t, "empty", chosen.GetInstanceId())
}

// The port has no memory predicate, so the fleet is driven past its KV capacity
// and the engine recovers by preempting. The comparison is against the
// instance's own reported capacity, so there is no threshold to choose.
func TestKvAdmissionRefusesABatchThatWillNotFit(t *testing.T) {
	cfg := polyserveConfig{
		globalTtftSloMs: 6000, globalTpotSloMs: 50,
		ttftMultiplier: 1, tpotMultiplier: 1, kvAdmission: true,
	}
	view := polyserveView("a", 5000, 50, 100, 5, 10)
	view.cmsView.Status.NumTotalGpuTokens = 100000
	view.schedulingCtx.metrics[consts.SchedulingMetricPolyserveIterMax] =
		&polyserveIterTime{
			baseMetric:        baseMetric{name: consts.SchedulingMetricPolyserveIterMax, value: 10},
			latency:           10,
			projectedKvTokens: 250000,
		}
	req := request("r", 50, 5000, 0)
	assert.Equal(t, "memory", cfg.judge(view, req, 50).reason)

	cfg.kvAdmission = false
	assert.True(t, cfg.judge(view, req, 50).admitted,
		"and the predicate is off by default, so no earlier run is re-interpreted")
}

// The pending time a request has already spent is what makes the refusal rule a
// statement about its own deadline. Measured from the first time the scheduler
// was asked about it, because the gateway re-asks about a held request.
func TestWaitLogMeasuresFromTheFirstSighting(t *testing.T) {
	w := newPolyserveWaitLog()
	assert.Equal(t, 0.0, w.waited("r", 1000))
	assert.Equal(t, 500.0, w.waited("r", 1500))
	w.forget("r")
	assert.Equal(t, 0.0, w.waited("r", 2000), "placed or refused, so the clock restarts")
}

// polyserveViewWithBatch carries a running-request count, which is what the
// release rule reads.
func polyserveViewWithBatch(id string, running int32) *instanceViewScheduling {
	v := polyserveView(id, 5000, 50, 0, 0, 0)
	v.cmsView.Status.SchedulerRunningToDecodeRequestsNum = running
	return v
}
