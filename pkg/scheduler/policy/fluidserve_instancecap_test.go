package policy

import (
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// ---------------------------------------------------------------------------
// tierServiceRate: the demand-to-instances conversion behind the cap
// ---------------------------------------------------------------------------

func TestTierServiceRateChargesPrefill(t *testing.T) {
	m := testCapacity(t)
	// A deepresearch-like class: 4,055 prompt tokens, 985 output tokens, 90 ms
	// allowance. The fixture's prefill law gives 520*chunk/8192 ms per chunk.
	mu := m.tierServiceRate(90, 4055, 985, 2048, 0)
	require.Greater(t, mu, 0.0)

	// The rate must sit below the prefill-only ceiling: even with zero decode
	// work, one instance cannot prefill more than 1000/prefillMsPerReq
	// requests per second. The decode-only closed form violated this by 1.65x
	// for this class, which is the defect this term exists to fix.
	chunks := prefillSteps(4055, 2048)
	prefillMsPerReq := chunks * m.prefillStepMs(2048)
	ceiling := 1000.0 / prefillMsPerReq
	assert.Less(t, mu, ceiling,
		"service rate above the prefill-only ceiling is physically impossible")

	// Dropping the prompt (same KV footprint kept via the output term) must
	// raise the rate: prefill work can only cost capacity.
	noPrefill := m.tierServiceRate(90, 0, 985, 2048, 0)
	assert.Greater(t, noPrefill, mu)
}

func TestTierServiceRateDegenerateInputs(t *testing.T) {
	m := testCapacity(t)
	assert.Zero(t, m.tierServiceRate(0, 674, 428, 2048, 0), "no allowance")
	assert.Zero(t, m.tierServiceRate(10, 674, 428, 2048, 0),
		"allowance below the per-step floor c0=16 cannot be served")
	assert.Zero(t, m.tierServiceRate(45, 674, 0.5, 2048, 0), "no output length")
	// The KV-pool clamp binds when the pool is small.
	free := m.tierServiceRate(45, 674, 428, 2048, 0)
	clamped := m.tierServiceRate(45, 674, 428, 2048, 1000)
	assert.Less(t, clamped, free)
}

// ---------------------------------------------------------------------------
// capLimitFor: floor and hysteresis
// ---------------------------------------------------------------------------

func TestCapLimitFloorsAtOne(t *testing.T) {
	st := &tierArrivalState{}
	assert.Equal(t, 1, capLimitFor(st, 0),
		"a class with a request in hand may always hold one instance")
	assert.Equal(t, 1, capLimitFor(st, 0.4))
	assert.Equal(t, 2, capLimitFor(st, 1.7))
}

func TestCapLimitHysteresisAtIntegerBoundary(t *testing.T) {
	// The mix-shift trace parks swe's demand at 0.995 instances; estimator
	// noise crosses 1.0 repeatedly and must not flip the limit each time.
	st := &tierArrivalState{prevLimit: 1}
	assert.Equal(t, 1, capLimitFor(st, 1.02), "inside the raise margin")
	assert.Equal(t, 2, capLimitFor(st, 1.2), "a real excursion raises")
	st.prevLimit = 2
	assert.Equal(t, 2, capLimitFor(st, 1.6), "ceil keeps it at 2")
	assert.Equal(t, 1, capLimitFor(st, 0.8), "a real drop lowers")
}

// ---------------------------------------------------------------------------
// applyInstanceCap: the filter itself
// ---------------------------------------------------------------------------

// capFlux builds an instanceFlux the way buildFlux would have left it for
// these tests: a gate tier with its nominal, n residents of that tier, and
// the model inputs the service-rate call reads.
func capFlux(id string, gateTier int, gateNominal float64, residents int) *instanceFlux {
	f := &instanceFlux{
		id:              id,
		chunk:           2048,
		capMem:          5_000_000,
		gateTier:        gateTier,
		gateTierNominal: gateNominal,
	}
	if gateTier == 0 {
		f.gateTierNominal = math.Inf(1)
	}
	for i := 0; i < residents; i++ {
		f.live = append(f.live, liveRequest{tier: gateTier, nominalMs: gateNominal})
	}
	return f
}

func capPolicy(t *testing.T) *fluidserveDispatchPolicy {
	t.Helper()
	return fsPolicy(t, "25:e2e:30000,50:decode,100:decode", func(c *fluidserveConfig) {
		c.classInstanceCap = true
		c.capWindowMult = 3.0
	})
}

func candsOf(fluxes ...*instanceFlux) []candidate {
	out := make([]candidate, 0, len(fluxes))
	for _, f := range fluxes {
		out = append(out, candidate{flux: f, feasible: true})
	}
	return out
}

func idsOf(cands []candidate) []string {
	out := make([]string, 0, len(cands))
	for i := range cands {
		out = append(out, cands[i].flux.id)
	}
	return out
}

func TestInstanceCapDrainsSurplusGateHolders(t *testing.T) {
	p := capPolicy(t)
	// Demand for tier 50 sized to a limit of 2 (mu is ~14.5 req/s for the
	// fixture's 200-token class at 674 prompt tokens; 25 req/s is 1.7
	// instances). Tier 100 has demand for an instance and holds none, so the
	// guardrail clause is armed: holding past one's demand is taking from it.
	p.tierArr[50] = &tierArrivalState{lambda: 25, meanPrompt: 674, ready: true}
	p.tierArr[100] = &tierArrivalState{lambda: 2, meanPrompt: 4055, ready: true}

	cands := candsOf(
		capFlux("a", 50, 50, 10), // most residents: designated
		capFlux("b", 50, 50, 5),  // second: designated
		capFlux("c", 50, 50, 1),  // surplus gate holder: must drain
		capFlux("d", 0, 0, 0),    // empty: a new gate, refused at the limit
	)
	got := p.applyInstanceCap(cands, &fluidserveRequest{tier: 50, nominalMs: 50})
	assert.Equal(t, []string{"a", "b"}, idsOf(got),
		"at the limit with a starved class present, only the most-loaded gate "+
			"holders remain; the surplus holder drains and the empty instance "+
			"is left for the class that lacks one")
}

func TestInstanceCapDoesNotBindWithoutAVictim(t *testing.T) {
	// The guardrail clause. Same fleet as the drain test, but the only other
	// class with arrivals already holds the one instance its demand asks for.
	// Nobody is short, so spreading costs nothing and nothing is filtered --
	// refusing it would only manufacture rejections.
	p := capPolicy(t)
	p.tierArr[50] = &tierArrivalState{lambda: 25, meanPrompt: 674, ready: true}
	p.tierArr[100] = &tierArrivalState{lambda: 2, meanPrompt: 4055, ready: true}
	cands := candsOf(
		capFlux("a", 50, 50, 10),
		capFlux("b", 50, 50, 5),
		capFlux("c", 50, 50, 1),
		capFlux("d", 100, 100, 3), // tier 100 holds its instance: satisfied
	)
	got := p.applyInstanceCap(cands, &fluidserveRequest{tier: 50, nominalMs: 50})
	assert.Len(t, got, 4, "no starved class, no filtering")
}

func TestInstanceCapAllowsRidingOnTighterGates(t *testing.T) {
	p := capPolicy(t)
	// Demand safely mid-interval: mu for this class is ~3 req/s, so 5 req/s is
	// ~1.7 instances and the limit is 2 without sitting on an integer boundary.
	p.tierArr[100] = &tierArrivalState{lambda: 5, meanPrompt: 4055, ready: true}
	// Tier 50 wants two instances and holds one, so the guardrail is armed
	// against tier 100 spreading further.
	p.tierArr[50] = &tierArrivalState{lambda: 25, meanPrompt: 674, ready: true}
	// Two tier-100 gate holders fill the limit; the instance gated tighter by
	// tier 50 stays a legal destination anyway, because placing 100 ms work
	// there cannot move a 50 ms gate.
	tight := capFlux("tight", 50, 50, 8)
	own := capFlux("own", 100, 100, 4)
	own2 := capFlux("own2", 100, 100, 2)
	empty := capFlux("empty", 0, 0, 0)
	got := p.applyInstanceCap(candsOf(tight, own, own2, empty),
		&fluidserveRequest{tier: 100, nominalMs: 100})
	assert.ElementsMatch(t, []string{"tight", "own", "own2"}, idsOf(got),
		"riding on a tighter gate is never a new gate; only the empty instance is refused")
}

func TestInstanceCapBelowLimitFiltersNothing(t *testing.T) {
	p := capPolicy(t)
	// Tiny demand: limit floors at 1, and with zero current gate holders the
	// class may open its first gate anywhere.
	p.tierArr[50] = &tierArrivalState{lambda: 0.1, meanPrompt: 674, ready: true}
	cands := candsOf(capFlux("d", 0, 0, 0), capFlux("e", 100, 100, 3))
	got := p.applyInstanceCap(cands, &fluidserveRequest{tier: 50, nominalMs: 50})
	assert.Len(t, got, 2)
}

func TestInstanceCapFailsOpenBeforeFirstBucket(t *testing.T) {
	p := capPolicy(t)
	cands := candsOf(capFlux("a", 50, 50, 1), capFlux("d", 0, 0, 0))
	got := p.applyInstanceCap(cands, &fluidserveRequest{tier: 50, nominalMs: 50})
	assert.Len(t, got, 2, "no demand estimate yet: the cap must not act")
}

func TestInstanceCapDoesNotRescaleUnderScarcity(t *testing.T) {
	// The EXP-107 rep-1 revision. Summed demand past the fleet size is the
	// NORMAL loaded state on this workload (the heavy classes' low
	// per-instance rates put it at 12-14 on a fleet of 4 all hour), and the
	// proportional rescale therefore ran a standing partition -- chat pinned
	// to ceil(4*2/14) = 1 instance against 10-30 req/s of arrivals. The limit
	// is the class's OWN demand, whatever the others sum to; scarcity is
	// admission control's job.
	p := capPolicy(t)
	// Tier 50 demands ~2.5 instances (limit 3); tier 100 demands ~2.5 as well
	// and holds one, so it is starved and the guardrail binds -- but at tier
	// 50's own limit of 3, not at a rescaled share of the fleet.
	p.tierArr[50] = &tierArrivalState{lambda: 36, meanPrompt: 674, ready: true}
	p.tierArr[100] = &tierArrivalState{lambda: 7.5, meanPrompt: 4055, ready: true}
	cands := candsOf(
		capFlux("a", 50, 50, 9),
		capFlux("b", 50, 50, 7),
		capFlux("c", 50, 50, 2),
		capFlux("d", 100, 100, 5),
	)
	got := p.applyInstanceCap(cands, &fluidserveRequest{tier: 50, nominalMs: 50})
	assert.Equal(t, []string{"a", "b", "c"}, idsOf(got),
		"the limit is the class's own demand (3), not a fleet share (2): all "+
			"three holders stay, and only new gates are refused")
}

func TestInstanceCapPinnedTierBypasses(t *testing.T) {
	p := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
		c.classInstanceCap = true
		c.capWindowMult = 3.0
		c.classPin = map[int][]int{50: {0}}
	})
	p.tierArr[50] = &tierArrivalState{lambda: 25, meanPrompt: 674, ready: true}
	cands := candsOf(capFlux("a", 50, 50, 3), capFlux("d", 0, 0, 0))
	got := p.applyInstanceCap(cands, &fluidserveRequest{tier: 50, nominalMs: 50})
	assert.Len(t, got, 2, "an explicit pin is the more explicit statement and wins")
}

