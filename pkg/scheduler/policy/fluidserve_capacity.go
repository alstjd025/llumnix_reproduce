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
}

// An online correction of the decode law was implemented and removed. The only
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
		c0:        p.DecodeStepLaw.C0Ms,
		cKv:       p.DecodeStepLaw.CKvMsPerToken,
		cN:        p.DecodeStepLaw.CNMsPerRequest,
		predictor: predictor,
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
	c0 := m.c0
	m.mu.RUnlock()

	sp := prefillSteps(pendingPrefill, chunk)
	k := float64(horizon)
	if sp > k {
		sp = k
	}
	if sp <= 0 {
		return dec
	}
	pre := m.prefillStepMs(math.Min(pendingPrefill, chunk))
	if math.IsInf(pre, 0) {
		return math.Inf(1)
	}
	return ((k-sp)*dec + sp*(pre+dec-c0)) / k
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
	c0, cKv, cN := m.c0, m.cKv, m.cN
	m.mu.RUnlock()

	if cKv <= 0 {
		return math.Inf(1)
	}
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
	return m.c0
}
