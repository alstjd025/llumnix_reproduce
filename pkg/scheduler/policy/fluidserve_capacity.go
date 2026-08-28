package policy

import (
	"math"
	"sync"
)

// C3, the capacity model. It answers two questions for one instance:
//
//	meanStepMs(...)      how long an iteration will take on average over the
//	                     planning horizon, given the KV it holds, the number of
//	                     decoding requests, and the prefill work still queued
//	maxKvForAllowance()   the largest KV occupancy at which that average still
//	                     fits inside a given per-token time allowance
//
// The model does not predict a single iteration. It predicts the average over a
// horizon, because that is the quantity the SLO is written against: a request's
// score is its mean time between tokens over its whole life, so a step that runs
// long is only a violation to the extent that it raises that mean.
//
// Two measured components:
//
//	decode step   t_dec(kv, n) = c0 + c_kv*kv + c_n*n, fitted on 377,838
//	              decode-only steps from the EXP-16 dumps (R2 = 0.95).
//	prefill step  t_pre(chunk), read from the ttft.json table that was measured
//	              directly with an idle-engine prompt-length sweep.
//
// A step that carries a prefill chunk costs t_pre(chunk) + t_dec - c0: it runs
// the prefill GEMMs and the decode attention for every running request in the
// same forward pass, and pays the fixed per-step overhead once. Charging only
// t_pre understates measured window means by 35-40% once most steps carry a
// chunk, because ttft.json was measured with nothing else running. See
// ms_dev/notes/fluidserve-implementation.md for the validation table.

type capacityModel struct {
	mu sync.RWMutex

	c0  float64 // fixed per-step cost, ms
	cKv float64 // ms per KV token held
	cN  float64 // ms per decoding request

	predictor *LatencyPredictor

	// correction scales the predicted MEAN iteration time. See noteResidual.
	//
	// When perInstance is false this is the only correction there is, shared by
	// the whole fleet. That is how it shipped, and EXP-97 section 10 is the
	// measurement that puts it in question: the engines holding chat run at
	// 1.07-1.15 times their prediction while the engines dedicated to deep
	// research run at 0.76-0.96, so one coefficient averages two errors of
	// opposite sign to a fleet mean of 0.975-0.997 while every individual
	// instance is wrong by -24% to +15%. A value correct on average is a value
	// correct for no instance.
	//
	// When perInstance is true this stays live and keeps its fleet meaning: it
	// is what a newly seen instance starts from, and it is still published so
	// the two can be compared in the same run.
	correction float64

	// corrections holds the per-instance factor when perInstance is set. An
	// instance absent from the map has not been measured yet and falls back to
	// the fleet value above rather than to 1.0, so a new instance starts from
	// what the fleet already knows instead of from the uncorrected law.
	corrections map[string]float64
	perInstance bool

	// prefillFraction is the share of an arriving prompt's tokens the engine
	// actually computes. See notePrefill.
	prefillFraction float64
}

const (
	// Weight of one interval in the correction. Deliberately small.
	//
	// The correction sits inside a loop: a larger correction tightens the gates,
	// which admits less, which makes the engine faster, which makes the
	// measurement fall below the prediction, which shrinks the correction again.
	// The loop has real delay in it -- an admission changes the engine's pace
	// only once the request is running -- so a filter fast enough to track load
	// oscillates instead of settling. Measured at 3000 rpm with a weight of
	// 0.02, which is a time constant of about six seconds at the rate these
	// samples arrive, it swung between 1.2 and 2.4 for the whole run and took
	// the admission decisions with it.
	//
	// At 0.002 the time constant is around a minute, which is far slower than
	// the loop and therefore stable, and still fast enough for what this is for:
	// following drift between the offline law and the engine in front of it,
	// not following the offered rate.
	fsCorrectionAlpha = 0.002
	// Bounds. Outside this range the offline law no longer describes the engine
	// at all, which is a condition to report rather than to absorb silently.
	fsCorrectionMin = 0.5
	fsCorrectionMax = 3.0
)

