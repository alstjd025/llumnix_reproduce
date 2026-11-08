package policy

import (
	"math"
	"strconv"

	"llumnix/pkg/metrics"
)

// The shadow ledger (--fluidserve-shadow-ledger, EXP-153).
//
// The aim is to simplify the policy: find which mechanisms change decisions
// and which do not. For every candidate the real decision evaluates, the
// feasibility test of evaluate() is re-run on the same flux with mechanisms
// removed or replaced, singly and together, and the verdicts are counted per
// class and per the instance's gate. Nothing computed here reaches a decision.
//
//	A  no future-decode charge: an arriving request is charged its prompt only,
//	   not prompt + min(h, expected output).
//	B  no KV projection: the instance is judged on the KV it holds now instead
//	   of now + growth over the horizon - expected release (no outflow model).
//	C  no per-instance correction: the latency model is used as fitted.
//	D  slack-based protection, replacing the gate and the incumbent test:
//	   (sustained) the decode step after placement plus the recurring prefill
//	   share the instance has been observed to carry (prefill duty x pace) must
//	   fit the gate; (one-time) the extra time the queued prefill plus this
//	   prompt add to the iterations that carry them must fit inside the smallest
//	   slack among the residents still on track. D1 measures slack from decode
//	   start (the ladder allowanceMs is built on); D2 measures it from arrival
//	   with the first-token budget added (the scorer's ladder95).
//
// Removing mechanisms one at a time reads redundant ones as zero, so the
// combinations are counted as well. "same" removes nothing and must agree
// with the real verdict.
const (
	ledgerSame = iota
	ledgerA
	ledgerB
	ledgerC
	ledgerD1
	ledgerD2
	ledgerABC
	ledgerABCD2
	nLedgerVariants
)

var ledgerNames = [nLedgerVariants]string{"same", "A", "B", "C", "D1", "D2", "ABC", "ABCD2"}

type ledgerOpts struct {
	noCharge, level, noCorr bool
	slack                   int // 0 off, 1 decode slack, 2 ladder slack
}

var ledgerSet = [nLedgerVariants]ledgerOpts{
	ledgerSame:  {},
	ledgerA:     {noCharge: true},
	ledgerB:     {level: true},
	ledgerC:     {noCorr: true},
	ledgerD1:    {slack: 1},
	ledgerD2:    {slack: 2},
	ledgerABC:   {noCharge: true, level: true, noCorr: true},
	ledgerABCD2: {noCharge: true, level: true, noCorr: true, slack: 2},
}

