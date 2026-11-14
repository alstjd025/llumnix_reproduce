package policy

import (
	"fmt"
	"math"
	"strconv"
	"strings"

	"llumnix/pkg/metrics"

	"k8s.io/klog/v2"
)

// Shadow re-judging of v0.5 with horizon-free and budget-based variants
// (--fluidserve-shadow-variants, EXP-158). Decisions are unchanged: every
// candidate is judged once more per variant on the state evaluate() already
// built, and the counters and the per-request fsshadowv line say where and how
// the verdict would have differed.
//
// EXP-157 showed that changing several tests at once (v0.6-candidate2) made the
// tests interact through the state they produce: a gate that let residents
// spend their budget slack shrank the slack the prefill-interference test was
// judged against, and an arriving chat request judged at its own allowance
// (TTFT budget left over, spread over its tokens) could enter instances the
// 0.9 x nominal gate had kept it out of, which undid the class separation. The
// variants here therefore start from v0.5 and change one thing each, and none
// of them reintroduces either of those two elements.
//
// The decision-level counter that matters most for class separation is
// shadowv_spread: decisions where every instance already holding the class is
// refused while some instance without it is accepted. With affinity weight 1.0
// and the count metric, that is exactly when a route takes the class onto a new
// instance, so a variant that raises it is one that would spread the class.
//
// Limits: each variant is judged on the state v0.5's own decisions produced, so
// feedback through the state (what EXP-157 measured) is not visible here.

const (
	varSame = iota
	varG10
	varU
	varUG10
	varM0
	varM1
	varUM0
	nVariants
)

var variantNames = [nVariants]string{"same", "G1.0", "U", "U+G1.0", "M0", "M1", "U+M0"}

type variantOpts struct {
	util   float64 // gate utilisation; 0 = the deployed one (p.gateUtil(), 0.9 in v0.5)
	budget bool    // U: sustained step + marginal late-token test instead of the h-spread mean step
	peak   int     // KV from the peak forecast without arrivals: 0 off, 1 with z x error, 2 mean only
}

var variantSet = [nVariants]variantOpts{
	varSame: {},
	varG10:  {util: 1.0},
	varU:    {budget: true},
	varUG10: {util: 1.0, budget: true},
	varM0:   {peak: 1},
	varM1:   {peak: 2},
	varUM0:  {budget: true, peak: 1},
}

// fsLateTokenShare is the scorer's tolerance: ladder95 accepts a request with
// up to 5% of its tokens past their deadlines.
const fsLateTokenShare = 0.05

// variantJudge is evaluate()'s feasibility conjunction for the shipped v0.5
// path (slack protection, ladder gate and peak forecast off), with the changes
// in o. With o = variantSet[varSame] it must return c.feasible exactly; the
// counters check that on every candidate.
func (p *fluidserveDispatchPolicy) variantJudge(f *instanceFlux, req *fluidserveRequest,
	c *candidate, o variantOpts) bool {

	cost := p.costOf(req)
	kv := f.proj + cost
	switch o.peak {
	case 1:
		kv = f.vPeakZ + cost
	case 2:
		kv = f.vPeakMean + cost
	}
	memKv := kv
	if p.cfg.memLevelTest {
		memKv = f.kvLogical + cost
	}
	// The gate before evaluate() applied the deployed utilisation.
	util := o.util
	if util <= 0 {
		util = p.gateUtil()
	}
	gate := c.gateAfter / p.gateUtil() * util

	var unpredictable, overGate, overIncumbents bool
	capKv := f.capKv
	if !o.budget {
		// Outside U the pace tests keep v0.5's mean step on its own projection;
		// M0/M1 change the memory test only.
		mean := c.meanAfter
		unpredictable = math.IsInf(mean, 0)
		overGate = mean > gate
		overIncumbents = mean > c.incumbentAfter
		if util != p.gateUtil() {
			capKv = p.capacity.maxKvForAllowance(f.id, f.gateAllowance*util, f.nDecode,
				f.effectivePrefill, f.chunk, p.paceHorizonSteps())
		}
	} else {
		corr := p.capacity.correctionFor(f.id)
		sustained := corr * (p.capacity.decodeStepMs(kv, f.nDecode+1) + f.prefillDuty*f.horizonPaceMs)
		unpredictable = math.IsInf(sustained, 0)
		overGate = sustained > gate
		late, unpred := p.lateTokenTest(f, sustained, c.prefillCharge, corr)
		overIncumbents = late && p.cfg.incumbentBalance
		unpredictable = unpredictable || unpred
		capKv = p.sustainedMaxKv(f, f.gateAllowance*util)
	}
	memLimit := f.capMem
	if p.cfg.memoryUsesPaceCap {
		memLimit = math.Min(capKv, f.capMem)
	}
	overMemory := memKv > memLimit
	overDeadline := p.cfg.deadlineFeasible && c.missesTtftDeadline
	return !unpredictable && !overGate && !overIncumbents && !overMemory && !overDeadline
}

