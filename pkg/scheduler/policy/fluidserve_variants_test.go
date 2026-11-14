package policy

import (
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestVariantSameReproducesTheRealVerdict(t *testing.T) {
	// The same variant re-runs v0.5's conjunction and must agree on every
	// candidate, with the pace cap on and off.
	for _, paceCap := range []bool{false, true} {
		for _, tier := range []int{25, 50, 100} {
			for _, prompt := range []int{100, 600, 4000, 9000, 30000} {
				p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
					c.shadowVariants = true
					c.memoryUsesPaceCap = paceCap
				})
				cands, _ := shadowCandidates(t, p, tier, prompt)
				for _, c := range cands {
					require.True(t, c.variantOn)
					assert.Equal(t, c.feasible, c.variant[varSame],
						"pacecap %v tier %d prompt %d instance %s", paceCap, tier, prompt, c.flux.id)
				}
			}
		}
	}
}

func TestVariantsAreOffOutsideTheV05Path(t *testing.T) {
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.shadowVariants = true
		c.slackProtect = true
	})
	cands, _ := shadowCandidates(t, p, 50, 600)
	for _, c := range cands {
		assert.False(t, c.variantOn)
	}
}

func TestG10IsNeverStricterThanTheRealGate(t *testing.T) {
	// A 1.0 gate admits a superset of the 0.9 gate on the same state.
	for _, tier := range []int{25, 50, 100} {
		for _, prompt := range []int{600, 4000, 9000} {
			p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.shadowVariants = true
				c.memoryUsesPaceCap = true
			})
			cands, _ := shadowCandidates(t, p, tier, prompt)
			for _, c := range cands {
				if c.feasible {
					assert.True(t, c.variant[varG10], "tier %d prompt %d on %s", tier, prompt, c.flux.id)
				}
			}
		}
	}
}

func lateTestPolicy(t *testing.T) *fluidserveDispatchPolicy {
	return fsPolicy(t, "25:decode:75,50:decode,100:decode", nil)
}

func TestLateTokenTestChargesOnlyTheMarginalDelay(t *testing.T) {
	// A long queue ahead of the newcomer delays the residents whether or not it
	// is admitted; a short prompt behind it adds little, so a resident whose
	// slack is smaller than the whole queue's delay but larger than the
	// newcomer's share is not counted as harmed.
	p := lateTestPolicy(t)
	whole, _ := p.stallMs(20000, 8192, 1)
	alone, _ := p.stallMs(20000+200, 8192, 1)
	marginal := alone - whole
	require.Greater(t, whole, 500.0)
	require.Less(t, marginal, whole)
	f := &instanceFlux{chunk: 8192, pendingPrefill: 20000, live: []liveRequest{
		{nominalMs: 50, j: 100, remaining: 300, slackLadderMs: marginal + 50},
	}}
	late, unpred := p.lateTokenTest(f, 40, 200, 1)
	assert.False(t, unpred)
	assert.False(t, late)
}

func TestLateTokenTestCountsTokensUntilTheDeficitIsMadeBack(t *testing.T) {
	// No queue: an 8k prompt costs D ms; a resident with no slack is late for
	// ceil(D / (50 - 40)) tokens and fails when that exceeds 5% of its length.
	p := lateTestPolicy(t)
	d, _ := p.stallMs(8000, 8192, 1)
	need := math.Ceil(d / 10)
	short := need / fsLateTokenShare * 0.5 // total tokens such that need > 5%
	long := need / fsLateTokenShare * 2    // total tokens such that need < 5%
	f := &instanceFlux{chunk: 8192, live: []liveRequest{{nominalMs: 50, j: 0, remaining: short, slackLadderMs: 0}}}
	late, _ := p.lateTokenTest(f, 40, 8000, 1)
	assert.True(t, late)
	f.live[0].remaining = long
	late, _ = p.lateTokenTest(f, 40, 8000, 1)
	assert.False(t, late)
	// Waiting, already-behind and unachievable residents are not protected.
	for _, r := range []liveRequest{
		{nominalMs: 50, remaining: short, slackLadderMs: 0, waiting: true},
		{nominalMs: 50, remaining: short, slackLadderMs: -10},
		{nominalMs: 50, remaining: short, slackLadderMs: 0, unachievable: true},
	} {
		f.live = []liveRequest{r}
		late, _ = p.lateTokenTest(f, 40, 8000, 1)
		assert.False(t, late)
	}
}

func TestVariantPeakHasNoArrivalTerm(t *testing.T) {
	// Arrivals noted for the deployed forecast do not enter the variant: on an
	// empty instance the variant forecast stays at the current KV.
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.arrivalHalfLifeMs = 10000
	})
	for ms := int64(0); ms <= 20000; ms += 100 {
		p.noteArrivalRate("x", ms, 1000)
	}
	f := &instanceFlux{id: "x", kvLogical: 100000, horizonPaceMs: 50}
	mean, withZ := p.variantPeak(f, 20000)
	assert.InDelta(t, 100000, mean, 1e-6)
	assert.InDelta(t, 100000, withZ, 1e-6) // no error measured yet
	assert.Greater(t, p.peakForecast(f, 20000), 100000.0)
}

func TestCanWaitPendingOnlyIgnoresAnticipatedArrivals(t *testing.T) {
	// The pending-only first-token estimate is never longer than the real one,
	// so T can only turn sheds into pends, never the reverse.
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.prefillInterleaveAware = true
		c.enablePend = true
	})
	f := &instanceFlux{id: "x", chunk: 8192, pendingPrefill: 0, arrivingPrefill: 40000, effectivePrefill: 40000}
	req := &fluidserveRequest{id: "r", tier: 50, promptTokens: 600, ttftSloMs: 5000}
	real, _, _ := p.prefillEstimateParts(req, f)
	fc := *f
	fc.effectivePrefill, fc.arrivingPrefill = fc.pendingPrefill, 0
	pendingOnly, _, _ := p.prefillEstimateParts(req, &fc)
	assert.LessOrEqual(t, pendingOnly, real)
}

func TestGateUtilisationOneDecidesWhatShadowG10Predicted(t *testing.T) {
	// --fluidserve-gate-utilisation=1.0 decides, candidate by candidate, what the
	// G1.0 shadow variant predicted on the same state under the 0.9 gate.
	for _, tier := range []int{25, 50, 100} {
		for _, prompt := range []int{100, 600, 4000, 9000, 30000} {
			shadow := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.shadowVariants = true
				c.memoryUsesPaceCap = true
			})
			real := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.gateUtil = 1.0
				c.shadowVariants = true
				c.memoryUsesPaceCap = true
			})
			want := map[string]bool{}
			sc, _ := shadowCandidates(t, shadow, tier, prompt)
			for _, c := range sc {
				want[c.flux.id] = c.variant[varG10]
			}
			rc, _ := shadowCandidates(t, real, tier, prompt)
			for _, c := range rc {
				assert.Equal(t, want[c.flux.id], c.feasible, "tier %d prompt %d on %s", tier, prompt, c.flux.id)
				assert.Equal(t, c.feasible, c.variant[varSame])
			}
		}
	}
}

func TestGateUtilisationDefaultIsTheCompiledValue(t *testing.T) {
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", nil)
	assert.InDelta(t, fsAllowanceUtilisation, p.gateUtil(), 1e-12)
}
