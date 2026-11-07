package policy

import (
	"math"
	"strconv"

	"llumnix/pkg/metrics"
)

// The shadow judge (--fluidserve-shadow-horizon, EXP-149).
//
// The planning horizon h enters the feasibility test in three places that are
// different mechanisms:
//
//	projection  the KV the instance is projected to hold one horizon ahead:
//	            growth nDecode*h, minus the expected release over h
//	            (expectedOutflow). Feeds the memory test and the KV term of the
//	            pace estimate.
//	pace        the share of iterations that carry prefill, pendingChunks/h, in
//	            meanStepMs and in the pace ceiling capKv (maxKvForAllowance), plus
//	            the duration over which arriving prefill is anticipated
//	            (carriedPrefillTokens). Feeds the gate and incumbent tests.
//	charge      the future decode an arriving request is charged for,
//	            min(h, expected output) (costOf).
//
// Comparing two runs at different horizons cannot say which of the three made
// a difference, because each run admits different requests and so reaches
// different states; every gauge then differs for reasons that are partly the
// horizon and partly the state it produced. This judge holds the state fixed:
// for each candidate the real decision evaluates, it re-runs the same test with
// h replaced, in all three places and in each alone, and counts where the
// verdict differs. It reads the flux the decision read and writes only
// counters, so the decisions are those of the configured horizon.
//
// Variant "same" re-runs the test at the real horizon through this code path.
// It must agree with the real verdict; its disagreement count measures how far
// this re-implementation drifts from evaluate() (the capacity correction can
// move between the two calls), and bounds what the other variants can claim.
const (
	shadowSame = iota
	shadowAll
	shadowPace
	shadowProj
	shadowCharge
	nShadowVariants
)

var shadowNames = [nShadowVariants]string{"same", "all", "pace", "proj", "charge"}

// shadowSupported reports whether the configuration is one the judge
// re-implements. The alternatives to the default projection (oracle lengths,
// KV slope, flux off, arrivals term) each replace a term this file rebuilds,
// and judging them through the default form would count differences that are
// not the horizon's.
func (p *fluidserveDispatchPolicy) shadowSupported() bool {
	return !p.cfg.oracleFlux && !p.cfg.kvSlopeProjection && p.cfg.enableFlux &&
		p.cfg.arrivalHorizons == 0
}

type shadowVerdict struct {
	feasible                                     bool
	unpredictable, gate, incumbents, mem, paceKv bool
	meanAfter, headroomAfter                     float64
}

// shadowJudge re-runs evaluate()'s feasibility test on flux f for request req
// with the horizon set separately for the projection, the pace estimate and
// the charge. prefillCharge is the prompt work evaluate() charged this request
// on this instance (it does not depend on the horizon).
func (p *fluidserveDispatchPolicy) shadowJudge(f *instanceFlux, req *fluidserveRequest,
	c *candidate, prefillCharge float64, hProj, hPace, hCharge int) shadowVerdict {

	// Projection.
	inflow := f.nDecode * float64(hProj)
	outflow := p.expectedOutflow(f.live, hProj)
	proj := f.kvLogical + inflow + f.arrivals - outflow
	if proj < 0 {
		proj = 0
	}

	// Pace: the anticipated prefill, the current pace, the incumbents' tightest
	// remaining allowance (which excludes those already slower than that pace),
	// and the pace ceiling.
	horizonMs := float64(hPace) * f.horizonPaceMs
	arriving := p.carriedPrefillTokens(f.prefillDuty, horizonMs, f.chunk)
	eff := math.Max(f.pendingPrefill, arriving)
	meanStep := p.capacity.meanStepMs(f.id, f.kvLogical, f.nDecode, eff, f.chunk, hPace)
	tightest := math.Inf(1)
	for i := range f.live {
		if f.live[i].unachievable || f.live[i].allowanceMs < meanStep {
			continue
		}
		if f.live[i].allowanceMs < tightest {
			tightest = f.live[i].allowanceMs
		}
	}
	if !p.cfg.incumbentBalance {
		tightest = math.Inf(1)
	}
	capKv := p.capacity.maxKvForAllowance(
		f.id, f.gateAllowance*fsAllowanceUtilisation, f.nDecode, eff, f.chunk, hPace)

	// Charge.
	cost := float64(req.promptTokens) + math.Min(float64(hCharge), req.expectedToks)

	newPending := eff + prefillCharge
	newKv := proj + cost
	memKv := newKv
	if p.cfg.memLevelTest {
		memKv = f.kvLogical + cost
	}
	v := shadowVerdict{}
	v.meanAfter = p.capacity.meanStepMs(f.id, newKv, f.nDecode+1, newPending, f.chunk, hPace)
	v.headroomAfter = math.Min(capKv, f.capMem) - newKv
	v.unpredictable = math.IsInf(v.meanAfter, 0)
	v.gate = v.meanAfter > c.gateAfter
	v.incumbents = v.meanAfter > tightest
	memLimit := f.capMem
	if p.cfg.memoryUsesPaceCap {
		memLimit = math.Min(capKv, f.capMem)
	}
	if memKv > memLimit {
		v.mem = memKv > f.capMem
		v.paceKv = p.cfg.memoryUsesPaceCap && memKv > capKv
	}
	// The first-token deadline does not depend on the horizon; take the real one.
	overDeadline := p.cfg.deadlineFeasible && c.missesTtftDeadline
	v.feasible = !v.unpredictable && !v.gate && !v.incumbents && !(v.mem || v.paceKv) &&
		!overDeadline
	return v
}