// noteResidual folds one interval's measurement into the correction.
//
// This replaces an earlier online calibration that was removed as unusable, and
// the difference is what is being compared. That one tried to refit the DECODE
// law, which required separating decode cost from prefill cost inside a 500 ms
// interval; the only test available looks at the queue at the two endpoints, and
// a queue that forms and drains in between is invisible, so prefill cost was
// charged to the decode law and the factor ran to 1.73.
//
// Here there is nothing to separate. The quantity the decision needs is the mean
// iteration time over the horizon, meanStepMs already predicts exactly that
// including its prefill term, and the step counter and status timestamp measure
// exactly that over the interval just elapsed. The correction is the ratio of
// the two, so an interval that carried prefill work is not a contaminated
// sample -- it is the sample.
//
// It matters because the bias is not small and is one-directional. Measured over
// a two-minute run the model predicted a median of 27 ms where the engine took
// 35 ms, on every instance. An instance is admitted work up to a budget, so a
// prediction 23% low is admission 23% past what the budget allows, and the
// requests that were admitted on that basis miss.
func (m *capacityModel) noteResidual(instance string, predictedMs, measuredMs float64) {
	if predictedMs <= 0 || measuredMs <= 0 || math.IsInf(predictedMs, 0) {
		return
	}
	ratio := measuredMs / predictedMs
	// A ratio this far out describes a status pair that straddles something
	// other than steady operation.
	if ratio < 0.2 || ratio > 5 {
		return
	}
	clamp := func(v float64) float64 {
		v *= 1 + fsCorrectionAlpha*(ratio-1)
		if v < fsCorrectionMin {
			v = fsCorrectionMin
		}
		if v > fsCorrectionMax {
			v = fsCorrectionMax
		}
		return v
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	// The ratio is against the ALREADY CORRECTED prediction, so the update
	// multiplies rather than replaces.
	//
	// The fleet value is updated on every sample whether or not per-instance
	// mode is on, for two reasons: it is what an instance seen for the first
	// time starts from, and keeping it live means a run can be read against the
	// fleet number the previous runs were produced with.
	m.correction = clamp(m.correction)
	if m.perInstance && instance != "" {
		if m.corrections == nil {
			m.corrections = map[string]float64{}
		}
		cur, ok := m.corrections[instance]
		if !ok {
			// Start from what the fleet knows, not from 1.0. A fresh instance
			// otherwise spends its first samples re-deriving a correction the
			// rest of the fleet has already measured, and during that time its
			// predictions are the uncorrected law.
			cur = m.correction
		}
		m.corrections[instance] = clamp(cur)
	}
}

func (m *capacityModel) correctionFactor() float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.correction
}

// correctionFor is the factor actually applied to instance `id`, which is what
// has to be published for the per-instance mode to be checkable at all: with
// only the fleet series in the metrics there is no way to tell from a run
// whether the split happened.
func (m *capacityModel) correctionFor(id string) float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.corrLocked(id)
}

const (
	// Bounds on the measured prefill fraction. The lower one is an order of
	// magnitude below the lowest sharing ratio ever measured on this workload,
	// so it bounds nonsense without interfering with a real reading; the upper
	// one is the no-cache case.
	fsPrefillFractionMin = 0.02
	fsPrefillFractionMax = 1.0
	// Bounds when the same quantity is used as a RESIDUAL rather than as the
	// estimate itself -- that is, when the prefix index supplies the token count
	// and this only corrects it. A residual has to be allowed above 1: it is
	// above 1 exactly when the index claimed more cache hits than the engine
	// had, which is the failure mode that matters, because the scheduler
	// remembers what it dispatched and cannot see eviction. Capping it at 1
	// would hide that.
	fsPrefillResidualMin = 0.25
	fsPrefillResidualMax = 4.0
	// Weight of one interval. Slower than the step correction because the
	// quantity is a property of the workload rather than of the hardware, and a
	// workload's prompt reuse changes over minutes rather than seconds.
	fsPrefillAlpha = 0.01
)