// ---------------------------------------------------------------------------
// The force switch
// ---------------------------------------------------------------------------

func TestForceOffRejectsWhatForceWouldPlace(t *testing.T) {
	// The population the force branch serves lives in the safety-margin band:
	// the predicted pace after placement clears the request's own budget but
	// not the margined gate. Here the instance's predicted mean lands at
	// ~47 ms -- above the 45 ms gate (50 x 0.90), inside the 50 ms budget --
	// so the request is infeasible everywhere yet predicted to meet its own
	// budget. With the branch on it is placed anyway; with it off it must come
	// back as an immediate, explicit rejection -- not another wait, which the
	// gateway would retry to its ceiling.
	build := func(force bool) (*fluidserveDispatchPolicy, map[string]*instanceViewScheduling) {
		p := fsPolicy(t, "25:e2e:30000,50:decode", func(c *fluidserveConfig) {
			c.enablePend = false
			c.enableForce = force
		})
		views := map[string]*instanceViewScheduling{
			"a": fsView(fsViewOpts{id: "a", decodeReqs: 20,
				decodeTokens: 2_850_000, usedGpu: 200_000, stepID: 5000}),
		}
		return p, views
	}

	p, views := build(true)
	require.NotNil(t, decide(p, fsRequest("chat-new", 50, 5000, 1000), views),
		"with force on, the margin-band request is placed")

	p, views = build(false)
	assert.Nil(t, decide(p, fsRequest("chat-new", 50, 5000, 1000), views))
	assert.True(t, p.admissionRejected("chat-new"),
		"with force off, the same request is an explicit early rejection")
}

