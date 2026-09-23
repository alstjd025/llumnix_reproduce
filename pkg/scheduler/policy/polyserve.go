package policy

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"sync"

	"k8s.io/klog/v2"

	"llumnix/pkg/consts"
	"llumnix/pkg/metrics"
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
	// includePrefill decides whether the queued prefill chunk is charged to this
	// estimate. Section 4.5 states plainly that it should not be -- "PolyServe
	// only considers batch size and KV cache size" -- and handles prefill through
	// TTFT in section 4.7 instead. The near-term estimate is the exception: the
	// wait time section 4.6 asks about is "waiting for the server to finish the
	// current iteration", and on a co-located server that iteration IS the chunk.
	// See --polyserve-steady-state-ignores-prefill.
	includePrefill bool
	latency        float64
	// projectedKvTokens is the total KV the batch reaches under the section 4.5
	// forward simulation, kept so the memory predicate can compare it against the
	// instance's capacity without simulating a second time.
	projectedKvTokens float64
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

	m.projectedKvTokens = float64(kv)

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
	if pending := int32(m.allPrefillsTokensNum.GetValue()); m.includePrefill && pending > 0 {
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

// ---------------------------------------------------------------------------
// The decision ladder.
//
// The first port expressed PolyServe as two Llumnix filters (tier affinity,
// admission) plus a selector. That cannot express what the paper does, because
// three of its four steps need a fact about the WHOLE tier -- "every server of
// this tier refused the request" -- and a singleInstanceFilter is shown one
// instance at a time. Llumnix's own escalation is a single blanket retry with
// the relaxable filters removed, which turned admission into a test whose
// failure had no consequence.
//
// So the ladder lives in the selector instead, the same shape FluidServe uses,
// and follows Figure 5 of the paper: greedy scheduling inside the tier (3),
// promotion to a tighter tier (4), scaling up from the idle pool (5), and
// otherwise the request stays in the pending queue that section 4.6 accounts
// for as part of TTFT.

// polyserveConfig is the paper's mechanisms, each behind its own switch so that
// a run can be attributed to one of them rather than to "the new PolyServe".
type polyserveConfig struct {
	admissionBinds bool
	ignorePrefill  bool
	lazyPromotion  bool
	elastic        bool
	preferLoaded   bool
	kvAdmission    bool

	globalTtftSloMs float32
	globalTpotSloMs float32
	ttftMultiplier  float32
	tpotMultiplier  float32
}

// polyserveRequest is the per-request context. A selector is handed instance
// views and no request, so the policy's calculateMetrics hook writes this onto
// every view before any filter or selector runs -- the same route the vLLM
// router baseline and FluidServe already use.
type polyserveRequest struct {
	id        string
	tier      int // the TPOT SLO in ms, which IS the tier identity (section 4.2)
	ttftSloMs float64
	tpotSloMs float64
	// waitedMs is how long this request has already spent in the pending queue,
	// measured from the first time the scheduler was asked about it. The gateway
	// re-asks about a held request on a fixed period, so this is the "pending
	// time" of section 4.6 and it is what makes the refusal rule below a
	// statement about the request's own deadline rather than about a timeout.
	waitedMs float64
}

// polyserveWaitLog remembers when each request was first seen so that the time
// it has spent pending can be charged against its TTFT budget. Entries are
// dropped when the request is placed or refused; the sweep is only for requests
// that vanish without either, which happens when the client disconnects.
type polyserveWaitLog struct {
	mu        sync.Mutex
	firstSeen map[string]int64
	lastSweep int64
}

// polyserveWaitLogSweepMs bounds how long a forgotten entry survives. It is
// housekeeping, not a decision: no outcome depends on its value, and it only has
// to exceed the longest a gateway will hold a request.
const polyserveWaitLogSweepMs = 120000

func newPolyserveWaitLog() *polyserveWaitLog {
	return &polyserveWaitLog{firstSeen: map[string]int64{}}
}

func (w *polyserveWaitLog) waited(id string, nowMs int64) float64 {
	if id == "" {
		return 0
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	first, seen := w.firstSeen[id]
	if !seen {
		w.firstSeen[id] = nowMs
		first = nowMs
	}
	if nowMs-w.lastSweep > polyserveWaitLogSweepMs {
		for k, t := range w.firstSeen {
			if nowMs-t > polyserveWaitLogSweepMs {
				delete(w.firstSeen, k)
			}
		}
		w.lastSweep = nowMs
	}
	return float64(nowMs - first)
}

func (w *polyserveWaitLog) forget(id string) {
	if id == "" {
		return
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	delete(w.firstSeen, id)
}

// polyserveVerdict is one server's answer to "may I take this request and still
// meet the budget that applies here". The budget is not always the request's
// own: under lazy promotion a guest is judged by its host's, which is what makes
// the paper's claim that a looser guest is harmless enforced rather than assumed.
type polyserveVerdict struct {
	admitted bool
	reason   string
	util     float32
}

func metricValue(view *instanceViewScheduling, name string) float64 {
	m, ok := view.schedulingCtx.metrics[name]
	if !ok || m == nil {
		return math.Inf(1)
	}
	return float64(m.GetValue())
}

// projectedKvTokens reads back the largest KV the section 4.5 forward simulation
// reached for this candidate. Returns 0 when the estimate is not the polyserve
// one, which disables the memory predicate rather than inventing a number.
func projectedKvTokens(view *instanceViewScheduling) float64 {
	m, ok := view.schedulingCtx.metrics[consts.SchedulingMetricPolyserveIterMax]
	if !ok {
		return 0
	}
	iter, ok := m.(*polyserveIterTime)
	if !ok {
		return 0
	}
	return iter.projectedKvTokens
}

func kvCapacityTokens(view *instanceViewScheduling) float64 {
	if view.cmsView == nil || view.cmsView.Status == nil {
		return 0
	}
	return float64(view.cmsView.Status.NumTotalGpuTokens)
}

// judge runs the paper's admission test for one server. hostTierMs is the tier
// the server currently belongs to; passing 0 means "no tier restriction", in
// which case the request's own budget is the only one that applies.
func (c *polyserveConfig) judge(
	view *instanceViewScheduling, req *polyserveRequest, hostTierMs int) polyserveVerdict {

	ttftSlo := float64(effectiveSloMs(int(req.ttftSloMs), c.globalTtftSloMs) * c.ttftMultiplier)
	tpotBudget := req.tpotSloMs
	if hostTierMs > 0 && float64(hostTierMs) < tpotBudget {
		// Section 4.4. The guest is looser than the host, so the host's residents
		// are the ones at risk; judging the guest by its own budget would let it
		// break them.
		tpotBudget = float64(hostTierMs)
	}
	tpotSlo := float64(effectiveSloMs(int(tpotBudget), c.globalTpotSloMs) * c.tpotMultiplier)

	ttft := metricValue(view, consts.SchedulingMetricPredictedTtft)
	iterNow := metricValue(view, consts.SchedulingMetricPolyserveIterNow)
	iterMax := metricValue(view, consts.SchedulingMetricPolyserveIterMax)

	// The load of the dimension that would bind first on this server, so that
	// "most loaded that still meets the SLO" (section 4.3) is a single number
	// drawn from two budgets in different units.
	//
	// Deliberately measured against the request's own UNSCALED budgets, not the
	// ones the admission test just tightened. The two dispatch multipliers can be
	// set independently, and scaling the two dimensions by different factors
	// would change which of them is the maximum -- that is, it would change the
	// route -- as a side effect of a threshold that is only meant to decide
	// whether a server is admissible at all. This is also what keeps the ordering
	// identical to the selector this replaced.
	ttftRef := float64(effectiveSloMs(int(req.ttftSloMs), c.globalTtftSloMs))
	tpotRef := float64(effectiveSloMs(int(req.tpotSloMs), c.globalTpotSloMs))
	util := float32(0)
	if ttftRef > 0 {
		util = float32(ttft / ttftRef)
	}
	if tpotRef > 0 {
		if r := float32(iterMax / tpotRef); r > util {
			util = r
		}
	}

	switch {
	case ttft > ttftSlo:
		return polyserveVerdict{reason: "first_token", util: util}
	case ttft+iterNow > ttftSlo+tpotSlo:
		return polyserveVerdict{reason: "second_token", util: util}
	case iterMax > tpotSlo:
		return polyserveVerdict{reason: "steady_state", util: util}
	}
	if c.kvAdmission {
		if capTokens := kvCapacityTokens(view); capTokens > 0 {
			proj := projectedKvTokens(view)
			if proj > capTokens {
				// EXP-127. What the section 4.5 simulation charged, at the moment
				// it refused. Published only on the refusal branch: judge runs
				// once per candidate per request, and setting a gauge on every
				// call would put a map lookup and a label allocation on the
				// scheduler's hot path for values nobody asked for. Read against
				// instance_cms_all_decodes_tokens_num, collected on the same
				// scrape, this says at what real occupancy the accounting stops
				// admitting -- which could not be answered from any recorded
				// series before, because the pool size was in none of them.
				metrics.Gauge("scheduler_polyserve_memrefuse_projected_tokens",
					metrics.Labels{{Name: "instance", Value: view.GetInstanceId()}}).Set(proj)
				return polyserveVerdict{reason: "memory", util: util}
			}
		}
	}
	return polyserveVerdict{admitted: true, util: util}
}

// pick returns the admissible server the paper would choose among candidates:
// the most loaded one, or the least loaded one when the load ordering has been
// inverted. Ties break on instance ID, because Go randomises map iteration and
// an unstable choice would make a run unreproducible.
func (c *polyserveConfig) pick(
	candidates map[string]*instanceViewScheduling, req *polyserveRequest, hostTierMs int,
) (*instanceViewScheduling, map[string]int) {

	refused := map[string]int{}
	var best *instanceViewScheduling
	var bestUtil float32
	var bestID string
	for id, view := range candidates {
		v := c.judge(view, req, hostTierMs)
		if !v.admitted {
			refused[v.reason]++
			continue
		}
		better := best == nil ||
			(c.preferLoaded && v.util > bestUtil) ||
			(!c.preferLoaded && v.util < bestUtil) ||
			(v.util == bestUtil && id < bestID)
		if better {
			best, bestUtil, bestID = view, v.util, id
		}
	}
	return best, refused
}

// leastLoadedIgnoringAdmission is the behaviour Llumnix's fallback pass produced
// before admission was allowed to bind: place the request on the emptiest server
// of its tier whatever the estimates say. It is kept because the paper has no
// drop path, so with --polyserve-admission-binds off this is what overload looks
// like -- a missed SLO rather than a refusal.
func (c *polyserveConfig) leastLoadedIgnoringAdmission(
	candidates map[string]*instanceViewScheduling, req *polyserveRequest,
) *instanceViewScheduling {

	var best *instanceViewScheduling
	var bestUtil float32
	var bestID string
	for id, view := range candidates {
		v := c.judge(view, req, 0)
		if best == nil || v.util < bestUtil || (v.util == bestUtil && id < bestID) {
			best, bestUtil, bestID = view, v.util, id
		}
	}
	return best
}

// polyserveSelector walks the ladder of Figure 5 for one request.
//
//	1. its own tier, greedily (section 4.3)
//	2. a tighter tier, if its own refused it everywhere (section 4.4)
//	3. a server from the idle pool (section 4.3)
//	4. otherwise the request stays in the pending queue, which is what makes
//	   step 3 possible on the next attempt, and is refused only once its own
//	   TTFT budget is gone
//
// With every switch at its default the ladder collapses to what the filter pair
// it replaced did: try the request's own tier, and if nothing there admits it,
// place it on the emptiest server of that tier anyway.
type polyserveSelector struct {
	policy *polyserveDispatchPolicy
}

// tierServers is the set of live instances currently serving a tier. The two
// partition modes answer this differently, and the difference matters at
// start-up: the demand allocator treats a tier it has not allocated yet as
// unrestricted, so its first requests may go anywhere, while the idle pool
// treats it as holding nothing, so its first request pends once and then claims
// a server. Both are deliberate -- the first keeps a tier from being
// unschedulable before the allocator has run, the second is the paper's own
// sequence.
func (p *polyserveDispatchPolicy) tierServers(
	tier int, all map[string]*instanceViewScheduling,
) map[string]*instanceViewScheduling {

	if p.cfg.elastic {
		return p.fleet.serving(tier, all)
	}
	out := make(map[string]*instanceViewScheduling, len(all))
	for id, view := range all {
		if p.tierPartition.allows(tier, id) {
			out[id] = view
		}
	}
	return out
}

// hostTierOf is the tier a server currently belongs to, which is the budget a
// promoted guest is judged against. Zero means the server is not restricted to
// a tier, so only the request's own budget applies.
func (p *polyserveDispatchPolicy) hostTierOf(id string) int {
	if p.cfg.elastic {
		return p.fleet.tierOfInstance(id)
	}
	for tier, servers := range p.tierPartition.snapshot() {
		if _, ok := servers[id]; ok {
			return tier
		}
	}
	return 0
}

// deadlineGone answers whether holding this request any longer can still help.
// The pending time it has already spent counts against its TTFT, which is what
// section 4.6 says it does, so if the fastest first token any live server could
// produce still lands past the budget, no later attempt will be better and the
// request is refused rather than held until the gateway's own patience runs out.
//
// The alternative is to hold every unplaceable request until the gateway cuts
// it, which would put the refusal decision in a deployment setting -- and the
// gateway's holding window has already been measured to move this workload's
// attainment by more than ten points, so it is not a neutral place to leave it.
func (p *polyserveDispatchPolicy) deadlineGone(
	req *polyserveRequest, all map[string]*instanceViewScheduling) bool {

	ttftSlo := float64(effectiveSloMs(int(req.ttftSloMs), p.cfg.globalTtftSloMs) * p.cfg.ttftMultiplier)
	if ttftSlo <= 0 {
		return false
	}
	best := math.Inf(1)
	for _, view := range all {
		if t := metricValue(view, consts.SchedulingMetricPredictedTtft); t < best {
			best = t
		}
	}
	if math.IsInf(best, 1) {
		return false
	}
	return req.waitedMs+best > ttftSlo
}

func (s *polyserveSelector) selectInstance(
	instances map[string]*instanceViewScheduling, fallback bool) *instanceViewScheduling {

	p := s.policy
	if len(instances) == 0 {
		return nil
	}

	// Every view carries the same request context; take it from any of them.
	var req *polyserveRequest
	for _, v := range instances {
		req = v.schedulingCtx.polyserveRequest
		break
	}
	if req == nil {
		klog.Warning("PolyServe selector: no request context, falling back to first instance")
		return anyInstance(instances)
	}

	own := p.tierServers(req.tier, instances)

	// 1. Greedy scheduling inside the request's own tier.
	if chosen, refused := p.cfg.pick(own, req, req.tier); chosen != nil {
		countPlacement("own_tier")
		p.waitLog.forget(req.id)
		return chosen
	} else {
		countRefusals("own_tier", refused)
	}

	// 2. Lazy promotion. Only upward, and only now that the request's own tier
	//    is full, which is the whole difference between lazy and eager: a guest
	//    that arrives before its own tier is full lowers the host tier's
	//    utilisation for nothing.
	if p.cfg.lazyPromotion {
		for _, host := range p.promotionTargets(req.tier) {
			hosts := p.tierServers(host, instances)
			if chosen, refused := p.cfg.pick(hosts, req, host); chosen != nil {
				countPlacement("promotion")
				p.waitLog.forget(req.id)
				return chosen
			} else {
				countRefusals("promotion", refused)
			}
		}
	}

	// 3. Grow the tier. The pending that got us here IS the trigger section 4.3
	//    names; there is no demand calculation behind it.
	if p.cfg.elastic {
		if id := p.fleet.claim(req.tier, instances); id != "" {
			countPlacement("scale_up")
			p.waitLog.forget(req.id)
			return instances[id]
		}
	}

	// 4. Nothing admits it.
	if !p.cfg.admissionBinds {
		// The paper has no drop path, and with admission relaxable this is what
		// Llumnix's fallback pass did: place it anyway and let the overload show
		// up as a missed SLO.
		countPlacement("forced")
		p.waitLog.forget(req.id)
		if chosen := p.cfg.leastLoadedIgnoringAdmission(own, req); chosen != nil {
			return chosen
		}
		return anyInstance(instances)
	}
	if p.deadlineGone(req, instances) {
		countPlacement("refused")
		p.markRejected(req.id)
		p.waitLog.forget(req.id)
		return nil
	}
	countPlacement("pending")
	return nil
}

// promotionTargets are the tiers a request of this tier may be promoted onto,
// tightest first.
func (p *polyserveDispatchPolicy) promotionTargets(tier int) []int {
	if p.cfg.elastic {
		return p.fleet.tighterThan(tier)
	}
	out := []int{}
	for host := range p.tierPartition.snapshot() {
		if host > 0 && host < tier {
			out = append(out, host)
		}
	}
	sort.Ints(out)
	return out
}