// notePrefill measures how much of the prompts sent to one instance the engine
// actually had to compute, and folds it into a running estimate.
//
// It exists because charging an arriving prompt in full is wrong by an order of
// magnitude on any workload with prompt reuse, and wrong in the direction that
// matters most. An agent request carries a 22k-token prompt; charged in full it
// occupies three of the next hundred iterations with prefill and raises the
// predicted mean by about 15 ms, which is enough to make it infeasible almost
// everywhere. Measured at an offered rate the fleet carries comfortably, that
// produced a 28.8% rejection rate for the agent class while the engines ran at
// 13.5 ms against budgets of 50 and 100 ms.
//
// The engine's own figure for queued prefill is already net of its cache, so
// only the request being added needs this. What is measured here is the work
// the engine did:
//
//	prefill time in the interval = (measured mean - decode-only prediction) x steps
//	chunks executed              = that / the extra cost of a chunk-carrying step
//	tokens computed              = chunks x chunk size
//
// against the prompt tokens this scheduler sent to that instance over the same
// interval. Anything else that makes the engine slower than the decode law
// predicts is attributed to prefill by this arithmetic, which overstates the
// tokens computed and therefore understates the discount. That is the safe
// direction: it charges an arrival more than it costs rather than less.
// When the prefix index supplies the token count, the DENOMINATOR changes and
// with it the meaning of the result. `charged` is then the prefill this
// scheduler predicted for the placements made over the same stretch, so the
// ratio is the error of that prediction and sits at 1.0 when it is right, rather
// than being the estimate itself. Both cases share this function because they
// share the numerator; what differs is which quantity the caller passes and
// which bounds apply. See ms_dev/notes/fluidserve-prefix.md section 2.
func (m *capacityModel) notePrefill(
	measuredMs, decodeOnlyMs, steps, chunk, promptTokensDispatched float64,
	residual bool) {

	if promptTokensDispatched <= 0 || steps <= 0 || chunk <= 0 ||
		measuredMs <= 0 || decodeOnlyMs <= 0 {
		return
	}
	perChunk := m.prefillStepMs(chunk) - m.c0
	if math.IsInf(perChunk, 0) || perChunk <= 0 {
		return
	}
	prefillMs := (measuredMs - decodeOnlyMs) * steps
	if prefillMs < 0 {
		prefillMs = 0
	}
	computed := prefillMs / perChunk * chunk
	ratio := computed / promptTokensDispatched
	lo, hi := fsPrefillFractionMin, fsPrefillFractionMax
	if residual {
		lo, hi = fsPrefillResidualMin, fsPrefillResidualMax
	}
	if ratio > hi {
		ratio = hi
	}
	if ratio < lo {
		ratio = lo
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.prefillFraction += fsPrefillAlpha * (ratio - m.prefillFraction)
}

// prefillFractionOf reports the share of an arriving prompt's tokens the engine
// is expected to compute. It starts at 1 -- charge the whole prompt -- so that
// the first decisions of a process are made on the conservative assumption, and
// moves only as evidence accumulates.
func (m *capacityModel) prefillFractionOf() float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.prefillFraction
}

// An online correction of the DECODE LAW specifically was implemented and removed. The only
// test available without an engine change -- no prefill queued at either end of
// a status interval -- cannot certify that no prefill ran DURING it: statuses
// arrive every 500 ms and the engine executes around 25 iterations in between,
// so a queue that formed and drained inside the interval is invisible. Measured
// live, that misattribution drove the correction to 1.73, which in turn drove
// the safety factor to its ceiling and cut throughput by half. The offline law
// is fitted over a far wider range than one run visits (377,838 iterations,
// cross-checked against an independent fit) and read 0.9998 against an idle
// engine, so it is used as loaded and the three constants the correction needed
// are gone. Reinstating it requires a per-step prefill-token signal from the
// engine, not another filter on the same data.

func newCapacityModel(
	p *fluidserveProfile, predictor *LatencyPredictor, perInstance bool) *capacityModel {

	return &capacityModel{
		c0:              p.DecodeStepLaw.C0Ms,
		cKv:             p.DecodeStepLaw.CKvMsPerToken,
		cN:              p.DecodeStepLaw.CNMsPerRequest,
		predictor:       predictor,
		correction:      1.0,
		corrections:     map[string]float64{},
		perInstance:     perInstance,
		prefillFraction: 1.0,
	}
}

// corrLocked is the factor to apply for instance `id`. The caller holds the
// lock. An empty id, which is what the tests and any call that does not know
// the instance pass, always gets the fleet value.
func (m *capacityModel) corrLocked(id string) float64 {
	if !m.perInstance || id == "" {
		return m.correction
	}
	if v, ok := m.corrections[id]; ok {
		return v
	}
	return m.correction
}

// decodeStepMs is the cost of one iteration with no prefill in the batch.
func (m *capacityModel) decodeStepMs(kvTokens, nDecode float64) float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.decodeStepLocked(kvTokens, nDecode)
}

func (m *capacityModel) decodeStepLocked(kvTokens, nDecode float64) float64 {
	if kvTokens < 0 {
		kvTokens = 0
	}
	if nDecode < 0 {
		nDecode = 0
	}
	return m.c0 + m.cKv*kvTokens + m.cN*nDecode
}

