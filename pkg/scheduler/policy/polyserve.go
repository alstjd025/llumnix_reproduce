package policy

import (
	"fmt"
	"math"
	"strconv"
	"strings"
	"sync"

	"k8s.io/klog/v2"

	"llumnix/pkg/consts"
	"llumnix/pkg/types"
)

// PolyServe (arXiv:2507.17769) pieces that Llumnix does not already have.
//
// What Llumnix brings: PredictedTtft already carries queueing -- it sums the
// waiting queue, in-progress prefills and inflight dispatches and converts that
// remaining work into time through the profiling table -- and PredictedTpot
// already asks the table about the batch *after* admitting the request, which
// is what section 4.5 means by "estimates the iteration time after admitting
// the new requests".
//
// What is missing, and lives here:
//
//	4.5  Admission is judged on the LARGEST KV the batch will reach as its
//	     requests grow, not on the current snapshot -> polyserveIterMax.
//	4.6  Wait-time-aware: the second token's deadline is TTFT + TPOT, and a
//	     request admitted with its TTFT nearly exhausted can still miss it if
//	     the very next iteration is slow -> the second-token check below.
//	4.7  Co-location must account for chunked prefill sharing the iteration;
//	     predictTpotLatency takes no prefill argument at all -> the prefill
//	     interference term in both iteration metrics.

// tierDecodeTokens maps a TPOT SLO (the tier key, in ms) to the expected output
// length of requests in that tier. PolyServe bins requests by per-token latency
// requirement, so the TPOT budget IS the tier identity and needs no extra
// plumbing beyond the per-request SLO added in P1.
type tierDecodeTokens struct {
	perTier      map[int]int
	defaultValue int
}

// parseTierDecodeTokens reads "tpotSloMs:tokens,..." e.g. "25:728,50:386,100:275".
func parseTierDecodeTokens(spec string, defaultValue int) (*tierDecodeTokens, error) {
	t := &tierDecodeTokens{perTier: map[int]int{}, defaultValue: defaultValue}
	if defaultValue <= 0 {
		return nil, fmt.Errorf("default decode tokens must be positive, got %d", defaultValue)
	}
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		key, value, found := strings.Cut(part, ":")
		if !found {
			return nil, fmt.Errorf("malformed entry %q, want tpotSloMs:tokens", part)
		}
		tpotMs, err := strconv.Atoi(strings.TrimSpace(key))
		if err != nil {
			return nil, fmt.Errorf("malformed tier %q: %w", key, err)
		}
		tokens, err := strconv.Atoi(strings.TrimSpace(value))
		if err != nil {
			return nil, fmt.Errorf("malformed token count %q: %w", value, err)
		}
		if tpotMs <= 0 || tokens <= 0 {
			return nil, fmt.Errorf("entry %q must be positive", part)
		}
		t.perTier[tpotMs] = tokens
	}
	return t, nil
}

func (t *tierDecodeTokens) forTier(tpotSloMs int) int {
	if tokens, ok := t.perTier[tpotSloMs]; ok {
		return tokens
	}
	return t.defaultValue
}

// polyserveIterTime estimates one decode iteration on an instance, optionally
// grown to the steady-state KV. Two instances of it are registered per policy:
// atMaxKV=false answers "how long is the very next iteration", atMaxKV=true
// answers "how long once this batch has grown to full length".
type polyserveIterTime struct {
	baseMetric
	allDecodesTokensNum
	allPrefillsTokensNum
	decodeBatchSize

	latencyPredictor *LatencyPredictor
	decodeTokens     *tierDecodeTokens
	atMaxKV          bool
	latency          float64
}

