package policy

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"k8s.io/klog/v2"
)

// C2's bookkeeping half: which requests are on which instance, how far each has
// got, and how much of its latency budget it has left.
//
// The engine does not report per-request progress, and in full-mode scheduling
// the gateway only notifies the scheduler about a request when forwarding
// fails, so completions are not observed either. Both gaps are closed without
// touching the engine:
//
//	progress     The engine publishes a monotonic step counter. In continuous
//	             batching a decoding request emits exactly one token per step it
//	             is scheduled in, so the number of steps elapsed since a request
//	             entered decode is the number of tokens it has produced.
//
//	completion   Inferred by reconciliation. The engine reports how many
//	             requests are decoding; when this registry holds more than that,
//	             the excess must have finished, and the ones most likely to have
//	             finished are the ones furthest along.
//
// The registry is deliberately not the authority on how much KV is held. The
// engine reports that directly. What the registry supplies is the breakdown of
// that total by class and by progress, which is what the completion model needs
// and what no engine metric provides.

// budgetMode says how a tier's latency budget is defined.
type budgetMode int

const (
	// budgetE2E: the whole request must finish within a fixed wall-clock budget
	// measured from arrival. Time spent queueing counts against it.
	budgetE2E budgetMode = iota
	// budgetDecode: the request's mean time between output tokens must stay
	// within the tier's per-token budget. Queueing before the first token is
	// judged separately, against the TTFT budget.
	budgetDecode
)

type budgetSpec struct {
	tier     int
	mode     budgetMode
	totalMs  float64 // budgetE2E only
	perTokMs float64 // budgetDecode only, equal to the tier key
}

type classBudgets struct {
	byTier map[int]budgetSpec
}

// parseClassBudgets reads "25:e2e:30000,50:decode,100:decode".
//
// The distinction matters because the two forms give a request very different
// room to recover. A request scored on end-to-end latency may spend its budget
// unevenly, so being slow early is survivable if the rest is fast. A request
// scored on mean time between tokens has the same property over its decode
// phase. Treating either as a fixed per-token ceiling, as a tier key alone
// would, is stricter than what the request is actually judged by, and that extra
// strictness is paid for by the whole instance the request happens to land on.
func parseClassBudgets(spec string) (*classBudgets, error) {
	b := &classBudgets{byTier: map[int]budgetSpec{}}
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		fields := strings.Split(part, ":")
		if len(fields) < 2 {
			return nil, fmt.Errorf("malformed entry %q, want tier:mode[:budgetMs]", part)
		}
		tier, err := strconv.Atoi(strings.TrimSpace(fields[0]))
		if err != nil || tier <= 0 {
			return nil, fmt.Errorf("malformed tier in %q", part)
		}
		switch strings.TrimSpace(fields[1]) {
		case "e2e":
			if len(fields) != 3 {
				return nil, fmt.Errorf("entry %q: e2e needs a budget in ms", part)
			}
			ms, err := strconv.Atoi(strings.TrimSpace(fields[2]))
			if err != nil || ms <= 0 {
				return nil, fmt.Errorf("malformed e2e budget in %q", part)
			}
			b.byTier[tier] = budgetSpec{tier: tier, mode: budgetE2E, totalMs: float64(ms)}
		case "decode":
			b.byTier[tier] = budgetSpec{tier: tier, mode: budgetDecode, perTokMs: float64(tier)}
		default:
			return nil, fmt.Errorf("entry %q: mode must be e2e or decode", part)
		}
	}
	if len(b.byTier) == 0 {
		return nil, fmt.Errorf("no class budgets given")
	}
	return b, nil
}

func (b *classBudgets) forTier(tier int) budgetSpec {
	if s, ok := b.byTier[tier]; ok {
		return s
	}
	// An unlisted tier is judged on its per-token key, which is the reading the
	// tier key carries on its own.
	return budgetSpec{tier: tier, mode: budgetDecode, perTokMs: float64(tier)}
}

// dispatchRecord is one request believed to be running on one instance.
type dispatchRecord struct {
	id             string
	tier           int
	promptTokens   int
	prefillSteps   int
	stepAtDispatch int64
	firstSeenMs    int64 // when the gateway first asked us to place it
	dispatchedMs   int64
	decodeStartMs  int64 // set the first time progress is seen; 0 until then
	lastJ          int
}