// prefillStepMs is the cost of one iteration that carries a chunk of this size,
// measured on an idle engine and therefore excluding any decode work.
func (m *capacityModel) prefillStepMs(chunk float64) float64 {
	if chunk <= 0 {
		return 0
	}
	v, err := m.predictor.predictPrefillStepLatency(int32(chunk))
	if err != nil || math.IsInf(v, 0) || v <= 0 {
		// Falling back to the decode floor would understate the cost of a
		// prefill step by an order of magnitude, so refuse instead: the caller
		// treats a non-finite cost as "this instance cannot take the request".
		return math.Inf(1)
	}
	return v
}

// prefillSteps is how many of the next `horizon` iterations carry prefill work,
// given how much of it there is. vLLM's chunked prefill fills the token budget on
// every step while any prefill is waiting, so the count is the work divided by
// the chunk size.
//
// It is NOT rounded up, and that matters at exactly the load where the decision
// is hard. Rounding up is right for a single step -- an iteration either runs or
// it does not -- but this quantity is spread over a hundred iterations, and over
// a hundred iterations 1.4 chunk-carrying steps is 1.4, not 2. Charging the extra
// 0.6 of a step adds (t_pre(chunk) - c0) * 0.6 / horizon to the predicted mean,
// which is 3.0 ms here.
//
// Measured, that 3.0 ms decided the outcome. At 4800 rpm the engines ran at 32-40
// ms while the model predicted 44.7-46.7 against a gate of 45, so nothing was ever
// feasible and 86.8% of all decisions were to hold the request at the gateway. The
// same run shows the mechanism directly: in the one interval where the arriving
// prefill fell to 0.85 chunks and the ceiling therefore stopped adding a whole
// step, the prediction dropped to 41.0 ms and the instance became admissible again.
//
// The pairing with the caller's cost term is what makes both regimes right. Below
// one chunk the caller prices the PARTIAL chunk, so the count has to stay at 1 or
// the same work would be discounted twice. Above one chunk the caller prices a
// full chunk, so the count carries the fraction.
func prefillSteps(pendingTokens, chunk float64) float64 {
	if pendingTokens <= 0 || chunk <= 0 {
		return 0
	}
	if pendingTokens <= chunk {
		return 1
	}
	return pendingTokens / chunk
}

// meanStepMs is the average iteration time over the next `horizon` iterations.
//
// s_p of those iterations carry a prefill chunk and the rest are decode-only:
//
//	mean = [ (k - s_p) * t_dec + s_p * (t_pre + t_dec - c0) ] / k
//
// Note that this is what a decoding request already on the instance will
// experience as its mean time between tokens, because a chunked-prefill step
// still produces one token for every request in the decode batch.
func (m *capacityModel) meanStepMs(
	instance string, kvTokens, nDecode, pendingPrefill, chunk float64, horizon int) float64 {

	if horizon <= 0 {
		horizon = 1
	}
	m.mu.RLock()
	dec := m.decodeStepLocked(kvTokens, nDecode)
	c0, corr := m.c0, m.corrLocked(instance)
	m.mu.RUnlock()

	sp := prefillSteps(pendingPrefill, chunk)
	k := float64(horizon)
	if sp > k {
		sp = k
	}
	if sp <= 0 {
		return dec * corr
	}
	pre := m.prefillStepMs(math.Min(pendingPrefill, chunk))
	if math.IsInf(pre, 0) {
		return math.Inf(1)
	}
	return corr * ((k-sp)*dec + sp*(pre+dec-c0)) / k
}

