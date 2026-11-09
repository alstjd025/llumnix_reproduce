package policy

import (
	"math"
	"sync"
)

// Horizon as an accuracy control (EXP-155).
//
// The planning horizon should set how far ahead the policy looks, and so how
// uncertain its forecast is, not which classes it admits. Two changes, each
// behind a flag that is off by default.
//
// --fluidserve-peak-forecast replaces the KV projection's single end point,
// kv + n*h - outflow(h), by the PEAK of an unbiased forecast over the horizon
// plus a measured spread:
//
//	KV(tau) = kv + n*tau + arrivalRate*tau*pace - release(tau)   tau in {0, h/4, h/2, 3h/4, h}
//	forecast = max over tau of KV(tau) + z * dev(tau)
//
// arrivalRate is an exponentially weighted average of the prompt tokens this
// instance has been sent (half-life --fluidserve-arrival-halflife-ms);
// release(tau) is the expected KV of residents finishing within tau
// iterations (the class length profile, no lower bound). dev(tau) is the
// average absolute error of this instance's own earlier forecasts at that
// offset, measured once each forecast comes due -- the way TCP tracks RTTVAR
// beside SRTT (RFC 6298). A longer horizon therefore looks further and adds
// the spread measured that far out; it no longer shifts the forecast. Offline
// (EXP-155): the old end point was optimistic by 0.1% of KV at h=25 and 20.8%
// at h=200; this form stays within -0.4..+1.8% at every h.
//
// --fluidserve-slack-protect replaces the two tests that spread an arriving
// prompt's prefill over the pace horizon -- the gate and the incumbent test --
// and the arriving request's own pace test, by tests that do not use the
// horizon: the sustained step after placement (decode plus the recurring
// prefill share the instance has been observed to carry) must fit the gate and
// the request's own budget, and the one-time stall of the queued prefill plus
// this prompt must fit inside the smallest non-negative ladder slack among the
// residents (first-token budget + i x per-token budget, the scorer's ladder).

const nForecastPoints = 5

var forecastFrac = [nForecastPoints]float64{0, 0.25, 0.5, 0.75, 1}

// fsForecastDevGain is the gain of the error average (RFC 6298's beta).
const fsForecastDevGain = 0.25

// fsForecastSampleMs bounds how often an instance's forecast is recorded for
// later scoring; statuses arrive every 500 ms, decisions far more often.
const fsForecastSampleMs = 250

type arrivalAvg struct {
	lastMs int64
	rate   float64 // prompt tokens per ms
}

type forecastSample struct {
	dueMs [nForecastPoints]int64
	pred  [nForecastPoints]float64
	done  [nForecastPoints]bool
}

type instanceForecast struct {
	dev     [nForecastPoints]float64
	pending []forecastSample
	lastMs  int64
	matured int64 // forecasts scored, for the gauge and tests
}

type forecastState struct {
	mu       sync.Mutex
	arrivals map[string]*arrivalAvg
	inst     map[string]*instanceForecast
}

func (s *forecastState) init() {
	if s.arrivals == nil {
		s.arrivals = map[string]*arrivalAvg{}
		s.inst = map[string]*instanceForecast{}
	}
}

func decayTo(a *arrivalAvg, nowMs int64, halfLifeMs float64) {
	if a.lastMs != 0 && nowMs > a.lastMs {
		a.rate *= math.Pow(0.5, float64(nowMs-a.lastMs)/halfLifeMs)
	}
	if nowMs > a.lastMs {
		a.lastMs = nowMs
	}
}

// noteArrivalRate adds a placement of `tokens` prompt tokens to the
// instance's arrival average. The average of an impulse train with weights
// 0.5^(age/T) normalised by its integral T/ln2 is a rate.
func (p *fluidserveDispatchPolicy) noteArrivalRate(id string, nowMs int64, tokens float64) {
	if tokens <= 0 {
		return
	}
	hl := p.cfg.arrivalHalfLifeMs
	s := &p.forecast
	s.mu.Lock()
	defer s.mu.Unlock()
	s.init()
	a := s.arrivals[id]
	if a == nil {
		a = &arrivalAvg{}
		s.arrivals[id] = a
	}
	decayTo(a, nowMs, hl)
	a.rate += tokens * math.Ln2 / hl
}

func (p *fluidserveDispatchPolicy) arrivalRate(id string, nowMs int64) float64 {
	s := &p.forecast
	s.mu.Lock()
	defer s.mu.Unlock()
	s.init()
	a := s.arrivals[id]
	if a == nil {
		return 0
	}
	decayTo(a, nowMs, p.cfg.arrivalHalfLifeMs)
	return a.rate
}