// stallMs is the prefill-interference delay of q queued prefill tokens: the
// extra time the chunk-carrying iterations take over a decode-only one.
func (p *fluidserveDispatchPolicy) stallMs(q, chunk, corr float64) (float64, bool) {
	if q <= 0 {
		return 0, false
	}
	pre := p.capacity.prefillStepMs(math.Min(q, chunk))
	if math.IsInf(pre, 0) {
		return 0, true
	}
	return corr * prefillSteps(q, chunk) * (pre - p.capacity.c0), false
}

// lateTokenTest asks whether placing a prompt of prefillCharge tokens would push
// any decoding resident past the ladder95 tolerance. Only the delay this
// placement ADDS is charged (the queue ahead of it is delayed by the residents
// whether or not it is admitted). A resident with budget slack s absorbs D up to
// s; beyond that it is late until it has made the deficit back at
// (nominal budget - sustained step) per token. Residents already behind, still
// waiting in the engine queue, or past saving are left out, as in v0.5.
func (p *fluidserveDispatchPolicy) lateTokenTest(f *instanceFlux, sustained, prefillCharge,
	corr float64) (late, unpredictable bool) {

	base, u1 := p.stallMs(f.pendingPrefill, f.chunk, corr)
	with, u2 := p.stallMs(f.pendingPrefill+prefillCharge, f.chunk, corr)
	if u1 || u2 {
		return false, true
	}
	d := math.Max(0, with-base)
	if d <= 0 {
		return false, false
	}
	for i := range f.live {
		r := &f.live[i]
		if r.unachievable || r.waiting || r.nominalMs <= 0 || math.IsInf(r.slackLadderMs, 0) {
			continue
		}
		s := r.slackLadderMs
		if s < 0 || d <= s {
			continue
		}
		rem := math.Max(r.remaining, 1)
		n := rem
		if gain := r.nominalMs - sustained; gain > 0 {
			n = math.Min(rem, math.Ceil((d-s)/gain))
		}
		if n > fsLateTokenShare*(float64(r.j)+rem) {
			return true, false
		}
	}
	return false, false
}

// variantPeak is the peak KV forecast of fluidserve_forecast.go without the
// arrival term, kept in its own state so it never touches the deployed
// forecast's error tracking. It returns the peak of the mean forecast and the
// peak of the forecast plus forecastZ (1.65 if unset) x the measured error.
func (p *fluidserveDispatchPolicy) variantPeak(f *instanceFlux, nowMs int64) (mean, withZ float64) {
	h := float64(p.cfg.horizonSteps)
	pace := f.horizonPaceMs
	if pace <= 0 {
		pace = p.capacity.decodeStepMs(f.kvLogical, f.nDecode)
	}
	z := p.cfg.forecastZ
	if z <= 0 {
		z = 1.65
	}
	var pred [nForecastPoints]float64
	for i, fr := range forecastFrac {
		tau := fr * h
		v := f.kvLogical + f.nDecode*tau - p.expectedRelease(f.live, tau)
		if v < 0 {
			v = 0
		}
		pred[i] = v
	}
	s := &p.vforecast
	s.mu.Lock()
	defer s.mu.Unlock()
	s.init()
	st := s.inst[f.id]
	if st == nil {
		st = &instanceForecast{lastMs: math.MinInt64 / 2}
		s.inst[f.id] = st
	}
	keep := st.pending[:0]
	for _, smp := range st.pending {
		all := true
		for i := range smp.dueMs {
			if smp.done[i] {
				continue
			}
			if nowMs >= smp.dueMs[i] {
				st.dev[i] += fsForecastDevGain * (math.Abs(f.kvLogical-smp.pred[i]) - st.dev[i])
				smp.done[i] = true
				st.matured++
			} else {
				all = false
			}
		}
		if !all {
			keep = append(keep, smp)
		}
	}
	st.pending = keep
	if nowMs-st.lastMs >= fsForecastSampleMs && len(st.pending) < 4096 {
		var smp forecastSample
		for i, fr := range forecastFrac {
			smp.dueMs[i] = nowMs + int64(fr*h*pace)
			smp.pred[i] = pred[i]
		}
		smp.done[0] = true
		st.pending = append(st.pending, smp)
		st.lastMs = nowMs
	}
	for i := range pred {
		mean = math.Max(mean, pred[i])
		withZ = math.Max(withZ, pred[i]+z*st.dev[i])
	}
	return mean, withZ
}