// maxKvForAllowance inverts meanStepMs in the KV term: the largest number of KV
// tokens this instance may hold and still average at or below `allowanceMs` per
// token, given the decode batch and the prefill work already queued.
//
// The inversion is exact because the KV term is linear and appears in every
// iteration, prefill-carrying or not. Writing f for the fraction of iterations
// that carry a chunk:
//
//	mean = c0 + c_kv*kv + c_n*n + f*(t_pre - c0)
//
// A negative result means the instance cannot meet the allowance even with an
// empty cache, which the caller reports rather than clamping to zero, because
// the two situations call for different decisions.
func (m *capacityModel) maxKvForAllowance(
	instance string, allowanceMs, nDecode, pendingPrefill, chunk float64, horizon int) float64 {

	if horizon <= 0 {
		horizon = 1
	}
	m.mu.RLock()
	c0, cKv, cN, corr := m.c0, m.cKv, m.cN, m.corrLocked(instance)
	m.mu.RUnlock()

	if cKv <= 0 || corr <= 0 {
		return math.Inf(1)
	}
	// The inversion is of the corrected mean, so the allowance is divided by the
	// correction once rather than each term being scaled.
	allowanceMs /= corr
	f := prefillSteps(pendingPrefill, chunk) / float64(horizon)
	if f > 1 {
		f = 1
	}
	overhead := c0 + cN*nDecode
	if f > 0 {
		pre := m.prefillStepMs(math.Min(pendingPrefill, chunk))
		if math.IsInf(pre, 0) {
			return math.Inf(-1)
		}
		overhead += f * (pre - c0)
	}
	return (allowanceMs - overhead) / cKv
}

// tierServiceRate is the steady-state completion rate, in requests per second,
// of one instance serving ONLY the given class at the given per-token
// allowance -- the mu behind the class-instance cap's demand-to-instances
// conversion (fluidserve_instancecap.go).
//
// It solves the same mixed-step model meanStepMs applies, at the operating
// point where the mean step time sits exactly at the allowance. Writing d for
// the marginal step cost of one more resident and s_p for the fraction of
// steps that carry a prefill chunk:
//
//	a = c0 + d*B + s_p*(t_pre - c0),   d = c_kv*kvPerReq + c_n
//
// A chunk-carrying step still advances every decoder, so prefill costs only
// its EXTRA time over a decode step. In steady state the chunk rate is the
// request rate times chunks per request, and a step takes a ms, which closes
// the equation without iteration:
//
//	s_p = (B/residence) * (prompt/chunk) * (a/1000)
//	B   = (a - c0) / (d + extraPrefillMsPerReq * a / (1000 * residence))
//
// Omitting the prefill term was measured to be not merely biased but
// physically impossible for the heavy-input classes: it claimed 6.3 req/s for
// a class whose prompt length alone caps one instance at 3.8.
//
// The allowance is divided by the fleet correction factor, as every other
// consumer of this model divides, and B is clamped by the KV pool when the
// caller knows it. The result is a class-mean quantity for sizing an integer
// limit, not a per-placement prediction.
func (m *capacityModel) tierServiceRate(
	allowanceMs, meanPrompt, expectedToks, chunk, kvCapTokens float64) float64 {

	if allowanceMs <= 0 || expectedToks < 1 {
		return 0
	}
	m.mu.RLock()
	c0, cKv, cN, corr := m.c0, m.cKv, m.cN, m.corrLocked("")
	m.mu.RUnlock()
	if corr <= 0 {
		return 0
	}
	a := allowanceMs / corr
	if a <= c0 {
		return 0
	}
	if chunk <= 0 {
		chunk = 2048
	}
	if meanPrompt < 0 {
		meanPrompt = 0
	}

	extraPrefill := 0.0 // ms of prefill surcharge per request
	prefillMsPerReq := 0.0
	if meanPrompt > 0 {
		pre := m.prefillStepMs(math.Min(meanPrompt, chunk))
		if math.IsInf(pre, 0) {
			return 0
		}
		chunks := prefillSteps(meanPrompt, chunk)
		extraPrefill = chunks * (pre - c0)
		prefillMsPerReq = chunks * pre
	}
	// Residence: the prompt's compute plus the decode phase at the promised
	// pace. The nominal allowance, not the margined one, because it describes
	// how long the request LIVES, which is set by the promise it runs under.
	residenceS := (prefillMsPerReq + expectedToks*allowanceMs) / 1000.0
	if residenceS <= 0 {
		return 0
	}
	kvPerReq := meanPrompt + expectedToks/2
	d := cKv*kvPerReq + cN
	b := (a - c0) / (d + extraPrefill*a/(1000.0*residenceS))
	if kvCapTokens > 0 && kvPerReq > 0 {
		b = math.Min(b, kvCapTokens/kvPerReq)
	}
	if b <= 0 {
		return 0
	}
	return b / residenceS
}

// floorStepMs is the cost of an iteration on an otherwise empty instance. An
// allowance below this cannot be met by any placement decision, which is the
// test used to identify a request whose SLO is not physically achievable.
func (m *capacityModel) floorStepMs(instance string) float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.c0 * m.corrLocked(instance)
}