// liveRequest is the per-request view the decision path consumes.
type liveRequest struct {
	id          string
	tier        int
	j           int     // output tokens produced so far
	kvTokens    float64 // prompt + produced, before reconciliation scaling
	remaining   float64 // expected output tokens still to come
	allowanceMs float64 // time per remaining token that still meets the budget
	// nominalMs is the per-token budget the tier starts with, independent of
	// how this particular request has fared. The live allowance moves as a
	// request falls behind or gets ahead, which makes it the right quantity for
	// deciding what an instance can still hold, but the wrong one for deciding
	// which requests belong together: one request falling behind would
	// otherwise change what class the instance appears to be serving.
	nominalMs float64
	// unachievable is set when the allowance has fallen below the cost of an
	// iteration on an empty instance. No placement decision can rescue such a
	// request, so it is excluded from the constraint that sets an instance's
	// capacity; continuing to honour it would hold the whole instance at a
	// capacity that helps nobody.
	unachievable bool
}

const (
	// Survival below which a request is assumed finished. Set against the tail
	// of the measured length distributions rather than at zero, so a request
	// that outlives every observed length does not stay in the registry forever.
	fsRetireSurvival = 0.02
	// Hard age limit. Backstop for a request the engine dropped without the
	// running count moving, for example if it was rejected downstream.
	fsMaxRecordAgeMs = 20 * 60 * 1000
	// How long a request id stays in the arrival table after we last saw it.
	// It has to outlive the gateway's hold-and-retry window so that a request
	// retried near the end of that window is still known to have been waiting.
	fsArrivalTTLMs = 5 * 60 * 1000
)

type requestRegistry struct {
	mu sync.Mutex

	lengths *lengthModel
	budgets *classBudgets

	// byInstance[instanceID][requestID]
	byInstance map[string]map[string]*dispatchRecord
	// arrival time per request id, kept for every request the gateway asks
	// about, including those still waiting for a placement.
	arrivedMs map[string]int64
	lastGCMs  int64

	// dispatchVersion[instanceID] increments on every placement. It is what lets
	// a cached view of an instance be invalidated by something other than the
	// engine's own status: between two status pulls the scheduler may itself
	// have added requests, and a view that did not know about them would let the
	// same capacity be handed out twice.
	dispatchVersion map[string]uint64
	// promptTokensSince[instanceID] accumulates the prompt tokens placed on an
	// instance since the last time the measurement read it, which is what the
	// prefill-fraction estimate is measured against.
	promptTokensSince map[string]float64
	// The offered rate: prompt tokens per millisecond ARRIVING at the gateway,
	// counted once per request rather than once per retry, and smoothed.
	//
	// TELEMETRY ONLY. No decision reads this. It was briefly the input to the
	// projection of future prefill work, and both things it was tried as failed
	// for the same underlying reason, which is worth keeping written down.
	//
	// The dispatched rate closes a loop through the PLACEMENT decision: a higher
	// estimate tightens an instance's gate, which sends it less, which lowers the
	// estimate. Measured, that settled in two different places on two runs of the
	// same binary at the same offered rate -- 70-80k tokens projected in one and
	// 17-37k in the other, 8 points of attainment apart.
	//
	// The offered rate closes a loop through the ADMISSION decision instead: it
	// counts the requests this policy is about to reject, so at rejection rate s
	// it reports the served rate divided by 1-s. A higher s therefore raises the
	// projection, which tightens the gate, which raises s. Measured at 3000 rpm
	// it settled at 54.6% rejected with the engines running at 32.9 ms against a
	// gate demanding 61.7 ms.
	//
	// What replaced it is the per-instance prefill duty cycle: the share of engine
	// time the engine itself was observed to spend on prefill. Occupancy and
	// iteration time are also consequences of this scheduler's decisions, and they
	// are safe to use for the reason neither rate was: the engine observes them
	// and reports them back, so a wrong belief is corrected rather than confirmed.
	offeredTokens     float64
	offeredRateEwma   float64
	offeredLastConvMs int64

	// Counters exported for telemetry.
	retiredByCount    int64
	retiredBySurvival int64
	retiredByAge      int64
}

func newRequestRegistry(lengths *lengthModel, budgets *classBudgets) *requestRegistry {
	return &requestRegistry{
		lengths:           lengths,
		budgets:           budgets,
		byInstance:        map[string]map[string]*dispatchRecord{},
		arrivedMs:         map[string]int64{},
		dispatchVersion:   map[string]uint64{},
		promptTokensSince: map[string]float64{},
	}
}

// noteArrival records when a request first asked to be placed and returns that
// time. The gateway retries the same request id while it holds it, so the first
// call is the arrival and later calls report how long it has been waiting.
//
// The first call is also where the offered rate is counted, for the same
// reason: it is the one call per request, so what accumulates here is what the
// workload asked for rather than how often the gateway asked again.
func (r *requestRegistry) noteArrival(requestID string, promptTokens int, nowMs int64) int64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	if t, ok := r.arrivedMs[requestID]; ok {
		return t
	}
	r.arrivedMs[requestID] = nowMs
	r.offeredTokens += float64(promptTokens)
	if r.offeredLastConvMs == 0 {
		r.offeredLastConvMs = nowMs
	} else if elapsed := float64(nowMs - r.offeredLastConvMs); elapsed >= fsOfferedWindowMs {
		rate := r.offeredTokens / elapsed
		if r.offeredRateEwma == 0 {
			r.offeredRateEwma = rate
		} else {
			r.offeredRateEwma += fsOfferedRateAlpha * (rate - r.offeredRateEwma)
		}
		r.offeredTokens = 0
		r.offeredLastConvMs = nowMs
	}
	r.gcLocked(nowMs)
	return nowMs
}

