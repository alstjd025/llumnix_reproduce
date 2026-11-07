package policy

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"llumnix/pkg/consts"
)

// shadowStates is a spread of instance states: empty, decode-heavy, a long
// prompt queued, residents of two classes. The shadow judge has to agree with
// evaluate() on every one of them at the real horizon.
func shadowStates(p *fluidserveDispatchPolicy) map[string]*instanceViewScheduling {
	fill(p, "mixed", 50, 700, 40, 4000, 0)
	fill(p, "mixed", 100, 4000, 20, 4000, 0)
	fill(p, "heavy", 100, 4000, 60, 4000, 0)
	fill(p, "full", 50, 700, 120, 4000, 0)
	return map[string]*instanceViewScheduling{
		"empty": fsView(fsViewOpts{id: "empty", stepID: 5000}),
		"heavy": fsView(fsViewOpts{id: "heavy", decodeReqs: 60, decodeTokens: 300000,
			usedGpu: 250000, stepID: 5000}),
		"queued": fsView(fsViewOpts{id: "queued", decodeReqs: 10, decodeTokens: 100000,
			pendingPre: 24576, usedGpu: 80000, stepID: 5000}),
		"mixed": fsView(fsViewOpts{id: "mixed", decodeReqs: 60, decodeTokens: 200000,
			pendingPre: 4000, usedGpu: 180000, stepID: 5000}),
		"full": fsView(fsViewOpts{id: "full", decodeReqs: 120, decodeTokens: 1200000,
			pendingPre: 16384, usedGpu: 600000, stepID: 5000}),
	}
}

func shadowCandidates(t *testing.T, p *fluidserveDispatchPolicy, tier, prompt int) (
	[]candidate, *fluidserveRequest) {
	t.Helper()
	views := shadowStates(p)
	p.calculateMetrics(consts.InferTypeNeutral, fsRequest("probe", tier, 10000, prompt), views)
	var out []candidate
	var req *fluidserveRequest
	for _, v := range views {
		f := v.schedulingCtx.fluidserveFlux
		require.NotNil(t, f)
		req = v.schedulingCtx.fluidserveRequest
		out = append(out, p.evaluate(f, v, req))
	}
	return out, req
}

func TestShadowAtTheRealHorizonReproducesTheRealVerdict(t *testing.T) {
	for _, tier := range []int{25, 50, 100} {
		for _, prompt := range []int{600, 4000, 9000} {
			p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.shadowHorizon = 25
				c.memoryUsesPaceCap = true
			})
			cands, _ := shadowCandidates(t, p, tier, prompt)
			for _, c := range cands {
				require.True(t, c.shadowOn)
				assert.Equal(t, c.feasible, c.shadow[shadowSame],
					"tier %d prompt %d instance %s", tier, prompt, c.flux.id)
			}
		}
	}
}

func TestShadowEqualToTheHorizonChangesNothing(t *testing.T) {
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.shadowHorizon = 100 // the real horizon
		c.memoryUsesPaceCap = true
	})
	cands, _ := shadowCandidates(t, p, 100, 4000)
	for _, c := range cands {
		for v := 0; v < nShadowVariants; v++ {
			assert.Equal(t, c.feasible, c.shadow[v], "variant %s on %s", shadowNames[v], c.flux.id)
		}
	}
}

func TestShorterPaceHorizonRaisesThePredictedStepWherePrefillIsQueued(t *testing.T) {
	// The pace estimate divides the queued prefill chunks by the horizon, so a
	// quarter of the horizon makes a queued prompt four times as present in the
	// mean iteration. The projection and the charge leave that estimate's
	// prefill share alone.
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.shadowHorizon = 25
	})
	cands, req := shadowCandidates(t, p, 100, 4000)
	for _, c := range cands {
		if c.flux.id != "queued" {
			continue
		}
		f := c.flux
		charge := c.prefillCharge
		real := p.shadowJudge(f, req, &c, charge, 100, 100, 100)
		pace := p.shadowJudge(f, req, &c, charge, 100, 25, 100)
		assert.InDelta(t, c.meanAfter, real.meanAfter, 1e-9)
		assert.Greater(t, pace.meanAfter, real.meanAfter)
	}
}

func TestShadowStatesCoverBothVerdicts(t *testing.T) {
	// The agreement test means nothing if every state is feasible or every one
	// is not: it has to see both verdicts, and the h=25 shadow has to disagree
	// with the real one somewhere, or the probes do not exercise the horizon.
	seen := map[bool]int{}
	flips := 0
	for _, tier := range []int{25, 50, 100} {
		for _, prompt := range []int{600, 4000, 9000} {
			p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.shadowHorizon = 25
				c.memoryUsesPaceCap = true
			})
			cands, _ := shadowCandidates(t, p, tier, prompt)
			for _, c := range cands {
				seen[c.feasible]++
				if c.shadow[shadowAll] != c.feasible {
					flips++
				}
			}
		}
	}
	assert.Positive(t, seen[true])
	assert.Positive(t, seen[false])
	assert.Positive(t, flips)
	t.Logf("feasible %d, infeasible %d, h=25 flips %d", seen[true], seen[false], flips)
}

func TestPaceHorizonFlagMatchesThePaceShadow(t *testing.T) {
	// --fluidserve-pace-horizon=25 on a 100-iteration plan must decide exactly
	// what the shadow judge's "pace" variant at 25 said that plan would decide:
	// the flag is the shadow's pace term made real, and nothing else.
	for _, tier := range []int{25, 50, 100} {
		for _, prompt := range []int{600, 4000, 9000} {
			shadow := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.shadowHorizon = 25
				c.memoryUsesPaceCap = true
			})
			paced := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.paceHorizon = 25
				c.shadowHorizon = 100
				c.memoryUsesPaceCap = true
			})
			want := map[string]bool{}
			sc, _ := shadowCandidates(t, shadow, tier, prompt)
			for _, c := range sc {
				want[c.flux.id] = c.shadow[shadowPace]
			}
			pc, _ := shadowCandidates(t, paced, tier, prompt)
			for _, c := range pc {
				assert.Equal(t, want[c.flux.id], c.feasible, "tier %d prompt %d on %s", tier, prompt, c.flux.id)
				// With the pace horizon set apart, a shadow at the planning horizon
				// changes nothing: "same" and "all" both leave the pace term alone.
				assert.Equal(t, c.feasible, c.shadow[shadowSame])
				assert.Equal(t, c.feasible, c.shadow[shadowAll])
			}
		}
	}
}