// expectedRelease is the mean KV of residents expected to finish within
// `steps` iterations (no lower bound: the spread term carries the margin).
func (p *fluidserveDispatchPolicy) expectedRelease(live []liveRequest, steps float64) float64 {
	if steps <= 0 {
		return 0
	}
	h := int(math.Round(steps))
	out := 0.0
	for _, r := range live {
		if r.waiting {
			continue
		}
		prof := p.lengths.forTier(r.tier)
		if prof == nil {
			continue
		}
		out += prof.completionProb(r.j, h) * r.kvTokens
	}
	return out
}

// peakForecast is the forecast KV for the coming horizon on flux f: the peak
// over the forecast points of the unbiased forecast plus z x its measured
// error at that offset. It also scores forecasts that have come due against
// the KV the instance holds now, and records this one for later scoring.
func (p *fluidserveDispatchPolicy) peakForecast(f *instanceFlux, nowMs int64) float64 {
	h := float64(p.cfg.horizonSteps)
	pace := f.horizonPaceMs
	if pace <= 0 {
		pace = p.capacity.decodeStepMs(f.kvLogical, f.nDecode)
	}
	rate := p.arrivalRate(f.id, nowMs)
	var pred [nForecastPoints]float64
	for i, fr := range forecastFrac {
		tau := fr * h
		v := f.kvLogical + f.nDecode*tau + rate*tau*pace - p.expectedRelease(f.live, tau)
		if v < 0 {
			v = 0
		}
		pred[i] = v
	}

	s := &p.forecast
	s.mu.Lock()
	defer s.mu.Unlock()
	s.init()
	st := s.inst[f.id]
	if st == nil {
		st = &instanceForecast{lastMs: math.MinInt64 / 2} // first forecast is always recorded
		s.inst[f.id] = st
	}
	// Score what has come due: the instance holds f.kvLogical now.
	keep := st.pending[:0]
	for _, smp := range st.pending {
		all := true
		for i := range smp.dueMs {
			if smp.done[i] {
				continue
			}
			if nowMs >= smp.dueMs[i] {
				err := math.Abs(f.kvLogical - smp.pred[i])
				st.dev[i] += fsForecastDevGain * (err - st.dev[i])
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
		smp.done[0] = true // offset 0 is the present, not a forecast
		st.pending = append(st.pending, smp)
		st.lastMs = nowMs
	}
	peak := 0.0
	for i := range pred {
		if v := pred[i] + p.cfg.forecastZ*st.dev[i]; v > peak {
			peak = v
		}
	}
	return peak
}

// slackTests is the horizon-free protection (--fluidserve-slack-protect). It
// returns the sustained step after placement and whether the one-time stall
// would exceed the residents' smallest non-negative ladder slack.
func (p *fluidserveDispatchPolicy) slackTests(f *instanceFlux, newKv, prefillCharge float64) (
	sustained float64, overIncumbents, unpredictable bool) {

	corr := p.capacity.correctionFor(f.id)
	sustained = corr * (p.capacity.decodeStepMs(newKv, f.nDecode+1) + f.prefillDuty*f.horizonPaceMs)
	q := f.pendingPrefill + prefillCharge
	stall := 0.0
	if q > 0 {
		pre := p.capacity.prefillStepMs(math.Min(q, f.chunk))
		if math.IsInf(pre, 0) {
			return sustained, false, true
		}
		stall = corr * prefillSteps(q, f.chunk) * (pre - p.capacity.c0)
	}
	if !p.cfg.incumbentBalance {
		return sustained, false, false
	}
	minSlack := math.Inf(1)
	for i := range f.live {
		if f.live[i].unachievable {
			continue
		}
		s := f.live[i].slackLadderMs
		if s < 0 {
			continue
		}
		if s < minSlack {
			minSlack = s
		}
	}
	return sustained, stall > minSlack, false
}

// sustainedMaxKv inverts the sustained step of slackTests in the KV term: the
// largest KV the instance may hold and still average at or below allowanceMs
// per iteration, given its decode batch and the recurring prefill share it has
// been observed to carry. Unlike maxKvForAllowance it has no term for queued
// prefill divided by the pace horizon, so the horizon does not enter it.
func (p *fluidserveDispatchPolicy) sustainedMaxKv(f *instanceFlux, allowanceMs float64) float64 {
	if math.IsInf(allowanceMs, 1) {
		return math.Inf(1)
	}
	m := p.capacity
	m.mu.RLock()
	c0, cKv, cN, corr := m.c0, m.cKv, m.cN, m.corrLocked(f.id)
	m.mu.RUnlock()
	if cKv <= 0 || corr <= 0 {
		return math.Inf(1)
	}
	return (allowanceMs/corr - c0 - cN*f.nDecode - f.prefillDuty*f.horizonPaceMs) / cKv
}
