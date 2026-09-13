package policy

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"llumnix/pkg/metrics"

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
			// An optional third field is an explicit per-token budget in ms.
			// Without it the tier KEY is the budget, which is right when the
			// key was chosen as a budget (50, 100) and wrong when it is a
			// legacy identifier -- the agent tier's key is 25 for historical
			// reasons, and 25 ms per token is below this hardware's decode
			// floor, the configuration that once made a baseline reject 98%
			// of the class. "25:decode:75" states the real budget while the
			// tier key keeps naming the class everywhere else.
			perTok := float64(tier)
			if len(fields) == 3 {
				ms, err := strconv.Atoi(strings.TrimSpace(fields[2]))
				if err != nil || ms <= 0 {
					return nil, fmt.Errorf("malformed decode budget in %q", part)
				}
				perTok = float64(ms)
			}
			b.byTier[tier] = budgetSpec{tier: tier, mode: budgetDecode, perTokMs: perTok}
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
	// prefillEstMs is what the decision path predicted this request's prefill
	// would cost on the instance it was sent to. Kept so that the realised
	// placement-to-first-token time can be split into the part that depends on
	// the prompt and the part that does not: subtracting it leaves the engine's
	// own queue and the tail of whatever iteration was running, which is what
	// the modelled prefill never included and what has to be measured.
	prefillEstMs float64
	// oracleTokens is the client's per-request output-length hint, or 0 when
	// none was supplied. EXP-64 uses it in place of the class distribution for
	// this request's remaining length while it is resident -- the feasibility
	// test asks what the incumbents still have to produce, so a hint applied only
	// to the arriving request would leave the larger half of the question on the
	// class average and measure something else.
	oracleTokens int
	lastJ        int
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
	// oracleTokens is the client's per-request length hint, carried through so
	// the fleet projection can use it as well. Zero when absent.
	oracleTokens int
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

	// Weight of one placement-to-first-token sample. Faster than the capacity
	// correction because this quantity follows the offered load rather than
	// drift between the offline law and the hardware: it has to be right within
	// a burst, not over a run. At the rate these samples arrive -- one per
	// placement, so hundreds a second at high load -- 0.01 is a time constant of
	// well under a second.
	fsPlacementDelayAlpha = 0.01
	// Samples required before the measured bound replaces the modelled prefill
	// time. Below this the standard deviation is not yet meaningful and using it
	// would make the first decisions of a process worse than the fallback.
	fsMinPlacementDelaySamples = 50
	// A sample beyond this straddles an engine restart or a record that outlived
	// the request it describes; the gateway's own hold window is 35 s.
	fsMaxPlacementDelayMs = 60 * 1000

	// The line between a first token that arrived in time and one that did not,
	// for the joint table published in reconcile. Deep research's
	// time-to-first-token budget, because that is the class the first-token
	// rule is about: over both EXP-100 repeats 19.3% of its completed requests
	// exceeded 10 s to a first token and 0.0% exceeded its per-token budget.
	// Instrumentation only; no decision reads it.
	fsSlowFirstTokenMs = 10000.0
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
	// lastSeenMs[requestID] is when this request was last put through the
	// scheduling path. The gateway re-asks about a held request on a fixed
	// cadence, so the difference between two consecutive calls IS that cadence,
	// measured rather than configured. See noteArrival.
	lastSeenMs map[string]int64
	lastGCMs   int64

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
	// chargedPrefillSince[instanceID] accumulates the same placements' PREDICTED
	// prefill charge, which is the prompt minus whatever the prefix index said
	// the instance already held.
	//
	// Two accumulators rather than one because they are two quantities and the
	// calibration divides by whichever the charge was actually made from. With
	// the prefix index off the charge is the whole prompt and the two agree; with
	// it on they differ by exactly the predicted hit, so dividing the engine's
	// computed tokens by the wrong one would apply the same discount twice --
	// once in the charge and once in the factor meant to correct it.
	chargedPrefillSince map[string]float64

	// promptHashes[requestID] is the request's prompt as one chained hash per
	// block, computed once.
	//
	// It is cached here rather than recomputed in calculateMetrics because that
	// function runs on every scheduling call and a request this policy holds at
	// the gateway re-enters it on every recheck. Hashing a 6,472-token agent
	// prompt costs about 20 microseconds; doing it once per request is free and
	// doing it on every recheck of a request held for sixteen seconds is the
	// call rate that has saturated this scheduler before.
	promptHashes map[string][]uint64

	// The time between placing a request and its first output token, measured.
	//
	// canWait decides whether a held request can afford to keep waiting, and to
	// do that it has to subtract what will still happen after it is placed. That
	// subtraction used a point estimate of the prefill compute time plus a fixed
	// safety constant, and the estimate left out everything else that stands
	// between a placement and a first token: the engine's own queue, the tail of
	// whatever iteration is running when the request lands, and the variance of
	// both. Measured at 80 req/s the two together came to about 375 ms while the
	// realised delay was around 500 ms with a tail past 2 s, and the whole of
	// chat's 15.5% violation rate was requests that had been held until 4.1 s of
	// a 5.0 s budget and then took longer than 900 ms to produce a token.
	//
	// Every other place in this policy that subtracts an uncertain quantity
	// takes a one-sided bound rather than a mean -- expectedOutflow uses
	// mean - z*sd with the same z. This is the one that did not, and it is where
	// the violations were.
	//
	// Mean and mean-square are smoothed separately so the standard deviation
	// comes out of the same two accumulations, for the same reason the duty
	// cycle is a ratio of accumulations: a smoothed ratio is not the ratio of
	// the smoothed parts.
	delayMean   float64
	delayMeanSq float64
	delaySeen   int64
	// The same three per instance, used when perInstanceDelay is set. The fleet
	// values above stay live in both modes: they are what an instance with too
	// few samples of its own falls back to, and they keep the meaning every
	// earlier run was analysed with.
	//
	// EXP-98/100 is the measurement that puts the single value in question. The
	// realised time from placement to first token has a p90 of 1,265, 1,520,
	// 2,564 and 20,022 ms across the four engines of one run -- a sixteenfold
	// spread against one number. This is the same defect, and the same fix, as
	// the iteration-time correction that was split per instance on 2026-08-25.
	delayByInst      map[string]*delayStat
	perInstanceDelay bool
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
		lastSeenMs:        map[string]int64{},
		dispatchVersion:   map[string]uint64{},
		promptTokensSince: map[string]float64{},

		chargedPrefillSince: map[string]float64{},
		promptHashes:        map[string][]uint64{},
	}
}