// variantJudgeCandidate fills c.variant and counts per-candidate disagreement
// with the real verdict (the same variant must never disagree).
func (p *fluidserveDispatchPolicy) variantJudgeCandidate(f *instanceFlux, req *fluidserveRequest,
	c *candidate) {
	if p.cfg.slackProtect || p.cfg.ladderGate || p.cfg.peakForecast {
		return // the variants are defined against the v0.5 path only
	}
	tier := strconv.Itoa(req.tier)
	for v := 0; v < nVariants; v++ {
		c.variant[v] = p.variantJudge(f, req, c, variantSet[v])
		if c.variant[v] != c.feasible {
			metrics.Counter("scheduler_fluidserve_shadowv_candidate_flips_total", metrics.Labels{
				{Name: "variant", Value: variantNames[v]}, {Name: "tier", Value: tier},
				{Name: "real", Value: strconv.FormatBool(c.feasible)},
			}).Inc()
		}
	}
	c.variantOn = true
}

// variantReq accumulates one request's re-judging across its pend attempts.
type variantReq struct {
	attempts     int
	first        [nVariants]int64 // waited ms at the first attempt with a feasible instance, -1 never
	last         [nVariants]bool
	tPend, tShed int
	lastMs       int64
}

const fsVariantLedgerMax = 20000

// variantCountDecision counts, per variant, whether any instance was feasible,
// whether the chosen instance would differ, and whether the decision would take
// the class onto an instance that does not hold it (spread), then updates the
// request's accumulator.
func (p *fluidserveDispatchPolicy) variantCountDecision(cands []candidate, req *fluidserveRequest) {
	var any, in, out [nVariants]bool
	real, realIn, realOut, hasClass, on := false, false, false, false, false
	for i := range cands {
		c := &cands[i]
		if c.sameCount > 0 {
			hasClass = true
		}
		if c.feasible {
			real = true
			if c.sameCount > 0 {
				realIn = true
			} else {
				realOut = true
			}
		}
		if !c.variantOn {
			continue
		}
		on = true
		for v := 0; v < nVariants; v++ {
			if c.variant[v] {
				any[v] = true
				if c.sameCount > 0 {
					in[v] = true
				} else {
					out[v] = true
				}
			}
		}
	}
	if !on {
		return
	}
	tier := strconv.Itoa(req.tier)
	if hasClass && !realIn && realOut {
		metrics.Counter("scheduler_fluidserve_shadowv_spread_total", metrics.Labels{
			{Name: "variant", Value: "real"}, {Name: "tier", Value: tier}}).Inc()
	}
	var realBest string
	if real {
		cp := make([]candidate, len(cands))
		copy(cp, cands)
		sortCandidates(cp, p.affinityWeight(), p.cfg.affinityMetric)
		realBest = cp[0].flux.id
	}
	for v := 0; v < nVariants; v++ {
		metrics.Counter("scheduler_fluidserve_shadowv_decision_total", metrics.Labels{
			{Name: "variant", Value: variantNames[v]}, {Name: "tier", Value: tier},
			{Name: "real", Value: strconv.FormatBool(real)},
			{Name: "shadow", Value: strconv.FormatBool(any[v])},
		}).Inc()
		if hasClass && !in[v] && out[v] {
			metrics.Counter("scheduler_fluidserve_shadowv_spread_total", metrics.Labels{
				{Name: "variant", Value: variantNames[v]}, {Name: "tier", Value: tier}}).Inc()
		}
		if real && any[v] {
			cp := make([]candidate, len(cands))
			copy(cp, cands)
			for i := range cp {
				cp[i].feasible = cp[i].variant[v]
			}
			sortCandidates(cp, p.affinityWeight(), p.cfg.affinityMetric)
			metrics.Counter("scheduler_fluidserve_shadowv_target_total", metrics.Labels{
				{Name: "variant", Value: variantNames[v]}, {Name: "tier", Value: tier},
				{Name: "changed", Value: strconv.FormatBool(cp[0].flux.id != realBest)},
			}).Inc()
		}
	}

	waited := req.nowMs - req.arrivedMs
	p.vledgerMu.Lock()
	defer p.vledgerMu.Unlock()
	if p.vledger == nil {
		p.vledger = map[string]*variantReq{}
	}
	r := p.vledger[req.id]
	if r == nil {
		if len(p.vledger) >= fsVariantLedgerMax {
			for id, old := range p.vledger {
				if req.nowMs-old.lastMs > 120000 {
					delete(p.vledger, id)
				}
			}
		}
		r = &variantReq{}
		for v := range r.first {
			r.first[v] = -1
		}
		p.vledger[req.id] = r
	}
	r.attempts++
	r.lastMs = req.nowMs
	for v := 0; v < nVariants; v++ {
		if any[v] && r.first[v] < 0 {
			r.first[v] = waited
		}
		r.last[v] = any[v]
	}
}

