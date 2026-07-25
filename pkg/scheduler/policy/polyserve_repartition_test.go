package policy

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"

	"llumnix/pkg/cms"
	"llumnix/pkg/scheduler/predictor"
	"llumnix/pkg/types"
)

// Miniature stand-ins for the shipped profiling tables, using the same shapes
// the real ones were fitted to: prefill is a floor plus a per-token rate, decode
// is a floor plus KV and batch terms. Both are complete grids, because the Go
// interpolator brackets on the marginal axes and then demands all four corners.
func newTestPrefillPredictor() *predictor.InterpolationPredictor {
	p := predictor.NewInterpolationPredictor()
	for _, tokens := range []float64{1, 1024, 8192, 65536} {
		p.AddSample(tokens, 0, 16.43+tokens/13263.0*1000.0)
	}
	return p
}

func newTestDecodePredictor() *predictor.InterpolationPredictor {
	p := predictor.NewInterpolationPredictor()
	for batch := 1.0; batch <= 2048; batch *= 2 {
		for _, tok := range []float64{8, 1024, 8192, 65536} {
			p.AddSample(batch, tok, 17.0+9e-6*batch*tok+0.08*batch)
		}
	}
	return p
}

func keysOf(m map[string]struct{}) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func TestAllocateServersProportional(t *testing.T) {
	// swe carries most of the token mass, so it should draw most of the fleet
	// while the other tiers keep a server each.
	got := allocateServers(map[int]float64{25: 3.0, 50: 0.4, 100: 0.6}, 4)
	assert.Equal(t, map[int]int{25: 2, 50: 1, 100: 1}, got)
	assert.Equal(t, 4, got[25]+got[50]+got[100], "allocation must sum to the fleet")
}

// Every tier keeps a server even when its demand rounds to nothing; otherwise
// a quiet tier would have nowhere to be scheduled at all.
func TestAllocateServersGuaranteesOnePerTier(t *testing.T) {
	got := allocateServers(map[int]float64{25: 100, 50: 0.001, 100: 0.001}, 4)
	for tier, count := range got {
		assert.GreaterOrEqual(t, count, 1, "tier %d starved", tier)
	}
	assert.Equal(t, 4, got[25]+got[50]+got[100])
}

func TestAllocateServersSumsToFleet(t *testing.T) {
	for _, n := range []int{3, 4, 5, 8, 16} {
		got := allocateServers(map[int]float64{25: 3.0, 50: 0.4, 100: 0.6}, n)
		total := 0
		for _, c := range got {
			total += c
		}
		assert.Equal(t, n, total, "fleet of %d", n)
	}
}

// Fewer servers than tiers makes the one-each guarantee impossible. The busiest
// tiers get a server and the rest stay unallocated, which the affinity filter
// reads as unrestricted rather than unschedulable.
func TestAllocateServersFewerServersThanTiers(t *testing.T) {
	got := allocateServers(map[int]float64{25: 3.0, 50: 0.4, 100: 0.6}, 2)
	assert.Equal(t, map[int]int{25: 1, 100: 1}, got)

	p := newTierPartition()
	p.set(assignInstances(nil, got, []string{"i1", "i2"}))
	assert.True(t, p.allows(50, "i1"), "an unallocated tier must stay schedulable")
}

func TestAllocateServersNoTrafficSpreadsEvenly(t *testing.T) {
	got := allocateServers(map[int]float64{25: 0, 50: 0, 100: 0}, 4)
	total := 0
	for _, c := range got {
		total += c
		assert.GreaterOrEqual(t, c, 1)
	}
	assert.Equal(t, 4, total)
}

func TestAllocateServersEdgeCases(t *testing.T) {
	assert.Empty(t, allocateServers(map[int]float64{}, 4))
	assert.Empty(t, allocateServers(map[int]float64{25: 1}, 0))
}