// hashesFor returns the request's prompt block hashes, computing them the first
// time this request is seen and returning the cached slice afterwards.
//
// A nil result means there is nothing to match on -- either the gateway did not
// forward token ids, or the prompt is shorter than one block. Both are handled
// the same way downstream: no hit is claimed and the whole prompt is charged,
// which is the behaviour this policy had before the index existed.
func (r *requestRegistry) hashesFor(
	requestID string, tokens []int64, blockTokens int) []uint64 {

	if requestID == "" {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if h, ok := r.promptHashes[requestID]; ok {
		return h
	}
	h := hashPrompt(tokens, blockTokens)
	// Stored even when empty, so a request whose ids never arrive is not
	// re-examined on every recheck.
	r.promptHashes[requestID] = h
	return h
}

// takeChargedPrefill returns and clears the predicted prefill charge placed on
// an instance since the previous call. The counterpart of takePromptTokens; see
// the field comment for why both exist.
func (r *requestRegistry) takeChargedPrefill(instanceID string) float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	v := r.chargedPrefillSince[instanceID]
	r.chargedPrefillSince[instanceID] = 0
	return v
}

// noteArrival records when a request first asked to be placed and returns that
// time. The gateway retries the same request id while it holds it, so the first
// call is the arrival and later calls report how long it has been waiting.
//
// The first call is also where the offered rate is counted, for the same
// reason: it is the one call per request, so what accumulates here is what the
// workload asked for rather than how often the gateway asked again.
// It also measures how long it has been since this request was last considered.
// A held request cannot act between two of those calls, so that interval is the
// granularity at which any deadline can be honoured, and a deadline that leaves
// less than one interval of margin can be passed without the request ever being
// looked at inside it. Measuring it here rather than configuring it keeps the
// policy independent of the gateway's retry setting: whatever cadence the
// gateway uses, this reports it.
func (r *requestRegistry) noteArrival(
	requestID string, promptTokens int, nowMs int64) (
	arrivedMs int64, recheckMs float64, first bool) {

	// The third return is an explicit first-sighting signal. Callers must not
	// infer it from arrivedMs == nowMs: a re-entry in the same millisecond as
	// the arrival returns equal timestamps too, and the per-tier demand
	// estimate behind the class-instance cap double-counts exactly the held
	// requests if that happens under load.
	r.mu.Lock()
	defer r.mu.Unlock()
	if t, ok := r.arrivedMs[requestID]; ok {
		gap := 0.0
		if last, seen := r.lastSeenMs[requestID]; seen && nowMs > last {
			gap = float64(nowMs - last)
		}
		r.lastSeenMs[requestID] = nowMs
		return t, gap, false
	}
	r.arrivedMs[requestID] = nowMs
	r.lastSeenMs[requestID] = nowMs
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
	// First sighting: nothing has been waited yet, so no re-decision interval is
	// needed and none has been observed.
	return nowMs, 0, true
}

