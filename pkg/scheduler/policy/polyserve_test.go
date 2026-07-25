package policy

import (
	"testing"

	"github.com/stretchr/testify/assert"

	"llumnix/pkg/cms"
	"llumnix/pkg/consts"
	"llumnix/pkg/types"
)

// polyserveView builds an instance view carrying already-computed metric values,
// so the filter and selector can be exercised without a profiling table or a
// live CMS.
func polyserveView(id string, ttftSloMs, tpotSloMs int, ttft, iterNow, iterMax float32) *instanceViewScheduling {
	// InstanceViewInterface must alias cmsView exactly as toClusterViewScheduling
	// sets it up, or GetInstanceId() dereferences a nil interface.
	cmsView := &cms.InstanceView{
		Instance: &types.LLMInstance{InferType: consts.InferTypeNeutral},
		Status:   &cms.InstanceStatus{InstanceId: id},
		Metadata: &cms.InstanceMetadata{InstanceId: id},
	}
	return &instanceViewScheduling{
		cmsView:               cmsView,
		InstanceViewInterface: cmsView,
		schedulingCtx: schedulingCtx{
			requestTtftSloMs: ttftSloMs,
			requestTpotSloMs: tpotSloMs,
			metrics: map[string]instanceSchedulingMetric{
				consts.SchedulingMetricPredictedTtft: &baseMetric{
					name: consts.SchedulingMetricPredictedTtft, value: ttft},
				consts.SchedulingMetricPolyserveIterNow: &baseMetric{
					name: consts.SchedulingMetricPolyserveIterNow, value: iterNow},
				consts.SchedulingMetricPolyserveIterMax: &baseMetric{
					name: consts.SchedulingMetricPolyserveIterMax, value: iterMax},
			},
		},
	}
}

func admissionFilter() *polyserveAdmissionFilter {
	return &polyserveAdmissionFilter{
		globalTtftSloMs: 6000, globalTpotSloMs: 50,
		ttftSloMultiplier: 1.0, tpotSloMultiplier: 1.0,
	}
}

func TestPolyserveAdmissionThreeChecks(t *testing.T) {
	f := admissionFilter()

	// swe tier: TTFT 11.8s, TPOT 25ms.
	cases := []struct {
		name     string
		ttft     float32
		iterNow  float32
		iterMax  float32
		rejected bool
		why      string
	}{
		{"all comfortable", 3000, 20, 22, false, ""},
		{"first token misses TTFT", 12000, 20, 22, true, "4.x first token"},
		{"steady state exceeds TPOT", 3000, 20, 40, true, "4.5 max-KV"},
		// A prefill chunk makes the next iteration cost 634ms. With TTFT nearly
		// spent there is no room for it before the second token's deadline, and
		// only the second-token check sees this: TTFT and the steady state both
		// pass on their own.
		{"prefill chunk blows the second token deadline", 11500, 634, 22, true, "4.7 + 4.6"},
		// The same slow iteration is harmless when the first token landed early,
		// because the second token's deadline is TTFT_slo + TPOT_slo from
		// arrival, not one TPOT after the first token.
		{"TTFT slack absorbs a slow next iteration", 3000, 634, 22, false, ""},
		{"exactly at the boundary is admitted", 11800, 25, 25, false, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			v := polyserveView("i", 11800, 25, c.ttft, c.iterNow, c.iterMax)
			assert.Equal(t, c.rejected, f.instanceFilteredOut(v), c.why)
		})
	}
}

// The second-token check (section 4.6) must not be implied by the other two.
// If it were written against the steady-state iteration instead of the
// near-term one it would be pure algebra -- ttft<=T and iter<=P give
// ttft+iter<=T+P -- and would reject nothing.
func TestPolyserveSecondTokenCheckIsNotRedundant(t *testing.T) {
	f := admissionFilter()

	// Passes check 1 (ttft 3000 <= 11800) and check 3 (iterMax 22 <= 25),
	// yet 3000 + 634 > 11800 + 25 is false, so it is admitted...
	admitted := polyserveView("a", 11800, 25, 3000, 634, 22)
	assert.False(t, f.instanceFilteredOut(admitted))

	// ...whereas a request whose TTFT is nearly spent cannot absorb the same
	// slow iteration, and only check 2 catches it.
	rejected := polyserveView("b", 11800, 25, 11500, 634, 22)
	assert.True(t, f.instanceFilteredOut(rejected))

	// Prove it is check 2 doing the work: both other checks pass on their own.
	assert.LessOrEqual(t, float32(11500), float32(11800), "check 1 passes")
	assert.LessOrEqual(t, float32(22), float32(25), "check 3 passes")
}

// A request without its own SLO must be judged against the global config,
// not against zero (which would reject everything).
func TestPolyserveAdmissionFallsBackToGlobalSlo(t *testing.T) {
	f := admissionFilter()
	v := polyserveView("i", 0, 0, 3000, 30, 40)
	assert.False(t, f.instanceFilteredOut(v), "3000<=6000 and 40<=50 under global SLO")

	tooSlow := polyserveView("i", 0, 0, 3000, 30, 60)
	assert.True(t, f.instanceFilteredOut(tooSlow), "60>50 global TPOT")
}

func TestPolyserveDispatchThresholdTightensBudget(t *testing.T) {
	f := admissionFilter()
	f.tpotSloMultiplier = 0.9
	// 24 <= 25 raw, but 24 > 25*0.9 = 22.5.
	assert.True(t, f.instanceFilteredOut(polyserveView("i", 11800, 25, 100, 10, 24)))
}

