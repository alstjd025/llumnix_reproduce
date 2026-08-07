package policy

import (
	"math"
	"testing"
)

func chargeReq(prompt int, hashes []uint64) *fluidserveRequest {
	return &fluidserveRequest{id: "r", tier: 50, promptTokens: prompt, promptHashes: hashes}
}

// The property that makes this change safe to deploy: when the index has nothing
// to say -- the flag is off, or the gateway forwarded no token ids, or the
// prompt is shorter than a block -- the charge is exactly what it was before.
func TestPrefillChargeFallsBackToTheFleetFraction(t *testing.T) {
	p := fsPolicy(t, "50:decode", nil)
	p.capacity.prefillFraction = 0.2

	if got := p.prefillChargeFor(chargeReq(1000, nil), "A"); math.Abs(got-200) > 1e-9 {
		t.Fatalf("with the flag off the charge should be prompt x fraction = 200, got %.3f", got)
	}

	// Flag on, index present, but the request carries no hashes: same answer.
	p.cfg.prefixAware = true
	p.cfg.prefixCalibrate = true
	p.prefix = newPrefixIndex(4, 100)
	if got := p.prefillChargeFor(chargeReq(1000, nil), "A"); math.Abs(got-200) > 1e-9 {
		t.Fatalf("a request with no hashes should be charged 200, got %.3f", got)
	}

	// Flag on, hashes present, but nothing was ever dispatched anywhere.
	h := hashPrompt(seq(0, 40), 4)
	if got := p.prefillChargeFor(chargeReq(1000, h), "A"); math.Abs(got-200) > 1e-9 {
		t.Fatalf("an empty index should charge the whole prompt, got %.3f", got)
	}
}

// The charge has to differ BETWEEN instances for the same request; that
// difference is the entire point of the change, and the fleet-wide fraction it
// replaces could not express it.
func TestPrefillChargeIsPerInstance(t *testing.T) {
	p := fsPolicy(t, "50:decode", func(c *fluidserveConfig) {
		c.prefixAware = true
		c.prefixCalibrate = false // isolate the token count from the calibration
	})
	p.prefix = newPrefixIndex(4, 1000)
	p.capacity.prefillFraction = 0.2

	h := hashPrompt(seq(0, 40), 4) // 10 blocks, 40 tokens of a 1000-token prompt
	p.prefix.note(h, "A")

	onA := p.prefillChargeFor(chargeReq(1000, h), "A")
	onB := p.prefillChargeFor(chargeReq(1000, h), "B")
	if math.Abs(onA-960) > 1e-9 {
		t.Fatalf("A holds 40 tokens of the prompt, so the charge is 960, got %.3f", onA)
	}
	if math.Abs(onB-1000) > 1e-9 {
		t.Fatalf("B holds none of it, so the charge is the whole prompt, got %.3f", onB)
	}
	if onA >= onB {
		t.Fatal("the instance holding the prefix must be cheaper, which is the whole mechanism")
	}
}

// A hit longer than the prompt would make the charge negative and hand out free
// capacity. It cannot happen from a correct index, but the arithmetic is on the
// admission path and a negative charge is the failure that admits without limit.
func TestPrefillChargeNeverGoesNegative(t *testing.T) {
	p := fsPolicy(t, "50:decode", func(c *fluidserveConfig) {
		c.prefixAware = true
		c.prefixCalibrate = false
	})
	p.prefix = newPrefixIndex(16, 1000)
	h := hashPrompt(seq(0, 320), 16) // 20 blocks = 320 tokens
	p.prefix.note(h, "A")

	// The recorded prompt length is smaller than what the hashes cover.
	if got := p.prefillChargeFor(chargeReq(100, h), "A"); got != 0 {
		t.Fatalf("the charge must clamp at zero, got %.3f", got)
	}
}

// With the calibration on, the charge is the residual-corrected token count. The
// two switches therefore have to compose rather than one overriding the other.
func TestPrefillChargeAppliesTheCalibration(t *testing.T) {
	p := fsPolicy(t, "50:decode", func(c *fluidserveConfig) {
		c.prefixAware = true
		c.prefixCalibrate = true
	})
	p.prefix = newPrefixIndex(4, 1000)
	p.capacity.prefillFraction = 1.5 // the index claimed more hits than the engine had

	h := hashPrompt(seq(0, 40), 4)
	p.prefix.note(h, "A")
	got := p.prefillChargeFor(chargeReq(1000, h), "A")
	if math.Abs(got-960*1.5) > 1e-6 {
		t.Fatalf("want (1000-40) x 1.5 = 1440, got %.3f", got)
	}
}

