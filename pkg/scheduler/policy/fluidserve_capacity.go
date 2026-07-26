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
	correction float64

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
func (m *capacityModel) noteResidual(predictedMs, measuredMs float64) {
	if predictedMs <= 0 || measuredMs <= 0 || math.IsInf(predictedMs, 0) {
		return
	}
	ratio := measuredMs / predictedMs
	// A ratio this far out describes a status pair that straddles something
	// other than steady operation.
	if ratio < 0.2 || ratio > 5 {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	// The ratio is against the ALREADY CORRECTED prediction, so the update
	// multiplies rather than replaces.
	m.correction *= 1 + fsCorrectionAlpha*(ratio-1)
	if m.correction < fsCorrectionMin {
		m.correction = fsCorrectionMin
	}
	if m.correction > fsCorrectionMax {
		m.correction = fsCorrectionMax
	}
}

func (m *capacityModel) correctionFactor() float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.correction
}

const (
	// Bounds on the measured prefill fraction. The lower one is an order of
	// magnitude below the lowest sharing ratio ever measured on this workload,
	// so it bounds nonsense without interfering with a real reading; the upper
	// one is the no-cache case.
	fsPrefillFractionMin = 0.02
	fsPrefillFractionMax = 1.0
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
func (m *capacityModel) notePrefill(
	measuredMs, decodeOnlyMs, steps, chunk, promptTokensDispatched float64) {

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
	if ratio > fsPrefillFractionMax {
		ratio = fsPrefillFractionMax
	}
	if ratio < fsPrefillFractionMin {
		ratio = fsPrefillFractionMin
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

func newCapacityModel(p *fluidserveProfile, predictor *LatencyPredictor) *capacityModel {
	return &capacityModel{
		c0:              p.DecodeStepLaw.C0Ms,
		cKv:             p.DecodeStepLaw.CKvMsPerToken,
		cN:              p.DecodeStepLaw.CNMsPerRequest,
		predictor:       predictor,
		correction:      1.0,
		prefillFraction: 1.0,
	}
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

// prefillSteps is how many iterations it takes to absorb this much queued
// prefill work at the engine's token budget. vLLM's chunked prefill fills the
// budget on every step while any prefill is waiting, so the count is simply the
// work divided by the chunk size, rounded up.
func prefillSteps(pendingTokens, chunk float64) float64 {
	if pendingTokens <= 0 || chunk <= 0 {
		return 0
	}
	return math.Ceil(pendingTokens / chunk)
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
func (m *capacityModel) meanStepMs(kvTokens, nDecode, pendingPrefill, chunk float64, horizon int) float64 {
	if horizon <= 0 {
		horizon = 1
	}
	m.mu.RLock()
	dec := m.decodeStepLocked(kvTokens, nDecode)
	c0, corr := m.c0, m.correction
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
	allowanceMs, nDecode, pendingPrefill, chunk float64, horizon int) float64 {

	if horizon <= 0 {
		horizon = 1
	}
	m.mu.RLock()
	c0, cKv, cN, corr := m.c0, m.cKv, m.cN, m.correction
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

// floorStepMs is the cost of an iteration on an otherwise empty instance. An
// allowance below this cannot be met by any placement decision, which is the
// test used to identify a request whose SLO is not physically achievable.
func (m *capacityModel) floorStepMs() float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.c0 * m.correction
}