// shadowJudgeCandidate fills c.shadow and counts, per variant and class,
// whether the verdict agrees with the real one and which test failed.
func (p *fluidserveDispatchPolicy) shadowJudgeCandidate(f *instanceFlux,
	req *fluidserveRequest, c *candidate, newPending float64) {

	if !p.shadowSupported() {
		return
	}
	h := p.cfg.horizonSteps
	hp := p.paceHorizonSteps()
	hc := p.cfg.chargeHorizon
	if hc <= 0 {
		hc = h
	}
	s := p.cfg.shadowHorizon
	sc := s
	if p.cfg.chargeHorizon > 0 {
		// A separately set charge window is not the planning horizon; leave it.
		sc = hc
	}
	prefillCharge := newPending - f.effectivePrefill
	sp := s
	if p.cfg.paceHorizon > 0 {
		// A separately set pace horizon is not the planning horizon; leave it.
		sp = hp
	}
	set := [nShadowVariants][3]int{
		shadowSame:   {h, hp, hc},
		shadowAll:    {s, sp, sc},
		shadowPace:   {h, s, hc},
		shadowProj:   {s, hp, hc},
		shadowCharge: {h, hp, sc},
	}
	tier := strconv.Itoa(req.tier)
	real := strconv.FormatBool(c.feasible)
	for i := 0; i < nShadowVariants; i++ {
		v := p.shadowJudge(f, req, c, prefillCharge, set[i][0], set[i][1], set[i][2])
		c.shadow[i] = v.feasible
		name := shadowNames[i]
		metrics.Counter("scheduler_fluidserve_shadow_candidate_total", metrics.Labels{
			{Name: "variant", Value: name}, {Name: "tier", Value: tier},
			{Name: "real", Value: real}, {Name: "shadow", Value: strconv.FormatBool(v.feasible)},
		}).Inc()
		for _, r := range []struct {
			on   bool
			name string
		}{{v.unpredictable, "unpredictable"}, {v.gate, "gate"}, {v.incumbents, "incumbents"},
			{v.mem, "memory"}, {v.paceKv, "pace_kv"}} {
			if r.on {
				metrics.Counter("scheduler_fluidserve_shadow_infeasible_total", metrics.Labels{
					{Name: "variant", Value: name}, {Name: "reason", Value: r.name},
					{Name: "tier", Value: tier},
				}).Inc()
			}
		}
		if !math.IsInf(v.meanAfter, 0) && !math.IsInf(c.meanAfter, 0) {
			metrics.Histogram("scheduler_fluidserve_shadow_meanafter_delta_ms", metrics.Labels{
				{Name: "variant", Value: name}, {Name: "tier", Value: tier},
			}).Observe(v.meanAfter - c.meanAfter)
		}
		if !math.IsInf(v.headroomAfter, 0) && !math.IsInf(c.headroomAfter, 0) {
			metrics.Histogram("scheduler_fluidserve_shadow_headroom_delta_tokens", metrics.Labels{
				{Name: "variant", Value: name}, {Name: "tier", Value: tier},
			}).Observe(v.headroomAfter - c.headroomAfter)
		}
	}
	c.shadowOn = true
}

// shadowCountDecision counts, per variant and class, whether the request had a
// feasible placement among the candidates the decision actually considered
// (after the class pin and the instance cap), really and in the shadow.
func (p *fluidserveDispatchPolicy) shadowCountDecision(cands []candidate, req *fluidserveRequest) {
	real := false
	var shadow [nShadowVariants]bool
	on := false
	for i := range cands {
		real = real || cands[i].feasible
		if !cands[i].shadowOn {
			continue
		}
		on = true
		for v := 0; v < nShadowVariants; v++ {
			shadow[v] = shadow[v] || cands[i].shadow[v]
		}
	}
	if !on {
		return
	}
	tier := strconv.Itoa(req.tier)
	for v := 0; v < nShadowVariants; v++ {
		metrics.Counter("scheduler_fluidserve_shadow_decision_total", metrics.Labels{
			{Name: "variant", Value: shadowNames[v]}, {Name: "tier", Value: tier},
			{Name: "real", Value: strconv.FormatBool(real)},
			{Name: "shadow", Value: strconv.FormatBool(shadow[v])},
		}).Inc()
	}
}