// The calibration means two different things depending on which denominator fed
// it, and the bounds have to follow. As a residual it must be allowed above 1,
// because above 1 is exactly the case where the index claimed hits the engine did
// not have -- the failure this scheduler cannot otherwise see.
func TestNotePrefillResidualMayExceedOne(t *testing.T) {
	m := testCapacity(t)
	m.prefillFraction = 1.0

	// Engine computed far more than the charge predicted. Feed it enough times
	// for the smoothing to move.
	for i := 0; i < 2000; i++ {
		m.notePrefill(20.0, 10.0, 100, 8192, 2000, true)
	}
	if m.prefillFractionOf() <= 1.0 {
		t.Fatalf("a residual above 1 must be representable, got %.3f", m.prefillFractionOf())
	}
	if m.prefillFractionOf() > fsPrefillResidualMax+1e-9 {
		t.Fatalf("residual should be bounded at %.2f, got %.3f",
			fsPrefillResidualMax, m.prefillFractionOf())
	}

	// The same measurement in the non-residual sense is a share of a prompt and
	// is still capped at 1.
	m2 := testCapacity(t)
	m2.prefillFraction = 1.0
	for i := 0; i < 2000; i++ {
		m2.notePrefill(20.0, 10.0, 100, 8192, 2000, false)
	}
	if m2.prefillFractionOf() > fsPrefillFractionMax+1e-9 {
		t.Fatalf("a share of a prompt cannot exceed 1, got %.3f", m2.prefillFractionOf())
	}
}

// End to end through the decision path: the instance holding the prefix must
// come out of evaluate() with a lower predicted iteration time and therefore a
// wider margin against both feasibility conditions. This is the mechanism the
// whole change rests on, and it is checked here rather than inferred from the
// charge alone because the charge reaches the decision through meanStepMs.
func TestEvaluateIsCheaperOnTheInstanceHoldingThePrefix(t *testing.T) {
	p := fsPolicy(t, "50:decode", func(c *fluidserveConfig) {
		c.prefixAware = true
		c.prefixCalibrate = false
	})
	p.prefix = newPrefixIndex(16, 100000)

	// Two instances in identical states, so the only difference between the two
	// candidates can be the prefix.
	mk := func(id string) *instanceFlux {
		v := fsView(fsViewOpts{id: id, decodeReqs: 40, decodeTokens: 40000,
			stepID: 1000, stepDurationS: 0.04})
		return p.buildFlux(v, 1_000_000, 2)
	}
	fa, fb := mk("A"), mk("B")
	if fa == nil || fb == nil {
		t.Fatal("could not build the instance state")
	}

	h := hashPrompt(seq(0, 6400), 16) // 400 blocks = 6,400 tokens, an agent prompt
	p.prefix.note(h, "A")
	req := chargeReq(6400, h)
	req.nominalMs = 50
	req.expectedToks = 700

	va := fsView(fsViewOpts{id: "A", decodeReqs: 40, decodeTokens: 40000,
		stepID: 1000, stepDurationS: 0.04})
	vb := fsView(fsViewOpts{id: "B", decodeReqs: 40, decodeTokens: 40000,
		stepID: 1000, stepDurationS: 0.04})
	ca := p.evaluate(fa, va, req)
	cb := p.evaluate(fb, vb, req)

	if ca.prefillCharge != 0 {
		t.Fatalf("A holds the whole prompt, so it should be charged nothing, got %.1f",
			ca.prefillCharge)
	}
	if cb.prefillCharge != 6400 {
		t.Fatalf("B holds none of it, so it should be charged 6400, got %.1f",
			cb.prefillCharge)
	}
	if !(ca.meanAfter < cb.meanAfter) {
		t.Fatalf("the cached instance must predict a faster iteration: %.3f vs %.3f",
			ca.meanAfter, cb.meanAfter)
	}
	if !(ca.prefillMs < cb.prefillMs) {
		t.Fatalf("the cached instance must predict a shorter time to first token: "+
			"%.1f vs %.1f", ca.prefillMs, cb.prefillMs)
	}
}