// ---------------------------------------------------------------------------
// gateTighteningCost: what a class costs an instance by gating it
// ---------------------------------------------------------------------------

// The fixture's cKv is 1e-5 ms per token and its correction is 1.0, so one ms of
// allowance is worth 100,000 tokens of pace ceiling. Tightening 90 ms -> 45 ms
// therefore lowers capKv by 4,500,000.
const testGateStep = 45.0 / 1e-5 // tokens of capKv per 45 ms of allowance

func TestGateCostIsZeroWhileMemoryBinds(t *testing.T) {
	m := testCapacity(t)
	// capKv is 6M now and 1.5M after; capMem is 1M, below both. The instance
	// admits 1M either way, so the class costs it nothing by gating it. This is
	// the eight-instance case: measured capKv 9.6-10.3M against capMem 1.3M.
	cost, ok := m.gateTighteningCost("i", 6_000_000, 1_000_000, 90, 45)
	require.True(t, ok)
	assert.Zero(t, cost, "a tighter gate on a ceiling that does not bind costs nothing")
}

func TestGateCostIsTheLostCapacityWhenPaceBinds(t *testing.T) {
	m := testCapacity(t)
	// capKv 600k now, 600k-4.5M after, so negative; capMem 6M, above both. The
	// instance goes from admitting 600k to admitting nothing. This is the
	// four-instance case: measured capKv 397k-618k with the physical pool well
	// above it.
	cost, ok := m.gateTighteningCost("i", 600_000, 6_000_000, 90, 45)
	require.True(t, ok)
	assert.InDelta(t, 600_000+(testGateStep-600_000), cost, 1,
		"the whole pace ceiling is lost, and then some")
	assert.Greater(t, cost, 600_000.0)
}