func (r *requestRegistry) gcLocked(nowMs int64) {
	if nowMs-r.lastGCMs < 30_000 {
		return
	}
	r.lastGCMs = nowMs
	for id, t := range r.arrivedMs {
		if nowMs-t > fsArrivalTTLMs {
			delete(r.arrivedMs, id)
		}
	}
}

// onDispatch records that a request was placed on an instance.
func (r *requestRegistry) onDispatch(
	instanceID, requestID string, tier, promptTokens int,
	chunk float64, stepID int64, nowMs int64) {

	r.mu.Lock()
	defer r.mu.Unlock()
	m := r.byInstance[instanceID]
	if m == nil {
		m = map[string]*dispatchRecord{}
		r.byInstance[instanceID] = m
	}
	steps := 1
	if chunk > 0 {
		steps = int(math.Ceil(float64(promptTokens) / chunk))
		if steps < 1 {
			steps = 1
		}
	}
	arrived := r.arrivedMs[requestID]
	if arrived == 0 {
		arrived = nowMs
	}
	r.dispatchVersion[instanceID]++
	r.promptTokensSince[instanceID] += float64(promptTokens)
	m[requestID] = &dispatchRecord{
		id:             requestID,
		tier:           tier,
		promptTokens:   promptTokens,
		prefillSteps:   steps,
		stepAtDispatch: stepID,
		firstSeenMs:    arrived,
		dispatchedMs:   nowMs,
	}
}

// reconcile brings one instance's records back in line with what the engine
// reports, and returns the live view of the requests still believed to be there.
//
// engineRunning is the number of requests the engine says are decoding. It is
// the authority: when the registry holds more, the extra ones have finished.
func (r *requestRegistry) reconcile(
	instanceID string, engineRunning int, stepID int64, nowMs int64) []liveRequest {

	r.mu.Lock()
	defer r.mu.Unlock()

	recs := r.byInstance[instanceID]
	if len(recs) == 0 {
		return nil
	}

	type entry struct {
		rec *dispatchRecord
		j   int
	}
	entries := make([]entry, 0, len(recs))
	for id, rec := range recs {
		if stepID < rec.stepAtDispatch {
			// The step counter went backwards, so the engine restarted and
			// nothing we recorded before it is still running.
			delete(recs, id)
			continue
		}
		if nowMs-rec.dispatchedMs > fsMaxRecordAgeMs {
			delete(recs, id)
			r.retiredByAge++
			continue
		}
		j := int(stepID - rec.stepAtDispatch - int64(rec.prefillSteps))
		if j < 0 {
			j = 0
		}
		if j > 0 && rec.decodeStartMs == 0 {
			// First evidence that this request has started producing tokens.
			// Recording the wall-clock instant here is what lets the decode
			// budget be measured against elapsed time rather than a modelled
			// step time.
			rec.decodeStartMs = nowMs
		}
		rec.lastJ = j
		prof := r.lengths.forTier(rec.tier)
		if prof != nil && prof.survivalAt(j) < fsRetireSurvival {
			delete(recs, id)
			r.retiredBySurvival++
			continue
		}
		entries = append(entries, entry{rec, j})
	}

	// Trim to the engine's count, dropping the requests furthest along first
	// because those are the ones most likely to have completed.
	if engineRunning >= 0 && len(entries) > engineRunning {
		sort.Slice(entries, func(a, b int) bool { return entries[a].j > entries[b].j })
		for i := 0; i < len(entries)-engineRunning; i++ {
			delete(recs, entries[i].rec.id)
			r.retiredByCount++
		}
		entries = entries[len(entries)-engineRunning:]
	}

	out := make([]liveRequest, 0, len(entries))
	for _, e := range entries {
		out = append(out, r.liveViewLocked(e.rec, e.j, nowMs))
	}
	return out
}