// canWaitPendingOnly is canWait with the first-token estimate built from the
// prefill actually queued, without the prefill expected to arrive over the
// horizon (variant T).
func (p *fluidserveDispatchPolicy) canWaitPendingOnly(best candidate, req *fluidserveRequest) bool {
	fc := *best.flux
	fc.effectivePrefill = fc.pendingPrefill
	fc.arrivingPrefill = 0
	est, own, queued := p.prefillEstimateParts(req, &fc)
	bc := best
	bc.flux = &fc
	bc.prefillMs, bc.prefillOwnMs, bc.prefillQueueMs = est, own, queued
	return p.canWait(bc, req)
}

// variantNoteWait judges variant T on a decision where no instance was feasible.
func (p *fluidserveDispatchPolicy) variantNoteWait(best candidate, req *fluidserveRequest, realPend bool) {
	t := p.cfg.enablePend && p.canWaitPendingOnly(best, req)
	name := func(b bool) string {
		if b {
			return "pend"
		}
		return "shed"
	}
	metrics.Counter("scheduler_fluidserve_shadowv_wait_total", metrics.Labels{
		{Name: "tier", Value: strconv.Itoa(req.tier)},
		{Name: "real", Value: name(realPend)}, {Name: "t", Value: name(t)},
	}).Inc()
	p.vledgerMu.Lock()
	defer p.vledgerMu.Unlock()
	if r := p.vledger[req.id]; r != nil {
		if t {
			r.tPend++
		} else {
			r.tShed++
		}
	}
}

// variantLog writes the request's fsshadowv line at its final decision (route
// or shed) and drops the accumulator. The prefix matches the runner's capture
// of "[Schedule] dispatch request" lines.
func (p *fluidserveDispatchPolicy) variantLog(req *fluidserveRequest, decision string) {
	p.vledgerMu.Lock()
	r := p.vledger[req.id]
	delete(p.vledger, req.id)
	p.vledgerMu.Unlock()
	if r == nil {
		return
	}
	var first, last strings.Builder
	for v := 0; v < nVariants; v++ {
		if v > 0 {
			first.WriteByte(',')
			last.WriteByte(',')
		}
		fmt.Fprintf(&first, "%s:%d", variantNames[v], r.first[v])
		b := 0
		if r.last[v] {
			b = 1
		}
		fmt.Fprintf(&last, "%s:%d", variantNames[v], b)
	}
	klog.Infof("[Schedule] dispatch request %s fsshadowv tier=%d decision=%s waited=%d attempts=%d "+
		"first=%s last=%s tpend=%d tshed=%d", req.id, req.tier, decision, req.nowMs-req.arrivedMs,
		r.attempts, first.String(), last.String(), r.tPend, r.tShed)
}