func TestGateCostCrossesFromOneCeilingToTheOther(t *testing.T) {
	m := testCapacity(t)
	// capKv 2M now, capMem 1M: memory binds. After tightening capKv is
	// 2M-4.5M < 0, so the pace ceiling takes over and the instance loses the
	// whole 1M it could admit. The cost is the DIFFERENCE OF THE MINIMA, not of
	// the pace ceilings, which is the reason the test is written on min().
	cost, ok := m.gateTighteningCost("i", 2_000_000, 1_000_000, 90, 45)
	require.True(t, ok)
	assert.InDelta(t, 1_000_000-(2_000_000-testGateStep), cost, 1)
}

func TestGateCostRefusesToAnswerForAnInfiniteCeiling(t *testing.T) {
	m := testCapacity(t)
	// An instance with no live request has gateAllowance +Inf and therefore
	// capKv +Inf. Subtracting a finite step from +Inf is still +Inf, so the
	// linear form would report "costs nothing" for the one placement the cap
	// most wants to reason about. Refuse instead, and let the caller decide.
	_, ok := m.gateTighteningCost("i", math.Inf(1), 1_000_000, math.Inf(1), 45)
	assert.False(t, ok, "an infinite ceiling must not be extrapolated")
	_, ok = m.gateTighteningCost("i", math.Inf(-1), 1_000_000, 90, 45)
	assert.False(t, ok, "the unpredictable-prefill sentinel must not be extrapolated")
}

func TestGateCostIsZeroForALooserClass(t *testing.T) {
	m := testCapacity(t)
	// deepresearch landing on a chat-gated instance does not move the gate, so
	// there is nothing to charge. The cap already keeps these as "riding"; the
	// helper must agree rather than return something.
	cost, ok := m.gateTighteningCost("i", 600_000, 6_000_000, 45, 90)
	require.True(t, ok)
	assert.Zero(t, cost)
}

func TestGateCostNeverReportsCreatedCapacity(t *testing.T) {
	m := testCapacity(t)
	for _, tc := range []struct{ capKv, capMem float64 }{
		{6_000_000, 1_000_000}, {600_000, 6_000_000}, {2_000_000, 1_000_000},
		{1_000_000, 1_000_000}, {0, 1_000_000},
	} {
		cost, ok := m.gateTighteningCost("i", tc.capKv, tc.capMem, 90, 45)
		require.True(t, ok)
		assert.GreaterOrEqual(t, cost, 0.0,
			"capKv=%v capMem=%v", tc.capKv, tc.capMem)
	}
}

func TestGateCostUsesThePerInstanceCorrection(t *testing.T) {
	m := testCapacity(t)
	m.perInstance = true
	m.corrections["slow"] = 2.0 // this instance runs at twice the modelled time
	fleet, ok := m.gateTighteningCost("fleet", 600_000, 6_000_000, 90, 45)
	require.True(t, ok)
	slow, ok := m.gateTighteningCost("slow", 600_000, 6_000_000, 90, 45)
	require.True(t, ok)
	// A correction of 2 halves the tokens each ms of allowance is worth, because
	// maxKvForAllowance divides the allowance by the correction before inverting.
	// So the same tightening costs exactly half as much: 4,500,000 against
	// 2,250,000. tierServiceRate reads the fleet value and cannot make this
	// distinction; maxKvForAllowance already reads the per-instance one, and this
	// helper must match it.
	assert.InDelta(t, 4_500_000.0, fleet, 1)
	assert.InDelta(t, fleet/2, slow, 1)
}