func (p *fluidserveDispatchPolicy) ledgerJudge(f *instanceFlux, req *fluidserveRequest,
	c *candidate, prefillCharge float64, o ledgerOpts) bool {

	h := p.cfg.horizonSteps
	hp := p.paceHorizonSteps()
	hc := p.cfg.chargeHorizon
	if hc <= 0 {
		hc = h
	}
	corr := p.capacity.correctionFor(f.id)
	// meanStepMs and maxKvForAllowance apply the correction internally; with
	// it removed, undo it on the way out (and in on the way in for capKv).
	undo := 1.0
	if o.noCorr && corr > 0 {
		undo = corr
	}

	// Projection.
	proj := f.kvLogical
	if !o.level {
		proj = f.kvLogical + f.nDecode*float64(h) + f.arrivals - p.expectedOutflow(f.live, h)
		if proj < 0 {
			proj = 0
		}
	}

	// Pace state, rebuilt as buildFlux does.
	horizonMs := float64(hp) * f.horizonPaceMs
	arriving := p.carriedPrefillTokens(f.prefillDuty, horizonMs, f.chunk)
	eff := math.Max(f.pendingPrefill, arriving)
	meanStep := p.capacity.meanStepMs(f.id, f.kvLogical, f.nDecode, eff, f.chunk, hp) / undo
	capKv := p.capacity.maxKvForAllowance(
		f.id, f.gateAllowance*fsAllowanceUtilisation*undo, f.nDecode, eff, f.chunk, hp)

	cost := float64(req.promptTokens)
	if !o.noCharge {
		cost += math.Min(float64(hc), req.expectedToks)
	}
	newPending := eff + prefillCharge
	newKv := proj + cost
	memKv := newKv
	if p.cfg.memLevelTest {
		memKv = f.kvLogical + cost
	}
	meanAfter := p.capacity.meanStepMs(f.id, newKv, f.nDecode+1, newPending, f.chunk, hp) / undo
	unpredictable := math.IsInf(meanAfter, 0)

	var overGate, overIncumbents bool
	if o.slack == 0 {
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
		overGate = meanAfter > c.gateAfter
		overIncumbents = meanAfter > tightest
	} else {
		k := 1.0
		if !o.noCorr {
			k = corr
		}
		sustained := k * (p.capacity.decodeStepMs(newKv, f.nDecode+1) + f.prefillDuty*f.horizonPaceMs)
		overGate = sustained > c.gateAfter
		q := f.pendingPrefill + prefillCharge
		stall := 0.0
		if q > 0 {
			pre := p.capacity.prefillStepMs(math.Min(q, f.chunk))
			if math.IsInf(pre, 0) {
				unpredictable = true
			} else {
				stall = k * prefillSteps(q, f.chunk) * (pre - p.capacity.c0)
			}
		}
		minSlack := math.Inf(1)
		for i := range f.live {
			if f.live[i].unachievable {
				continue
			}
			s := f.live[i].slackDecodeMs
			if o.slack == 2 {
				s = f.live[i].slackLadderMs
			}
			if s < 0 {
				continue // already behind: refusing work does not rescue it
			}
			if s < minSlack {
				minSlack = s
			}
		}
		if !p.cfg.incumbentBalance {
			minSlack = math.Inf(1)
		}
		overIncumbents = stall > minSlack
	}
	memLimit := f.capMem
	if p.cfg.memoryUsesPaceCap {
		memLimit = math.Min(capKv, f.capMem)
	}
	overMemory := memKv > memLimit
	overDeadline := p.cfg.deadlineFeasible && c.missesTtftDeadline
	return !unpredictable && !overGate && !overIncumbents && !overMemory && !overDeadline
}

func ledgerGateLabel(f *instanceFlux) string {
	if math.IsInf(f.gateAllowance, 0) || f.gateAllowance <= 0 {
		return "none"
	}
	return strconv.Itoa(int(math.Round(f.gateAllowance)))
}

func (p *fluidserveDispatchPolicy) ledgerJudgeCandidate(f *instanceFlux,
	req *fluidserveRequest, c *candidate, newPending float64) {

	if !p.shadowSupported() {
		return
	}
	prefillCharge := newPending - f.effectivePrefill
	tier := strconv.Itoa(req.tier)
	gate := ledgerGateLabel(f)
	real := strconv.FormatBool(c.feasible)
	for i := 0; i < nLedgerVariants; i++ {
		v := p.ledgerJudge(f, req, c, prefillCharge, ledgerSet[i])
		c.ledger[i] = v
		metrics.Counter("scheduler_fluidserve_ledger_candidate_total", metrics.Labels{
			{Name: "variant", Value: ledgerNames[i]}, {Name: "tier", Value: tier},
			{Name: "gate", Value: gate}, {Name: "real", Value: real},
			{Name: "shadow", Value: strconv.FormatBool(v)},
		}).Inc()
	}
	c.ledgerOn = true
}

// ledgerCountDecision counts, per variant and class, whether the request had a
// feasible placement among the candidates the decision considered (after the
// class pin and the instance cap), really and in the ledger variant.
func (p *fluidserveDispatchPolicy) ledgerCountDecision(cands []candidate, req *fluidserveRequest) {
	real := false
	var shadow [nLedgerVariants]bool
	on := false
	for i := range cands {
		real = real || cands[i].feasible
		if !cands[i].ledgerOn {
			continue
		}
		on = true
		for v := 0; v < nLedgerVariants; v++ {
			shadow[v] = shadow[v] || cands[i].ledger[v]
		}
	}
	if !on {
		return
	}
	tier := strconv.Itoa(req.tier)
	for v := 0; v < nLedgerVariants; v++ {
		metrics.Counter("scheduler_fluidserve_ledger_decision_total", metrics.Labels{
			{Name: "variant", Value: ledgerNames[v]}, {Name: "tier", Value: tier},
			{Name: "real", Value: strconv.FormatBool(real)},
			{Name: "shadow", Value: strconv.FormatBool(shadow[v])},
		}).Inc()
	}
}