func (r *requestRegistry) gcLocked(nowMs int64) {
	if nowMs-r.lastGCMs < 30_000 {
		return
	}
	r.lastGCMs = nowMs
	for id, t := range r.arrivedMs {
		if nowMs-t > fsArrivalTTLMs {
			delete(r.arrivedMs, id)
			delete(r.lastSeenMs, id)
			delete(r.promptHashes, id)
		}
	}
}

// onDispatch records that a request was placed on an instance.
//
// chargedPrefill is the prompt tokens this placement was PRICED at -- the whole
// prompt when the prefix index is off or found nothing, and the prompt minus the
// predicted hit when it did. It is accumulated separately from promptTokens
// because the calibration in capacityModel.notePrefill divides by whichever one
// the charge was actually made from.
func (r *requestRegistry) onDispatch(
	instanceID, requestID string, tier, promptTokens int,
	chunk float64, stepID int64, nowMs int64, prefillEstMs float64,
	chargedPrefill float64, oracleTokens int) {

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
	r.chargedPrefillSince[instanceID] += chargedPrefill
	m[requestID] = &dispatchRecord{
		id:             requestID,
		tier:           tier,
		promptTokens:   promptTokens,
		prefillSteps:   steps,
		stepAtDispatch: stepID,
		firstSeenMs:    arrived,
		dispatchedMs:   nowMs,
		prefillEstMs:   prefillEstMs,
		oracleTokens:   oracleTokens,
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
			// The RESIDUAL, not the whole delay: what happened between the
			// placement and the first token beyond the prefill the decision
			// path had already priced. See notePlacementDelayLocked.
			realised := float64(nowMs - rec.dispatchedMs)
			r.notePlacementDelayLocked(instanceID, realised-rec.prefillEstMs)
			// Published so that "is the first-token estimate right" can be
			// answered from a run instead of by joining the client's records
			// afterwards. The decision path compares waited+prefillEstMs
			// against the TTFT budget, so the relation between these two series
			// is exactly the error that comparison carries.
			lbl := metrics.Labels{{Name: "instance", Value: instanceID}}
			// Counters take an int; these are milliseconds in the thousands, so
			// rounding costs nothing against the quantity being measured.
			metrics.Counter("scheduler_fluidserve_placement_predicted_ms_total", lbl).
				Add(int(rec.prefillEstMs + 0.5))
			metrics.Counter("scheduler_fluidserve_placement_realised_ms_total", lbl).
				Add(int(realised + 0.5))
			metrics.Counter("scheduler_fluidserve_placement_samples_total", lbl).Inc()

			// And the JOINT outcome, which the two sums above cannot give.
			//
			// A ratio of sums answers "is the estimate right on average", and
			// that is not the question an admission test asks. The test asks
			// whether the estimate is LARGE exactly when the wait turns out to
			// be long, because a request whose first token arrives late is the
			// only one it should refuse. An estimate can carry the right mean
			// and still be uncorrelated with the outcome, in which case no
			// threshold on it separates the two populations and no version of
			// the deadline test can work -- which is a conclusion about the
			// design, not a tuning result.
			//
			// So: a two-by-two table of (the decision expected this to be slow,
			// it was slow), at one threshold. The threshold is deep research's
			// time-to-first-token budget, because that is the class whose
			// violations are 100% of this rule -- measured over both EXP-100
			// repeats, 19.3% of its completed requests exceeded 10 s to a first
			// token while 0.0% exceeded its 100 ms per-token budget.
			//
			// Read as: true-positive over (true-positive + false-negative) is
			// how much of the late work the test could see at all, and
			// false-positive is what refusing on it would have cost.
			predSlow := rec.prefillEstMs >= fsSlowFirstTokenMs
			realSlow := realised >= fsSlowFirstTokenMs
			cell := "fast_fast"
			switch {
			case predSlow && realSlow:
				cell = "slow_slow"
			case predSlow && !realSlow:
				cell = "slow_fast"
			case !predSlow && realSlow:
				cell = "fast_slow"
			}
			// Labelled by CLASS rather than by instance. The interesting cells
			// are the slow ones and those are almost all one class, while chat
			// is 76.9% of the requests and nearly all of it is fast, so an
			// instance-labelled table would report a fleet that looks accurate
			// because most of what it places is easy. The tier is the class's
			// per-token budget in milliseconds, which is how every other series
			// here names a class.
			metrics.Counter("scheduler_fluidserve_placement_joint_total",
				metrics.Labels{
					{Name: "tier", Value: strconv.Itoa(rec.tier)},
					{Name: "cell", Value: cell},
				}).Inc()
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

// delayStat is one instance's running mean and mean-square of the queue
// residual, plus how many samples built them.
type delayStat struct {
	mean   float64
	meanSq float64
	seen   int64
}

// notePlacementDelayLocked folds one realised QUEUE residual into the running
// mean and mean-square: the time between a placement and its first token, minus
// the prefill the decision path had already priced for that request.
//
// The residual rather than the whole delay, because the whole delay is not one
// quantity. It is a prompt-dependent part -- the prefill compute, which scales
// with the prompt and which the capacity model already predicts per request --
// and a prompt-independent part -- the engine's own queue and the tail of the
// iteration running when the request lands. Folding both into one fleet-wide
// scalar makes that scalar an average over the class mix, and the mix here is
// 76.9% chat, so the scalar comes out at chat's value and is then applied to an
// agent request whose prompt is ten times longer.
//
// Measured, that is what happened: the fleet-wide version read 424 ms with 111
// of standard deviation, which is larger than the 375 ms the model gave a chat
// request and smaller than what it gave an agent one. Chat and deep research
// were held less and served better, rejection up 8 points each and attainment up;
// the agent class was held MORE, rejection down 20 points, and the extra
// requests it then admitted were marginal ones, so its attainment fell 6.
//
// Splitting it keeps the prompt-dependent part per request and measures only the
// part that has no reason to depend on the prompt.
func (r *requestRegistry) notePlacementDelayLocked(instance string, ms float64) {
	if ms < 0 {
		// The prefill was priced above what the whole placement took. Real
		// evidence that the queue cost nothing, so it counts as zero rather than
		// being discarded, which would keep only the samples that argue for
		// waiting less.
		ms = 0
	}
	if ms > fsMaxPlacementDelayMs {
		// Straddles an engine restart or a record that outlived its request.
		return
	}
	if r.delaySeen == 0 {
		r.delayMean, r.delayMeanSq = ms, ms*ms
	} else {
		r.delayMean += fsPlacementDelayAlpha * (ms - r.delayMean)
		r.delayMeanSq += fsPlacementDelayAlpha * (ms*ms - r.delayMeanSq)
	}
	r.delaySeen++

	if instance == "" {
		return
	}
	if r.delayByInst == nil {
		r.delayByInst = map[string]*delayStat{}
	}
	d, ok := r.delayByInst[instance]
	if !ok {
		// Start from what the fleet knows rather than from this one sample, for
		// the same reason the per-instance correction does: an instance seen for
		// the first time otherwise spends its early decisions on a mean built
		// from a single observation.
		d = &delayStat{mean: r.delayMean, meanSq: r.delayMeanSq}
		r.delayByInst[instance] = d
	}
	d.mean += fsPlacementDelayAlpha * (ms - d.mean)
	d.meanSq += fsPlacementDelayAlpha * (ms*ms - d.meanSq)
	d.seen++
}

// PlacementDelayBound is the queue residual to reserve on TOP of this request's
// own modelled prefill, as a one-sided upper bound rather than a mean. Returns
// -1 before enough has been measured, which the caller reads as "use the old
// fixed margin", so a fresh process behaves as it did before.
func (r *requestRegistry) PlacementDelayBound(instance string, z float64) (float64, int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	mean, meanSq, seen := r.delayMean, r.delayMeanSq, r.delaySeen
	if r.perInstanceDelay && instance != "" {
		if d, ok := r.delayByInst[instance]; ok && d.seen >= fsMinPlacementDelaySamples {
			mean, meanSq, seen = d.mean, d.meanSq, d.seen
		}
		// Below the sample floor this falls back to the fleet value rather than
		// to the fixed margin, which is the conservative direction: the fleet
		// number is built from real placements and the margin is a constant.
	}
	if seen < fsMinPlacementDelaySamples {
		return -1, seen
	}
	variance := meanSq - mean*mean
	if variance < 0 {
		variance = 0
	}
	return mean + z*math.Sqrt(variance), seen
}

// PlacementDelayFor reports one instance's bound and sample count for the
// metrics, without the fallback the decision path uses. -1 means "not measured
// here yet", which is what has to be visible to tell a genuinely fast instance
// from one that has no samples.
func (r *requestRegistry) PlacementDelayFor(instance string) (float64, int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	d, ok := r.delayByInst[instance]
	if !ok {
		return -1, 0
	}
	return d.mean, d.seen
}

// SetPerInstanceDelay is called once at construction.
func (r *requestRegistry) SetPerInstanceDelay(v bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.perInstanceDelay = v
}

// PlacementDelayMean is the centre of the same distribution, for telemetry. The
// bound is what decisions use.
func (r *requestRegistry) PlacementDelayMean() float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.delayMean
}

// liveViewLocked turns a record into the decision path's view of it, including
// the allowance: the time per remaining token that still satisfies the budget.
func (r *requestRegistry) liveViewLocked(rec *dispatchRecord, j int, nowMs int64) liveRequest {
	remaining := 1.0
	if rec.oracleTokens > 0 {
		// The hint is a length, not a distribution, so what is left is simply
		// what has not been produced. It floors at 1 rather than 0 for the same
		// reason expectedRemaining does: the allowance divides by it, and a
		// request on its last token still occupies the instance for that token.
		remaining = math.Max(1, float64(rec.oracleTokens-j))
	} else if prof := r.lengths.forTier(rec.tier); prof != nil {
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
		id:           rec.id,
		tier:         rec.tier,
		j:            j,
		kvTokens:     float64(rec.promptTokens + j),
		remaining:    remaining,
		allowanceMs:  allowance,
		nominalMs:    r.nominalAllowanceLocked(rec.tier),
		oracleTokens: rec.oracleTokens,
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
	delete(r.lastSeenMs, requestID)
	delete(r.promptHashes, requestID)
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