// liveViewLocked turns a record into the decision path's view of it, including
// the allowance: the time per remaining token that still satisfies the budget.
func (r *requestRegistry) liveViewLocked(rec *dispatchRecord, j int, nowMs int64) liveRequest {
	prof := r.lengths.forTier(rec.tier)
	remaining := 1.0
	if prof != nil {
		remaining = prof.expectedRemaining(j)
	}
	spec := r.budgets.forTier(rec.tier)

	var allowance float64
	switch spec.mode {
	case budgetE2E:
		elapsed := float64(nowMs - rec.firstSeenMs)
		allowance = (spec.totalMs - elapsed) / remaining
	default:
		if rec.decodeStartMs == 0 {
			// Still prefilling: nothing of the decode budget is spent yet, so
			// the allowance is the tier's per-token budget.
			allowance = spec.perTokMs
		} else {
			spent := float64(nowMs - rec.decodeStartMs)
			total := spec.perTokMs * (float64(j) + remaining)
			allowance = (total - spent) / remaining
		}
	}
	if allowance < 0 {
		allowance = 0
	}
	return liveRequest{
		id:          rec.id,
		tier:        rec.tier,
		j:           j,
		kvTokens:    float64(rec.promptTokens + j),
		remaining:   remaining,
		allowanceMs: allowance,
		nominalMs:   r.nominalAllowanceLocked(rec.tier),
	}
}

// nominalAllowanceLocked is the per-token budget a tier starts with: its key
// for the per-token form, and the whole budget spread over the expected output
// for the end-to-end form.
func (r *requestRegistry) nominalAllowanceLocked(tier int) float64 {
	spec := r.budgets.forTier(tier)
	if spec.mode == budgetE2E {
		expected := 1.0
		if prof := r.lengths.forTier(tier); prof != nil {
			expected = prof.expectedRemaining(0)
		}
		return spec.totalMs / expected
	}
	return spec.perTokMs
}

// NominalAllowance is the exported form used when judging a request that has
// not been dispatched yet.
func (r *requestRegistry) NominalAllowance(tier int) float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.nominalAllowanceLocked(tier)
}

// requestBudget describes what an arriving request of this tier is promised:
// the per-token pace, how many tokens it is expected to produce, and -- when the
// class is judged end to end -- the whole budget it has to fit inside.
//
// None of these depend on how long the request has already waited. The elapsed
// time is applied where the decision needs it, so that one quantity does not
// silently mean two different things.
func (r *requestRegistry) requestBudget(tier int) (
	nominalMs, expectedTokens float64, isE2E bool, budgetMs float64) {

	expectedTokens = 1
	if prof := r.lengths.forTier(tier); prof != nil {
		expectedTokens = prof.expectedRemaining(0)
	}
	spec := r.budgets.forTier(tier)
	if spec.mode == budgetE2E {
		return spec.totalMs / expectedTokens, expectedTokens, true, spec.totalMs
	}
	return spec.perTokMs, expectedTokens, false, 0
}

// forget drops any record of a request. It is called when the request is
// rejected, so that a request the gateway asked about many times while it was
// held does not leave an arrival entry behind for its whole time-to-live.
func (r *requestRegistry) forget(requestID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.arrivedMs, requestID)
}

// takePromptTokens returns and clears the prompt tokens placed on an instance
// since the previous call.
//
// This feeds the prefill-fraction estimate only, which is a ratio of two
// quantities measured over the same stretch -- the prefill work the engine did
// there, and the prompt tokens sent there. Both move together when the
// scheduler sends more or less, so the ratio is a property of the workload
// rather than of the decisions, which is why this one is safe to build from the
// dispatch ledger where a rate is not.
func (r *requestRegistry) takePromptTokens(instanceID string) float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	v := r.promptTokensSince[instanceID]
	r.promptTokensSince[instanceID] = 0
	return v
}

const (
	// Shortest stretch of arrivals converted into a rate. Below this the divisor
	// is small enough that one long prompt reads as a burst.
	fsOfferedWindowMs = 1000
	// Weight of one such window. At a one-second window this settles over about
	// twenty seconds, which is the timescale an offered rate moves on in the
	// dynamic trace and slow enough not to follow one arrival.
	fsOfferedRateAlpha = 0.05
)

// offeredRate reports the prompt tokens per millisecond arriving at the fleet.
func (r *requestRegistry) offeredRate() float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.offeredRateEwma
}

// version reports how many placements this instance has received.
func (r *requestRegistry) version(instanceID string) uint64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.dispatchVersion[instanceID]
}

func (r *requestRegistry) instanceCount(instanceID string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.byInstance[instanceID])
}

func (r *requestRegistry) counters() (byCount, bySurvival, byAge int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.retiredByCount, r.retiredBySurvival, r.retiredByAge
}

func (r *requestRegistry) logSummary() {
	byCount, bySurvival, byAge := r.counters()
	klog.V(3).Infof("FluidServe registry: retired %d by count, %d by survival, %d by age",
		byCount, bySurvival, byAge)
}

func nowMillis() int64 { return time.Now().UnixMilli() }