func (m *polyserveIterTime) Calculate(
	request *types.SchedulingRequest, instanceView *instanceViewScheduling) {
	m.allDecodesTokensNum.Calculate(request, instanceView)
	m.allPrefillsTokensNum.Calculate(request, instanceView)
	m.decodeBatchSize.Calculate(request, instanceView)

	promptTokens := 0
	tpotSloMs := 0
	if request != nil {
		promptTokens = request.PromptNumTokens
		tpotSloMs = request.TpotSloMs
	}

	// "after admitting the new request": the batch gains this request, and the
	// KV gains its prompt.
	batch := int32(m.decodeBatchSize.GetValue()) + 1
	kv := int32(m.allDecodesTokensNum.GetValue()) + int32(promptTokens)

	if m.atMaxKV {
		// Section 4.5 simulates forward to the largest KV the batch reaches.
		// Every request is assumed to grow by the tier's expected output
		// length, which is the true upper bound of that simulation and matches
		// the paper's conservative framing; the paper likewise does not predict
		// per-request output length.
		kv += batch * int32(m.decodeTokens.forTier(tpotSloMs))
	}

	iter, err := m.latencyPredictor.predictTpotLatency(batch, kv)
	if err != nil {
		klog.Warningf("[polyserveIterTime] tpot predict failed: %v", err)
		m.latency = math.Inf(1)
		m.baseMetric.value = float32(m.latency)
		return
	}

	// Section 4.7, co-location: a pending prefill means the next steps carry a
	// chunk, and that chunk IS the iteration for every decoding request in the
	// batch. Omitting this is what makes a snapshot TPOT estimate useless here
	// -- a full chunk costs hundreds of milliseconds against tens for a pure
	// decode step.
	if pending := int32(m.allPrefillsTokensNum.GetValue()); pending > 0 {
		chunk := pending
		if instanceView.cmsView != nil && instanceView.cmsView.Metadata != nil {
			if budget := instanceView.cmsView.Metadata.MaxNumBatchedTokens; budget > 0 && chunk > budget {
				chunk = budget
			}
		}
		prefillCost, err := m.latencyPredictor.predictPrefillStepLatency(chunk)
		if err != nil {
			klog.Warningf("[polyserveIterTime] prefill predict failed: %v", err)
			m.latency = math.Inf(1)
			m.baseMetric.value = float32(m.latency)
			return
		}
		iter += prefillCost
	}

	m.latency = iter
	m.baseMetric.value = float32(iter)

	klog.V(3).Infof(
		"Instance %s polyserveIterTime(maxKV=%v) = %.2fms [batch=%d kv=%d pendingPrefill=%.0f tier=%dms]",
		instanceView.GetInstanceId(), m.atMaxKV, iter, batch, kv,
		m.allPrefillsTokensNum.GetValue(), tpotSloMs)
}

func (m *polyserveIterTime) GetValue() float32 { return float32(m.latency) }

func (m *polyserveIterTime) ValueLess(value float32) bool { return m.GetValue() < value }

func (m *polyserveIterTime) Less(other instanceSchedulingMetric) bool {
	return m.GetValue() < other.GetValue()
}

// effectiveSloMs prefers the request's own budget and falls back to the global
// --ttft-slo / --tpot-slo when the client sent none (P1 encodes "unspecified"
// as zero).
func effectiveSloMs(requestSloMs int, globalSloMs float32) float32 {
	if requestSloMs > 0 {
		return float32(requestSloMs)
	}
	return globalSloMs
}

// polyserveAdmissionFilter is PolyServe's admission test: may this server take
// this request and still meet ITS SLO? Three checks, one per deadline regime.
type polyserveAdmissionFilter struct {
	globalTtftSloMs   float32
	globalTpotSloMs   float32
	ttftSloMultiplier float32
	tpotSloMultiplier float32
}

func (f *polyserveAdmissionFilter) instanceFilteredOut(instance *instanceViewScheduling) bool {
	ttftSlo := effectiveSloMs(instance.schedulingCtx.requestTtftSloMs, f.globalTtftSloMs) * f.ttftSloMultiplier
	tpotSlo := effectiveSloMs(instance.schedulingCtx.requestTpotSloMs, f.globalTpotSloMs) * f.tpotSloMultiplier

	ttft := instance.schedulingCtx.metrics[consts.SchedulingMetricPredictedTtft].GetValue()
	iterNow := instance.schedulingCtx.metrics[consts.SchedulingMetricPolyserveIterNow].GetValue()
	iterMax := instance.schedulingCtx.metrics[consts.SchedulingMetricPolyserveIterMax].GetValue()

	reject := func(reason string) bool {
		klog.V(3).Infof(
			"PolyServe admission rejected instance %s (%s): ttft=%.1f/%.1fms "+
				"iterNow=%.1f iterMax=%.1f/%.1fms",
			instance.GetInstanceId(), reason, ttft, ttftSlo, iterNow, iterMax, tpotSlo)
		return true
	}

	// First token, deadline TTFT. PredictedTtft already includes queueing.
	if ttft > ttftSlo {
		return reject("first token")
	}
	// Second token, deadline TTFT + TPOT (section 4.6). This is NOT implied by
	// the other two: it uses the near-term iteration, which a co-scheduled
	// prefill chunk can blow far past TPOT even while the steady state is fine.
	// A request admitted with its TTFT nearly spent has almost no room left,
	// which is exactly the case the paper calls out.
	if ttft+iterNow > ttftSlo+tpotSlo {
		return reject("second token")
	}
	// Third token onwards, deadline i*TPOT, judged at the steady-state KV
	// (section 4.5).
	if iterMax > tpotSlo {
		return reject("steady state")
	}
	return false
}

// skipWhenFallback: when no server passes, Llumnix retries with the relaxable
// filters dropped. Admission is relaxable -- the request then goes to the least
// loaded server in its tier rather than being rejected, which is how PolyServe
// behaves (it has no drop path; overload shows up as missed SLOs). Tier
// affinity is NOT relaxable, so isolation survives overload.
func (f *polyserveAdmissionFilter) skipWhenFallback() bool { return true }