// Reassignment is cheap but not free -- a server that switches tier drains its
// old queue first -- so recomputing the same allocation must not shuffle
// servers around.
func TestAssignInstancesKeepsPreviousWherePossible(t *testing.T) {
	live := []string{"i1", "i2", "i3", "i4"}
	first := assignInstances(nil, map[int]int{25: 2, 50: 1, 100: 1}, live)
	second := assignInstances(first, map[int]int{25: 2, 50: 1, 100: 1}, live)
	assert.Equal(t, first, second, "a stable target must not move servers")

	// Growing a tier by one takes a single server, leaving the rest in place.
	third := assignInstances(first, map[int]int{25: 3, 50: 1, 100: 0}, live)
	for id := range first[25] {
		assert.Contains(t, third[25], id, "%s should have stayed on tier 25", id)
	}
	assert.Len(t, third[25], 3)
	assert.Len(t, third[50], 1)
}

func TestAssignInstancesPartitionsWithoutOverlap(t *testing.T) {
	live := []string{"i1", "i2", "i3", "i4"}
	got := assignInstances(nil, map[int]int{25: 2, 50: 1, 100: 1}, live)
	seen := map[string]int{}
	for _, servers := range got {
		for _, id := range keysOf(servers) {
			seen[id]++
		}
	}
	for _, id := range live {
		assert.Equal(t, 1, seen[id], "%s must belong to exactly one tier", id)
	}
}

func TestAssignInstancesDropsDeadServers(t *testing.T) {
	previous := map[int]map[string]struct{}{25: {"i1": {}, "gone": {}}}
	got := assignInstances(previous, map[int]int{25: 2}, []string{"i1", "i2"})
	assert.NotContains(t, got[25], "gone")
	assert.Len(t, got[25], 2)
}

func repartitionerForTest() *tierRepartitioner {
	decodeTokens, _ := parseTierDecodeTokens("25:728,50:386,100:275", 463)
	// No predictor: costs collapse to zero, which is fine for the tests that
	// exercise windowing and hysteresis rather than the cost model.
	return newTierRepartitioner(decodeTokens, nil, newTierPartition())
}

func viewsForTest(ids ...string) map[string]*instanceViewScheduling {
	out := map[string]*instanceViewScheduling{}
	for _, id := range ids {
		cmsView := &cms.InstanceView{
			Status:   &cms.InstanceStatus{InstanceId: id},
			Metadata: &cms.InstanceMetadata{InstanceId: id, MaxNumBatchedTokens: 8192},
		}
		out[id] = &instanceViewScheduling{cmsView: cmsView, InstanceViewInterface: cmsView}
	}
	return out
}

// The first call only starts the window; acting on a zero-length window would
// divide arrival counts by ~0 and produce absurd demand.
func TestRepartitionFirstCallOnlyStartsWindow(t *testing.T) {
	r := repartitionerForTest()
	views := viewsForTest("i1", "i2")
	now := time.Now()
	r.observe(&types.SchedulingRequest{TpotSloMs: 25, PromptNumTokens: 22000})
	r.maybeRepartition(now, views)
	assert.Nil(t, r.partition.snapshot(), "must not allocate before a full window")
	assert.Equal(t, now, r.windowStart)
}

func TestRepartitionWaitsForPeriod(t *testing.T) {
	r := repartitionerForTest()
	views := viewsForTest("i1", "i2")
	start := time.Now()
	r.maybeRepartition(start, views)
	r.observe(&types.SchedulingRequest{TpotSloMs: 25, PromptNumTokens: 22000})
	r.maybeRepartition(start.Add(r.period/2), views)
	assert.Nil(t, r.partition.snapshot())
}