// Admission relaxes on the fallback pass so an overloaded tier still places its
// requests; tier affinity does not, so isolation survives overload. This is the
// pairing the whole design rests on.
func TestPolyserveFallbackSemantics(t *testing.T) {
	assert.True(t, admissionFilter().skipWhenFallback(), "admission must relax")
	assert.False(t, (&tierAffinityFilter{partition: newTierPartition()}).skipWhenFallback(),
		"tier isolation must survive fallback")
}

func TestTierPartition(t *testing.T) {
	p := newTierPartition()
	// Default partition is inert: P3 will populate it, and until then every
	// server serves every tier rather than none.
	assert.True(t, p.allows(25, "any"))

	p.assignment = map[int]map[string]struct{}{
		25: {"i1": {}, "i2": {}},
		50: {"i3": {}},
	}
	assert.True(t, p.allows(25, "i1"))
	assert.False(t, p.allows(25, "i3"))
	assert.True(t, p.allows(50, "i3"))
	// An unassigned tier is unrestricted rather than starved.
	assert.True(t, p.allows(100, "i1"))

	var nilPartition *tierPartition
	assert.True(t, nilPartition.allows(25, "i1"))
}

func TestTierAffinityFilter(t *testing.T) {
	p := newTierPartition()
	p.assignment = map[int]map[string]struct{}{25: {"i1": {}}}
	f := &tierAffinityFilter{partition: p}

	assert.False(t, f.instanceFilteredOut(polyserveView("i1", 11800, 25, 0, 0, 0)))
	assert.True(t, f.instanceFilteredOut(polyserveView("i2", 11800, 25, 0, 0, 0)))
	// A chat request (tier 50) is unrestricted here.
	assert.False(t, f.instanceFilteredOut(polyserveView("i2", 5000, 50, 0, 0, 0)))
}

// The selector scores each instance by its worst-case SLO utilisation, so the
// dimension that would bind first decides -- without needing a global choice of
// dimension.
func TestLeastBindingLatencySelector(t *testing.T) {
	s := &leastBindingLatencySelector{globalTtftSloMs: 6000, globalTpotSloMs: 50}

	// i1 is better on TTFT but far worse on TPOT; i2's worst dimension is
	// lower, so i2 wins even though i1 looks better on the first metric.
	instances := map[string]*instanceViewScheduling{
		"i1": polyserveView("i1", 10000, 100, 1000, 10, 90), // max(0.10, 0.90)
		"i2": polyserveView("i2", 10000, 100, 5000, 10, 20), // max(0.50, 0.20)
	}
	assert.Equal(t, "i2", s.selectInstance(instances, false).GetInstanceId())

	// With TPOT comfortable on both, TTFT binds and the lower one wins.
	instances = map[string]*instanceViewScheduling{
		"i1": polyserveView("i1", 10000, 100, 1000, 10, 10),
		"i2": polyserveView("i2", 10000, 100, 5000, 10, 10),
	}
	assert.Equal(t, "i1", s.selectInstance(instances, false).GetInstanceId())

	assert.Nil(t, s.selectInstance(map[string]*instanceViewScheduling{}, false))
}

// Go randomises map iteration order, so an unbroken tie would make routing
// differ run to run and an experiment irreproducible.
func TestLeastBindingLatencySelectorTieIsDeterministic(t *testing.T) {
	s := &leastBindingLatencySelector{globalTtftSloMs: 6000, globalTpotSloMs: 50}
	instances := map[string]*instanceViewScheduling{
		"i3": polyserveView("i3", 10000, 100, 1000, 10, 10),
		"i1": polyserveView("i1", 10000, 100, 1000, 10, 10),
		"i2": polyserveView("i2", 10000, 100, 1000, 10, 10),
	}
	for i := 0; i < 50; i++ {
		assert.Equal(t, "i1", s.selectInstance(instances, false).GetInstanceId())
	}
}

func TestParseTierDecodeTokens(t *testing.T) {
	tt, err := parseTierDecodeTokens("25:728,50:386,100:275", 463)
	assert.NoError(t, err)
	assert.Equal(t, 728, tt.forTier(25))
	assert.Equal(t, 386, tt.forTier(50))
	assert.Equal(t, 275, tt.forTier(100))
	// Unlisted tier, and the "no per-request SLO" case (tier 0), take the default.
	assert.Equal(t, 463, tt.forTier(30))
	assert.Equal(t, 463, tt.forTier(0))

	tt, err = parseTierDecodeTokens("", 463)
	assert.NoError(t, err)
	assert.Equal(t, 463, tt.forTier(25))

	tt, err = parseTierDecodeTokens(" 25 : 728 , 50:386 ", 463)
	assert.NoError(t, err)
	assert.Equal(t, 728, tt.forTier(25))

	for _, bad := range []string{"25", "abc:1", "25:xyz", "0:100", "25:0", "-5:100"} {
		_, err := parseTierDecodeTokens(bad, 463)
		assert.Error(t, err, "should reject %q", bad)
	}
	_, err = parseTierDecodeTokens("25:728", 0)
	assert.Error(t, err, "non-positive default must be rejected")
}

func TestEffectiveSloMs(t *testing.T) {
	assert.Equal(t, float32(11800), effectiveSloMs(11800, 6000))
	assert.Equal(t, float32(6000), effectiveSloMs(0, 6000))
}