// tierAffinityFilter keeps a request on the servers assigned to its SLO tier.
// The assignment itself is owned by a tierPartition, which P3 will drive
// dynamically; until then the default partition assigns every server to every
// tier, making this filter inert rather than wrong.
type tierAffinityFilter struct {
	partition *tierPartition
}

func (f *tierAffinityFilter) instanceFilteredOut(instance *instanceViewScheduling) bool {
	tier := instance.schedulingCtx.requestTpotSloMs
	if f.partition.allows(tier, instance.GetInstanceId()) {
		return false
	}
	klog.V(3).Infof("PolyServe tier affinity rejected instance %s (not assigned to tier %dms)",
		instance.GetInstanceId(), tier)
	return true
}

// Isolation is the point of the tier partition, so it must survive the fallback
// pass; otherwise an overloaded tier would spill onto the servers protecting
// the other tiers, which is precisely what PolyServe exists to prevent.
func (f *tierAffinityFilter) skipWhenFallback() bool { return false }

// tierPartition holds the tier -> servers assignment. The zero value assigns
// everything to everything, so the filter is inert until the repartitioner has
// seen enough traffic to allocate.
type tierPartition struct {
	mu sync.RWMutex
	// assignment maps a tier (TPOT SLO in ms) to the instance IDs serving it.
	// A nil map, or a tier absent from it, means "no restriction". Treating an
	// unallocated tier as unrestricted rather than starved matters: a tier that
	// has not been seen yet must not be unschedulable.
	assignment map[int]map[string]struct{}
}

func newTierPartition() *tierPartition { return &tierPartition{} }

func (p *tierPartition) allows(tier int, instanceID string) bool {
	if p == nil {
		return true
	}
	p.mu.RLock()
	defer p.mu.RUnlock()
	if p.assignment == nil {
		return true
	}
	servers, ok := p.assignment[tier]
	if !ok || len(servers) == 0 {
		return true
	}
	_, allowed := servers[instanceID]
	return allowed
}

func (p *tierPartition) set(assignment map[int]map[string]struct{}) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.assignment = assignment
}

func (p *tierPartition) snapshot() map[int]map[string]struct{} {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.assignment
}

// leastBindingLatencySelector picks the least loaded survivor, measured on
// whichever SLO dimension is closest to binding for that instance.
//
// PolyServe routes to the highest-load server that still meets the SLO, to
// build a load gradient an autoscaler can act on. We deliberately invert that:
// with a fixed fleet there is nothing to scale down into, so packing buys
// nothing, while TTFT grows with queue depth and headroom absorbs bursts and
// prediction error.
//
// "Least loaded" needs a single number from two budgets in different units, so
// each instance is scored by its worst-case utilisation
// max(predictedTtft/ttftSlo, iterMax/tpotSlo) and the smallest score wins. That
// is the load on the dimension that would bind first, evaluated per instance,
// so it needs no global choice of dimension and stays order-independent.
type leastBindingLatencySelector struct {
	globalTtftSloMs float32
	globalTpotSloMs float32
}

func (s *leastBindingLatencySelector) score(instance *instanceViewScheduling) float32 {
	ttftSlo := effectiveSloMs(instance.schedulingCtx.requestTtftSloMs, s.globalTtftSloMs)
	tpotSlo := effectiveSloMs(instance.schedulingCtx.requestTpotSloMs, s.globalTpotSloMs)

	worst := float32(0)
	if ttftSlo > 0 {
		if m, ok := instance.schedulingCtx.metrics[consts.SchedulingMetricPredictedTtft]; ok {
			worst = m.GetValue() / ttftSlo
		}
	}
	if tpotSlo > 0 {
		if m, ok := instance.schedulingCtx.metrics[consts.SchedulingMetricPolyserveIterMax]; ok {
			if r := m.GetValue() / tpotSlo; r > worst {
				worst = r
			}
		}
	}
	return worst
}

func (s *leastBindingLatencySelector) selectInstance(
	instances map[string]*instanceViewScheduling, fallback bool) *instanceViewScheduling {

	var selected *instanceViewScheduling
	var bestScore float32
	var bestID string
	for id, instance := range instances {
		score := s.score(instance)
		// Ties broken by instance ID so the choice is deterministic; Go map
		// iteration order is randomised and would otherwise make an
		// experiment's routing unreproducible.
		if selected == nil || score < bestScore || (score == bestScore && id < bestID) {
			selected, bestScore, bestID = instance, score, id
		}
	}
	if selected == nil {
		klog.V(4).Info("PolyServe selector: no instance available")
		return nil
	}
	klog.V(4).Infof("PolyServe selected instance %s (binding utilisation %.3f, fallback=%v)",
		selected.GetInstanceId(), bestScore, fallback)
	return selected
}
