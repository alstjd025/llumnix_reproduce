package policy

import (
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestArrivalAverageConvergesToTheRate(t *testing.T) {
	// 1,000 prompt tokens every 100 ms is 10 tokens per ms; an exponential
	// average of the impulses, normalised by its integral, must settle there.
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.peakForecast = true
		c.arrivalHalfLifeMs = 10000
	})
	for ms := int64(0); ms <= 120000; ms += 100 {
		p.noteArrivalRate("i", ms, 1000)
	}
	assert.InDelta(t, 10.0, p.arrivalRate("i", 120000), 0.5)
	// With nothing arriving, one half-life halves it.
	assert.InDelta(t, p.arrivalRate("i", 120000)/2, p.arrivalRate("i", 130000), 0.3)
}

func TestPeakForecastIsTheMaximumNotTheEndPoint(t *testing.T) {
	// Residents near the end of their lengths release KV late in the horizon;
	// growth and arrivals come in from the start. The forecast is the peak over
	// the horizon, so it is never below the current KV plus early growth, while
	// the old end point can fall below the current KV.
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.peakForecast = true
		c.arrivalHalfLifeMs = 10000
		c.forecastZ = 1.65
	})
	fill(p, "heavy", 100, 4000, 60, 4000, 0)
	views := map[string]*instanceViewScheduling{
		"heavy": fsView(fsViewOpts{id: "heavy", decodeReqs: 60, decodeTokens: 300000,
			usedGpu: 250000, stepID: 5000}),
	}
	p.calculateMetrics("neutral", fsRequest("probe", 50, 5000, 600), views)
	f := views["heavy"].schedulingCtx.fluidserveFlux
	require.NotNil(t, f)
	endPoint := f.kvLogical + f.inflow - f.outflow
	assert.GreaterOrEqual(t, f.proj, f.kvLogical)
	assert.GreaterOrEqual(t, f.proj, endPoint)
}

func TestForecastErrorIsMeasuredWhenForecastsComeDue(t *testing.T) {
	// Each forecast is scored once its offset has passed, against the KV the
	// instance holds then; a forecast that was 50,000 tokens off raises the
	// spread, and with it the forecast z x dev.
	p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
		c.peakForecast = true
		c.arrivalHalfLifeMs = 10000
		c.forecastZ = 1.0
	})
	f := &instanceFlux{id: "x", kvLogical: 100000, nDecode: 0, horizonPaceMs: 50}
	first := p.peakForecast(f, 0)
	assert.InDelta(t, 100000, first, 1e-6)
	// 100 steps x 50 ms = 5 s later the KV turned out to be 150,000.
	f2 := &instanceFlux{id: "x", kvLogical: 150000, nDecode: 0, horizonPaceMs: 50}
	second := p.peakForecast(f2, 6000)
	st := p.forecast.inst["x"]
	require.NotNil(t, st)
	assert.Positive(t, st.matured)
	assert.Greater(t, st.dev[nForecastPoints-1], 0.0)
	assert.Greater(t, second, 150000.0)
	assert.False(t, math.IsNaN(second))
}

func TestSlackProtectMatchesLedgerD2(t *testing.T) {
	// --fluidserve-slack-protect decides what ledger variant D2 predicted on the
	// same state (the own-budget test changes the shed path only, not feasibility).
	for _, tier := range []int{25, 50, 100} {
		for _, prompt := range []int{600, 4000, 9000} {
			ledger := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.shadowLedger = true
				c.memoryUsesPaceCap = true
			})
			slack := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
				c.slackProtect = true
				c.memoryUsesPaceCap = true
			})
			want := map[string]bool{}
			lc, _ := shadowCandidates(t, ledger, tier, prompt)
			for _, c := range lc {
				want[c.flux.id] = c.ledger[ledgerD2]
			}
			sc, _ := shadowCandidates(t, slack, tier, prompt)
			for _, c := range sc {
				assert.Equal(t, want[c.flux.id], c.feasible, "tier %d prompt %d on %s", tier, prompt, c.flux.id)
			}
		}
	}
}

func TestSlackProtectCapacityDoesNotDependOnTheHorizon(t *testing.T) {
	// The pace ceiling used by the memory test must not move with the horizon
	// once the horizon-spread prefill term is gone.
	got := map[int]float64{}
	for _, h := range []int{25, 100, 200} {
		p := fsPolicy(t, "25:decode:75,50:decode,100:decode", func(c *fluidserveConfig) {
			c.slackProtect = true
			c.horizonSteps = h
		})
		fill(p, "mixed", 50, 700, 40, 4000, 0)
		views := map[string]*instanceViewScheduling{
			"mixed": fsView(fsViewOpts{id: "mixed", decodeReqs: 40, decodeTokens: 200000,
				pendingPre: 4000, usedGpu: 180000, stepID: 5000}),
		}
		p.calculateMetrics("neutral", fsRequest("probe", 50, 5000, 600), views)
		got[h] = views["mixed"].schedulingCtx.fluidserveFlux.capKv
	}
	assert.InDelta(t, got[100], got[25], 1e-6)
	assert.InDelta(t, got[100], got[200], 1e-6)
}