// Hysteresis: a target must be asked for repeatedly before servers move.
func TestRepartitionHysteresis(t *testing.T) {
	r := repartitionerForTest()
	views := viewsForTest("i1", "i2", "i3", "i4")
	now := time.Now()
	r.maybeRepartition(now, views)

	for round := 1; round <= r.stableRounds; round++ {
		r.observe(&types.SchedulingRequest{TpotSloMs: 25, PromptNumTokens: 22000})
		r.observe(&types.SchedulingRequest{TpotSloMs: 50, PromptNumTokens: 674})
		now = now.Add(r.period)
		r.maybeRepartition(now, views)
		if round < r.stableRounds {
			assert.Nil(t, r.partition.snapshot(),
				"round %d: must not apply before the target is stable", round)
		}
	}
	assert.NotNil(t, r.partition.snapshot(), "a stable target must eventually apply")
	total := 0
	for _, servers := range r.partition.snapshot() {
		total += len(servers)
	}
	assert.Equal(t, 4, total)
}

// Requests without their own SLO are unrestricted by the tier filter, so
// counting them would inflate a partition they never occupy.
func TestRepartitionIgnoresRequestsWithoutSlo(t *testing.T) {
	r := repartitionerForTest()
	r.observe(&types.SchedulingRequest{TpotSloMs: 0, PromptNumTokens: 500})
	r.observe(nil)
	assert.Empty(t, r.observed)
}

// A tier that stops receiving traffic must decay instead of holding its share
// of the fleet forever.
func TestRepartitionDemandDecaysForSilentTier(t *testing.T) {
	r := repartitionerForTest()
	views := viewsForTest("i1", "i2")
	now := time.Now()
	r.maybeRepartition(now, views)

	r.demand[25] = 4.0
	now = now.Add(r.period)
	r.observe(&types.SchedulingRequest{TpotSloMs: 50, PromptNumTokens: 674})
	r.maybeRepartition(now, views)
	assert.InDelta(t, 4.0*(1-r.ewmaAlpha), r.demand[25], 1e-9)
}

// A tighter TPOT forces a smaller batch, so the same output costs more server
// time -- the reason a strict tier earns more servers per request.
func TestServerSecondsRisesAsTpotTightens(t *testing.T) {
	predictor := &LatencyPredictor{
		ttftPredictor: newTestPrefillPredictor(),
		tpotPredictor: newTestDecodePredictor(),
	}
	decodeTokens, _ := parseTierDecodeTokens("25:500,100:500", 500)
	r := newTierRepartitioner(decodeTokens, predictor, newTierPartition())

	strict := r.serverSecondsPerRequest(25, 1000, 8192)
	lax := r.serverSecondsPerRequest(100, 1000, 8192)
	assert.Greater(t, strict, 0.0)
	assert.Greater(t, strict, lax,
		"a 25ms tier must cost more server time than a 100ms tier for equal output")
}

func TestMaxBatchForTpotNeverZero(t *testing.T) {
	predictor := &LatencyPredictor{
		ttftPredictor: newTestPrefillPredictor(),
		tpotPredictor: newTestDecodePredictor(),
	}
	decodeTokens, _ := parseTierDecodeTokens("", 500)
	r := newTierRepartitioner(decodeTokens, predictor, newTierPartition())
	// 1ms is unreachable on any batch; charging a whole server per request is
	// the right degenerate answer, and dividing by zero is not.
	assert.Equal(t, int32(1), r.maxBatchForTpot(1, 1000))
	assert.Greater(t, r.maxBatchForTpot(100, 1000), int32(1))
}

func TestSameAllocation(t *testing.T) {
	assert.True(t, sameAllocation(map[int]int{25: 2, 50: 1}, map[int]int{50: 1, 25: 2}))
	assert.False(t, sameAllocation(map[int]int{25: 2}, map[int]int{25: 2, 50: 1}))
	assert.False(t, sameAllocation(map[int]int{25: 2}, map[int]int{25: 3}))
	assert.False(t, sameAllocation(map[int]int{25: 2}, nil))
}

func TestFormatAllocationIsSorted(t *testing.T) {
	assert.Equal(t, "25ms=2 50ms=1 100ms=1",
		formatAllocation(map[int]int{100: 1, 25: 2, 50: 1}))
}
