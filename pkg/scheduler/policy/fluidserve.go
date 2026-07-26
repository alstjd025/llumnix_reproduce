package policy

import (
	"fmt"
	"math"
	"sort"
	"sync"
	"time"

	"k8s.io/klog/v2"

	"llumnix/cmd/scheduler/app/options"
	"llumnix/pkg/consts"
	"llumnix/pkg/metrics"
	"llumnix/pkg/types"
)

// FluidServe: routing and admission decided from one quantity.
//
// An instance's headroom is how much more KV it can take over the planning
// horizon and still meet the latency budgets of everything on it. Routing picks
// the instance with the most headroom after accounting for the capacity that
// placing this request there would cost the fleet; admission is the case where
// no instance has enough, in which case the request waits at the gateway rather
// than being committed to an engine.
//
// The three quantities the decision needs, and where each comes from:
//
//	occupancy    Reported by the engine. Exact, not estimated: it is the KV the
//	             engine says it is holding.
//	growth       Deterministic over a horizon counted in iterations. Every
//	             decoding request produces one token per iteration, so a batch
//	             of n grows by n*k tokens over k iterations regardless of how
//	             long those iterations take. Counting the horizon in iterations
//	             rather than seconds also avoids a circular definition, since
//	             iteration time is itself a function of occupancy.
//	release      Probabilistic. Estimated from the measured output-length
//	             distribution of each class, conditioned on how far each request
//	             has already got, and biased low by a multiple of its own
//	             standard deviation.
//
// The bias is deliberate and one-directional. Overestimating release admits
// more than the instance can hold, which ends in preemption and a collapse that
// takes far longer to recover from than the throughput given up by
// underestimating it.

const (
	// Fraction of physical KV capacity treated as usable. The engine begins
	// preempting before the pool is literally full, and a request's last block
	// is partially used, so the last few percent are not available in practice.
	fsMemorySafety = 0.95
	// How much of the tightest allowance an instance is allowed to consume
	// before it stops accepting new work. Leaves room for the requests already
	// there to absorb a burst without immediately violating.
	fsAllowanceUtilisation = 0.90
	// Ceiling on how much one incumbent can contribute to the harm score. A
	// request whose remaining slack is nearly zero would otherwise divide by
	// almost nothing and let a single incumbent dominate the ranking.
	fsHarmCap = 10.0
	// Placing a request is preferred to holding it unless doing so would eat
	// this much of some incumbent's remaining slack. Holding only helps when
	// the reason not to place is that it damages others; if the request simply
	// cannot meet its own budget anywhere, waiting makes that worse rather than
	// better.
	fsPendHarmThreshold = 2.0
	// A request's budget has to be at least this much tighter than the instance's
	// current constraint before it is charged for binding the instance to it.
	// Weight on the budget mismatch, chosen so that the separation between the
	// interactive and the batch budget (a factor of two, so a log-ratio of 0.69)
	// outweighs any difference in free space between two instances, which the
	// room term bounds to 1.
	fsMismatchWeight = 3.0
	// Largest share of its time-to-first-token budget a request may spend
	// waiting for a placement.
	fsMaxPendFraction = 0.25
)

type fluidserveConfig struct {
	horizonSteps            int
	zSafety                 float64
	alphaExternality        float64
	enablePend              bool
	enableExternality       bool
	enableFlux              bool
	enableOnlineCalibration bool
	pendGraceMs             float64
	ttftSafetyMs            float64
}

// fluidserveRequest is the per-request context the selector needs. Filters and
// selectors are handed an instance view and no request, so it travels on the
// scheduling context, which is rebuilt per request and therefore not shared.
type fluidserveRequest struct {
	id           string
	tier         int
	ttftSloMs    float64
	promptTokens int
	arrivedMs    int64
	nowMs        int64
	allowanceMs  float64
	expectedToks float64
	nominalMs    float64
}

// instanceFlux is one instance's state at the moment of a decision.
type instanceFlux struct {
	id string

	kvLogical      float64 // KV tokens as the latency model counts them
	kvPhysical     float64 // blocks the engine has allocated
	kvCapacity     float64 // physical pool size
	nDecode        float64
	pendingPrefill float64
	chunk          float64
	stepID         int64

	live              []liveRequest
	tightestAllowance float64 // over requests whose budget is still achievable
	tightestNominal   float64 // the budget in force right now
	preferredBudget   float64 // the budget this instance has been serving
	achievable        int
	unachievable      int

	inflow   float64
	outflow  float64
	proj     float64
	capKv    float64
	capMem   float64
	headroom float64
	meanStep float64
	// observedStep is the mean iteration time the engine actually achieved over
	// the last status interval, or -1 if it could not be measured. Comparing it
	// with meanStep is a live check on the capacity model.
	observedStep float64
}

type fluidserveDispatchPolicy struct {
	baseDispatchPolicy

	cfg      fluidserveConfig
	capacity *capacityModel
	registry *requestRegistry
	lengths  *lengthModel
	tuner    *safetyTuner

	obsMu   sync.Mutex
	lastObs map[string]stepObservation
}

// calculateMetrics is the only per-request hook that sees both the request and
// the live instance set, so the whole controller runs from here: the online
// calibration is fed, the registry is reconciled against what the engines
// report, and the resulting decision context is stamped onto each instance view
// for the selector to read.
func (p *fluidserveDispatchPolicy) calculateMetrics(
	inferType consts.InferType,
	request *types.SchedulingRequest,
	instanceViews map[string]*instanceViewScheduling) {

	if p.baseDispatchPolicy[inferType] == nil {
		return
	}
	if inferType != consts.InferTypeNeutral || request == nil {
		p.baseDispatchPolicy.calculateMetrics(inferType, request, instanceViews)
		return
	}

	now := nowMillis()
	arrived := p.registry.noteArrival(request.Id, now)

	tier := request.TpotSloMs
	allowance, expected := p.registry.newRequestAllowance(
		tier, request.PromptNumTokens, arrived, now)

	ctx := &fluidserveRequest{
		id:           request.Id,
		tier:         tier,
		ttftSloMs:    float64(request.TtftSloMs),
		promptTokens: request.PromptNumTokens,
		arrivedMs:    arrived,
		nowMs:        now,
		allowanceMs:  allowance,
		expectedToks: expected,
		nominalMs:    p.registry.NominalAllowance(tier),
	}

	for _, view := range instanceViews {
		observed := p.observeInstance(view)
		view.schedulingCtx.fluidserveRequest = ctx
		f := p.buildFlux(view, now)
		if f != nil {
			f.observedStep = observed
			if observed >= 0 {
				// Published only when the engine actually advanced, which is at
				// most once per status pull per instance. These are the series
				// the run is analysed from: the per-decision log lines do not
				// survive long enough, since the scheduler rotates its container
				// log within about a minute at the verbosity they need.
				lbl := metrics.Labels{{Name: "instance", Value: f.id}}
				metrics.Gauge("scheduler_fluidserve_observed_step_ms", lbl).Set(observed)
				metrics.Gauge("scheduler_fluidserve_predicted_step_ms", lbl).Set(f.meanStep)
				metrics.Gauge("scheduler_fluidserve_headroom_tokens", lbl).Set(f.headroom)
				// An instance with no live request has no latency constraint at
				// all. Reporting -1 rather than leaving the previous value in
				// place keeps the series honest: a stale reading would look like
				// a real constraint that is no longer there.
				capKv := f.capKv
				if math.IsInf(capKv, 0) {
					capKv = -1
				}
				metrics.Gauge("scheduler_fluidserve_cap_kv_tokens", lbl).Set(capKv)
				metrics.Gauge("scheduler_fluidserve_projected_kv_tokens", lbl).Set(f.proj)
				metrics.Gauge("scheduler_fluidserve_outflow_tokens", lbl).Set(f.outflow)
				metrics.Gauge("scheduler_fluidserve_live_requests", lbl).Set(float64(len(f.live)))
				metrics.Gauge("scheduler_fluidserve_unachievable_requests", lbl).
					Set(float64(f.unachievable))
				allowance := f.tightestAllowance
				if math.IsInf(allowance, 0) {
					allowance = -1
				}
				metrics.Gauge("scheduler_fluidserve_tightest_allowance_ms", lbl).
					Set(allowance)
			}
		}
		view.schedulingCtx.fluidserveFlux = f
	}
	p.tuner.tick(now, instanceViews)

	p.baseDispatchPolicy.calculateMetrics(inferType, request, instanceViews)
}

// stepObservation is the previous status seen from one instance, kept so that
// the mean iteration time can be measured across the interval between two
// statuses.
type stepObservation struct {
	stepID      int64
	timestampMs int64
	kvLogical   float64
	nDecode     float64
	pending     float64
}

// observeInstance measures how long the engine's iterations actually took since
// the previous status, and returns that mean in milliseconds (or -1 when it
// cannot be measured yet).
//
// The engine also publishes the duration of a single step, but only for steps
// that carried a prefill chunk and only while a bounded profiling budget lasts,
// so it is not a usable signal for decode cost. The step counter and the status
// timestamp are always published, and their differences give
//
//	mean iteration time = (elapsed engine time) / (iterations executed)
//
// over the interval. That is the same quantity the capacity model predicts, and
// measuring it as a window mean rather than per step avoids the attribution
// problem that makes single-step timings unusable here: the engine's scheduler
// runs asynchronously, so an individual step's measured gap is either almost
// zero, when the scheduler ran ahead, or the true forward time, when it blocked.
func (p *fluidserveDispatchPolicy) observeInstance(view *instanceViewScheduling) float64 {
	if view.cmsView == nil || view.cmsView.Status == nil {
		return -1
	}
	st := view.cmsView.Status
	id := view.GetInstanceId()

	cur := stepObservation{
		stepID:      int64(st.StepId),
		timestampMs: st.TimestampMs,
		kvLogical:   float64(st.SchedulerRunningToDecodeTokensNum),
		nDecode:     float64(st.SchedulerRunningToDecodeRequestsNum),
		pending: float64(st.NumUncomputedTokensAllWaitingPrefills +
			st.NumUncomputedTokensSchedulerRunningPrefills),
	}

	p.obsMu.Lock()
	if p.lastObs == nil {
		p.lastObs = map[string]stepObservation{}
	}
	prev, seen := p.lastObs[id]
	// Only advance the stored observation when the engine has actually moved on,
	// so that repeated scheduling calls between two status pulls do not each
	// contribute a sample of the same interval.
	if !seen || cur.stepID > prev.stepID {
		p.lastObs[id] = cur
	}
	p.obsMu.Unlock()

	if !seen || cur.stepID <= prev.stepID || cur.timestampMs <= prev.timestampMs {
		return -1
	}
	steps := cur.stepID - prev.stepID
	elapsed := float64(cur.timestampMs - prev.timestampMs)
	// Guard against a status pair that straddles an engine restart or a long
	// stall, where the ratio would describe neither interval.
	if steps > 10000 || elapsed > 30000 {
		return -1
	}
	// An engine with nothing to decode still advances its step counter, but the
	// wall time between those steps is time spent waiting for work rather than
	// time spent computing. Dividing one by the other would report iteration
	// times of hundreds of milliseconds for an idle instance, which is the
	// opposite of the truth and would make the safety loop back off exactly when
	// there is most room.
	if prev.nDecode < 1 || cur.nDecode < 1 {
		return -1
	}
	measured := elapsed / float64(steps)

	// Feeding this back into the decode law is off unless asked for. The test
	// available here -- no prefill queued at either end of the interval -- does
	// not certify that no prefill ran DURING it: statuses arrive every 500 ms
	// and the engine executes around 25 iterations in between, so a queue that
	// formed and drained inside the interval is invisible. Measured live, that
	// misattribution drove the correction to 1.73, which in turn drove the
	// safety factor to its ceiling and cut throughput by half. The offline law
	// is fitted over a far wider range than one run visits (377,838 iterations,
	// cross-checked against an independent fit) and read 0.9998 against an idle
	// engine, so the seed is the better estimate until a signal exists that can
	// separate the two costs within an interval.
	if p.cfg.enableOnlineCalibration &&
		prev.pending == 0 && cur.pending == 0 && prev.nDecode >= 1 {
		p.capacity.observe(prev.kvLogical, prev.nDecode, 0, measured)
	}
	return measured
}

func (p *fluidserveDispatchPolicy) buildFlux(
	view *instanceViewScheduling, nowMs int64) *instanceFlux {

	if view.cmsView == nil || view.cmsView.Status == nil {
		return nil
	}
	st := view.cmsView.Status

	f := &instanceFlux{
		id:         view.GetInstanceId(),
		kvPhysical: float64(st.NumUsedGpuTokens),
		kvCapacity: float64(st.NumTotalGpuTokens),
		stepID:     int64(st.StepId),
		chunk:      8192,
	}
	if view.cmsView.Metadata != nil && view.cmsView.Metadata.MaxNumBatchedTokens > 0 {
		f.chunk = float64(view.cmsView.Metadata.MaxNumBatchedTokens)
	}

	// The latency model was fitted against the logical KV count (the sum of
	// each request's computed tokens), which is what the engine's own decode
	// accounting reports and what prefix-cache sharing inflates past the
	// physical pool. Queries have to use the same quantity as the fit.
	f.kvLogical = float64(st.HybridSchedulerWaitingToDecodeTokensNum +
		st.SchedulerWaitingToDecodeTokensNum +
		st.SchedulerRunningToDecodeTokensNum +
		st.NumTokensLoadingRequests +
		view.cmsView.NumTokensInflightDispatchDecodeRequests)
	f.nDecode = float64(st.HybridSchedulerWaitingToDecodeRequestsNum +
		st.NumLoadingRequests +
		st.SchedulerWaitingToDecodeRequestsNum +
		st.SchedulerRunningToDecodeRequestsNum +
		view.cmsView.NumInflightDispatchDecodeRequests)
	f.pendingPrefill = math.Max(0, float64(
		st.NumUncomputedTokensAllWaitingPrefills+
			st.NumUncomputedTokensSchedulerRunningPrefills+
			view.cmsView.NumUncomputedTokensInflightDispatchPrefillRequests))

	f.live = p.registry.reconcile(f.id, int(st.SchedulerRunningToDecodeRequestsNum),
		f.stepID, nowMs)
	f.preferredBudget = p.registry.preferredBudgetMs(f.id)

	floor := p.capacity.floorStepMs()
	f.tightestAllowance = math.Inf(1)
	f.tightestNominal = math.Inf(1)
	for i := range f.live {
		if f.live[i].allowanceMs < floor {
			// No batch composition can serve this request within its remaining
			// budget, because even an iteration on an empty instance costs more
			// than the time it has left per token. Holding the instance to that
			// allowance would stop it accepting anything else without helping
			// this request, so it is excluded from the constraint and served on
			// a best-effort basis.
			f.live[i].unachievable = true
			f.unachievable++
			continue
		}
		f.achievable++
		if f.live[i].allowanceMs < f.tightestAllowance {
			f.tightestAllowance = f.live[i].allowanceMs
		}
		if f.live[i].nominalMs > 0 && f.live[i].nominalMs < f.tightestNominal {
			f.tightestNominal = f.live[i].nominalMs
		}
	}

	f.inflow = f.nDecode * float64(p.cfg.horizonSteps)
	f.outflow = p.expectedOutflow(f.live, p.cfg.horizonSteps)
	if !p.cfg.enableFlux {
		// Ablation: fall back to judging the instance on its current occupancy
		// alone, which is what a level-based controller does.
		f.inflow, f.outflow = 0, 0
	}
	f.proj = f.kvLogical + f.inflow - f.outflow
	if f.proj < 0 {
		f.proj = 0
	}

	f.capKv = p.capacity.maxKvForAllowance(
		f.tightestAllowance*fsAllowanceUtilisation, f.nDecode,
		f.pendingPrefill, f.chunk, p.cfg.horizonSteps)

	// Physical capacity, converted into the logical units everything else is in.
	// The ratio is measured rather than assumed because prefix-cache sharing
	// makes it workload dependent: EXP-16 saw 7.0M logical tokens on a fleet
	// whose physical pool is about 0.6M, a ratio near 0.09.
	//
	// The floor matters. An instance that has just drained reports zero physical
	// tokens while its logical decode accounting is still catching up, giving a
	// ratio near zero and, once divided through, a memory capacity three orders
	// of magnitude too large. The floor is set an order of magnitude below the
	// lowest ratio ever measured, so it bounds nonsense without interfering with
	// a real reading.
	ratio := 1.0
	if f.kvLogical > 1000 && f.kvPhysical > 1000 {
		ratio = f.kvPhysical / f.kvLogical
	}
	if ratio < 0.01 {
		ratio = 0.01
	}
	f.capMem = f.kvCapacity * fsMemorySafety / ratio

	limit := math.Min(f.capKv, f.capMem)
	f.headroom = limit - f.proj
	f.meanStep = p.capacity.meanStepMs(f.kvLogical, f.nDecode, f.pendingPrefill,
		f.chunk, p.cfg.horizonSteps)
	return f
}

// expectedOutflow is the KV expected to be released over the horizon, reduced
// by a multiple of its standard deviation.
//
// Each request contributes its whole footprint with the probability that it
// finishes within the horizon, so the sum is over independent Bernoulli terms
// scaled by the footprint, and the variance follows directly. Subtracting
// z standard deviations turns the mean into a lower confidence bound, which is
// the conservative direction: releasing less than predicted only costs some
// utilisation, while releasing more than predicted means the instance was
// admitted past what it can hold.
func (p *fluidserveDispatchPolicy) expectedOutflow(live []liveRequest, horizon int) float64 {
	mean, variance := 0.0, 0.0
	for _, r := range live {
		prof := p.lengths.forTier(r.tier)
		if prof == nil {
			continue
		}
		q := prof.completionProb(r.j, horizon)
		mean += q * r.kvTokens
		variance += q * (1 - q) * r.kvTokens * r.kvTokens
	}
	out := mean - p.cfg.zSafety*math.Sqrt(variance)
	if out < 0 {
		return 0
	}
	return out
}

// fluidserveSelector holds the whole decision. Everything is done here rather
// than split across filters because the choice is not "which instances are
// acceptable" followed by "which is best" -- waiting is one of the options, and
// deciding to wait needs to compare against every instance at once.
type fluidserveSelector struct {
	policy *fluidserveDispatchPolicy
}

type candidate struct {
	flux          *instanceFlux
	view          *instanceViewScheduling
	feasible      bool
	score         float64
	harm          float64
	mismatch      float64
	meanBefore    float64
	meanAfter     float64
	allowAfter    float64
	headroomAfter float64
}

func (s *fluidserveSelector) selectInstance(
	instances map[string]*instanceViewScheduling, fallback bool) *instanceViewScheduling {

	p := s.policy
	if len(instances) == 0 {
		return nil
	}

	// Every view carries the same request context; take it from any of them.
	var req *fluidserveRequest
	for _, v := range instances {
		req = v.schedulingCtx.fluidserveRequest
		break
	}
	if req == nil {
		klog.Warning("FluidServe selector: no request context, falling back to first instance")
		return anyInstance(instances)
	}

	cands := make([]candidate, 0, len(instances))
	for _, view := range instances {
		f := view.schedulingCtx.fluidserveFlux
		if f == nil {
			continue
		}
		cands = append(cands, p.evaluate(f, view, req))
	}
	if len(cands) == 0 {
		return nil
	}
	// Deterministic order so a run can be reproduced; Go randomises map
	// iteration, which would otherwise make two identical runs route differently.
	sort.Slice(cands, func(a, b int) bool {
		if cands[a].feasible != cands[b].feasible {
			return cands[a].feasible
		}
		if cands[a].score != cands[b].score {
			return cands[a].score > cands[b].score
		}
		return cands[a].flux.id < cands[b].flux.id
	})

	best := cands[0]
	if best.feasible {
		p.commit(best, req, "route")
		return best.view
	}

	// Nothing can take the request within its budget right now. Waiting at the
	// gateway costs nothing and keeps every option open, so it is preferred
	// while the request can still afford it. What it cannot afford is to wait
	// past the point where even an immediate placement would miss the
	// time-to-first-token budget.
	if p.cfg.enablePend && best.harm > fsPendHarmThreshold && p.canWait(best, req) {
		metrics.Counter("scheduler_fluidserve_decisions_total",
			metrics.Labels{{Name: "decision", Value: "pend"}}).Inc()
		klog.V(3).Infof("FluidServe pends request %s (tier %dms, waited %dms): "+
			"best instance %s headroom %.0f needs %.0f, placing it would consume "+
			"%.1f of the slack its requests have left",
			req.id, req.tier, req.nowMs-req.arrivedMs, best.flux.id,
			best.flux.headroom, s.policy.costOf(req), best.harm)
		return nil
	}

	// Out of time: place it on the instance that degrades the fleet least, and
	// accept that the budget may be missed. Refusing outright would not make it
	// any more likely to be served.
	p.commit(best, req, "force")
	return best.view
}

func anyInstance(instances map[string]*instanceViewScheduling) *instanceViewScheduling {
	ids := make([]string, 0, len(instances))
	for id := range instances {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return instances[ids[0]]
}

// costOf is the KV a request adds over the horizon: its prompt, plus the tokens
// it will produce while the horizon lasts. A request that will outlive the
// horizon is charged only for the part that falls inside it, because the
// horizon is also the window over which the release of everything else is
// counted.
func (p *fluidserveDispatchPolicy) costOf(req *fluidserveRequest) float64 {
	growth := math.Min(float64(p.cfg.horizonSteps), req.expectedToks)
	return float64(req.promptTokens) + growth
}

func (p *fluidserveDispatchPolicy) evaluate(
	f *instanceFlux, view *instanceViewScheduling, req *fluidserveRequest) candidate {

	c := candidate{flux: f, view: view}
	cost := p.costOf(req)

	// State after admitting this request.
	newPending := f.pendingPrefill + float64(req.promptTokens)
	newN := f.nDecode + 1
	newKv := f.proj + cost
	c.allowAfter = math.Min(f.tightestAllowance, req.allowanceMs)
	c.meanAfter = p.capacity.meanStepMs(newKv, newN, newPending, f.chunk, p.cfg.horizonSteps)
	c.headroomAfter = f.headroom - cost

	c.feasible = c.headroomAfter >= 0 &&
		!math.IsInf(c.meanAfter, 0) &&
		c.meanAfter <= c.allowAfter*fsAllowanceUtilisation

	c.meanBefore = f.meanStep
	if p.cfg.enableExternality {
		c.harm = p.harmToIncumbents(f, c.meanBefore, c.meanAfter)
		// Matched against what the instance has been serving rather than what
		// is on it at this instant, so that an instance which happens to be
		// empty still attracts the class it has been handling.
		c.mismatch = budgetMismatch(f.preferredBudget, req.nominalMs)
	}

	// The terms are put on one scale by expressing each as a fraction of the
	// instance's physical capacity, so the weight means the same thing
	// regardless of instance size. The memory capacity is used as the scale
	// rather than the binding limit because the binding limit goes negative
	// once the latency budget cannot be met at any occupancy, and dividing by a
	// quantity that changes sign makes the ranking meaningless exactly where it
	// matters most.
	scale := math.Max(f.capMem, 1)
	room := c.headroomAfter / scale
	if room > 1 {
		room = 1
	} else if room < -1 {
		room = -1
	}
	c.score = room - p.cfg.alphaExternality*(c.harm+fsMismatchWeight*c.mismatch)
	return c
}

// harmToIncumbents prices what placing this request does to the requests
// already on the instance.
//
// This is where the separation between classes comes from, and it is a
// statement about the WORK a request brings, not about the budget it declares.
// A long prompt makes several of the next iterations carry a prefill chunk, and
// every request decoding on that instance waits through them, so admitting one
// alongside requests that are close to their budget is what breaks them. The
// earlier formulation charged only for binding an instance to a tighter budget,
// which never fired in the case that matters: an agent request with a 22k-token
// prompt has a LOOSER per-token budget than an interactive one, so it was
// charged nothing for landing on an instance full of interactive requests and
// pushing all of them past their limit.
//
// Requests whose budget is already spent contribute nothing. They are going to
// miss whatever happens next, so further delay is not an additional loss, and
// counting it would make the instance that has already absorbed the heavy work
// look expensive -- which is exactly the instance that should keep absorbing
// it. That asymmetry is what makes the heavy class collect on a few instances
// while the rest stay clean, with none of them reserved in advance.
func (p *fluidserveDispatchPolicy) harmToIncumbents(
	f *instanceFlux, meanBefore, meanAfter float64) float64 {

	delta := meanAfter - meanBefore
	if delta <= 0 || math.IsInf(delta, 0) {
		return 0
	}
	harm := 0.0
	for _, r := range f.live {
		if r.unachievable {
			continue
		}
		slack := r.allowanceMs - meanBefore
		if slack <= 0 {
			continue // already past its budget; nothing more to lose here
		}
		h := delta / slack
		if h > fsHarmCap {
			h = fsHarmCap
		}
		harm += h
	}
	return harm
}

// Ordering budget affinity ahead of free space was tried and measured worse:
// 32.8 equal-weight against 43.2 for the weighted sum, with the routing
// concentration of every class falling. The reason is that an instance with
// nothing on it has no budget in force and therefore no mismatch, so it ranked
// alongside instances already serving this budget -- and being empty, it also
// won on free space. Every time an instance drained it attracted whichever
// class asked next, so the assignment never settled. Making "no constraint" its
// own rank between "matching" and "mismatched" would fix that, but the weighted
// sum already produced the best result measured, so the ordering is left as a
// weighted sum and this is recorded rather than re-attempted here.

// budgetMismatch measures how far a request's latency budget is from the budget
// the instance is currently held to, as the log of the ratio: zero when they
// match, growing symmetrically in either direction.
//
// This is what makes requests of the same class collect on the same instances
// without any instance being assigned to a class. An instance's admissible
// occupancy is set by the tightest budget on it, so mixing budgets wastes
// capacity in both directions: a tight request lands on a loose instance and
// pulls its whole capacity down, while a loose request lands on a tight
// instance and gets less room than its budget entitles it to. An instance with
// nothing on it has no constraint and so no mismatch, which leaves it free to
// take whatever arrives first and become the home for that budget.
//
// The earlier form of this term measured the same thing in tokens of capacity
// given up, and had to be divided by the instance size to be comparable with
// the other terms. Measured, that came to 0.013 against a term spanning
// [-1, 1], so it never affected which instance was chosen: all four instances
// ended a run held to the same budget, which is the absence of any separation.
func budgetMismatch(instanceAllowanceMs, requestAllowanceMs float64) float64 {
	// A zero or infinite instance budget means the instance has no preference
	// yet, which leaves it open to whatever arrives.
	if math.IsInf(instanceAllowanceMs, 0) ||
		instanceAllowanceMs <= 0 || requestAllowanceMs <= 0 {
		return 0
	}
	ratio := requestAllowanceMs / instanceAllowanceMs
	if ratio < 1 {
		ratio = 1 / ratio
	}
	return math.Log(ratio)
}

// canWait decides whether the request still has room to be held at the gateway.
//
// The limit is not the whole time-to-first-token budget: the request still has
// to be prefilled once placed, and prefill of a long prompt takes several
// iterations of several hundred milliseconds each. Waiting past the point where
// prefill would no longer fit inside the budget converts a request that could
// have been served late into one that is certain to miss.
func (p *fluidserveDispatchPolicy) canWait(best candidate, req *fluidserveRequest) bool {
	if req.ttftSloMs <= 0 {
		return false
	}
	waited := float64(req.nowMs - req.arrivedMs)
	prefillMs := p.prefillEstimateMs(req, best.flux)
	deadline := req.ttftSloMs - prefillMs - p.cfg.ttftSafetyMs
	// Never spend more than a fraction of the budget waiting. The condition
	// above only asks whether a placement made at the last possible moment
	// could still produce a first token in time, which for an interactive
	// request works out at over 90% of its budget: by the time the hold ends,
	// the request is scored as a miss whatever happens next, so the hold
	// protected the incumbents at the cost of certainly losing this request.
	if cap := req.ttftSloMs * fsMaxPendFraction; deadline > cap {
		deadline = cap
	}
	if deadline < p.cfg.pendGraceMs {
		deadline = p.cfg.pendGraceMs
	}
	return waited < deadline
}

func (p *fluidserveDispatchPolicy) prefillEstimateMs(
	req *fluidserveRequest, f *instanceFlux) float64 {

	chunk := 8192.0
	if f != nil && f.chunk > 0 {
		chunk = f.chunk
	}
	steps := prefillSteps(float64(req.promptTokens), chunk)
	if steps <= 0 {
		return 0
	}
	per := p.capacity.prefillStepMs(math.Min(float64(req.promptTokens), chunk))
	if math.IsInf(per, 0) {
		return 0
	}
	queued := 0.0
	if f != nil {
		queued = prefillSteps(f.pendingPrefill, chunk) * per
	}
	return steps*per + queued
}

func (p *fluidserveDispatchPolicy) commit(c candidate, req *fluidserveRequest, kind string) {
	p.registry.onDispatch(c.flux.id, req.id, req.tier, req.promptTokens,
		c.flux.chunk, c.flux.stepID, req.nowMs)

	metrics.Counter("scheduler_fluidserve_decisions_total",
		metrics.Labels{{Name: "decision", Value: kind}}).Inc()
	metrics.Histogram("scheduler_fluidserve_headroom_at_dispatch",
		metrics.Labels{}).Observe(c.headroomAfter)
	metrics.Histogram("scheduler_fluidserve_harm", metrics.Labels{}).Observe(c.harm)
	metrics.Histogram("scheduler_fluidserve_budget_mismatch",
		metrics.Labels{}).Observe(c.mismatch)

	klog.V(3).Infof("FluidServe %s request %s (tier %dms, prompt %d) -> %s: "+
		"headroom %.0f->%.0f cap(kv %.0f mem %.0f) proj %.0f meanStep %.1f->%.1fms "+
		"allowance %.1fms harm %.2f bindingLoss %.0f live %d (%d unachievable)",
		kind, req.id, req.tier, req.promptTokens, c.flux.id,
		c.flux.headroom, c.headroomAfter, c.flux.capKv, c.flux.capMem, c.flux.proj,
		c.flux.meanStep, c.meanAfter, c.allowAfter, c.harm, c.mismatch,
		len(c.flux.live), c.flux.unachievable)
}

// safetyTuner is C6: the slow loop. The fast path already refuses to admit past
// what the capacity model allows, so this loop only adjusts how much margin
// that model keeps, and a slow or wrong adjustment costs utilisation rather
// than correctness.
//
// The signal is the measured iteration time against what the capacity model
// PREDICTED for the same interval, not against the budget in force. The
// difference matters: under sustained overload every instance runs past the
// budget no matter what is routed where, so a budget-based trigger fires
// continuously and drives the margin to its ceiling, which throttles the fleet
// without making any request meet its SLO. Measured live, that is exactly what
// happened -- the overshoot rate sat at 1.0 and the safety factor pinned at its
// maximum. What the margin should respond to is the estimate being optimistic,
// which is the model residual.
//
// The response is deliberately asymmetric. Margin is given back slowly and only
// after several consecutive quiet windows, but taken immediately on the first
// window where the estimate was substantially optimistic, because occupancy
// that runs past what the instance can hold ends in preemption, and recovering
// from preemption costs far more than the utilisation given up by backing off
// early.
type safetyTuner struct {
	cfg *fluidserveConfig

	lastTickMs   int64
	windowMs     int64
	samples      int
	overshoots   int
	quietWindows int

	baseZ float64
}

func newSafetyTuner(cfg *fluidserveConfig) *safetyTuner {
	return &safetyTuner{cfg: cfg, windowMs: 5000, baseZ: cfg.zSafety}
}

const (
	// Fraction of intervals in which the engine was substantially slower than
	// predicted before the margin is widened.
	fsOvershootTrigger = 0.25
	// How much slower than predicted counts as substantially. Below this the
	// difference is within the spread the offline fit already reports
	// (6% median, 21% at the 90th percentile).
	fsResidualTrigger = 2.0
	fsZBackoffFactor  = 1.5
	fsZStep           = 0.05
	fsZMin            = 0.5
	fsZMax            = 3.0
	fsQuietWindows    = 3
)

func (t *safetyTuner) tick(nowMs int64, views map[string]*instanceViewScheduling) {
	for _, v := range views {
		f := v.schedulingCtx.fluidserveFlux
		if f == nil || f.observedStep <= 0 || f.meanStep <= 0 ||
			math.IsInf(f.meanStep, 0) {
			continue
		}
		t.samples++
		if f.observedStep > f.meanStep*fsResidualTrigger {
			t.overshoots++
		}
	}

	if t.lastTickMs == 0 {
		t.lastTickMs = nowMs
		return
	}
	if nowMs-t.lastTickMs < t.windowMs {
		return
	}
	t.lastTickMs = nowMs
	if t.samples < 20 {
		t.samples, t.overshoots = 0, 0
		return
	}

	rate := float64(t.overshoots) / float64(t.samples)
	t.samples, t.overshoots = 0, 0

	if rate > fsOvershootTrigger {
		t.quietWindows = 0
		t.cfg.zSafety = math.Min(fsZMax, t.cfg.zSafety*fsZBackoffFactor)
		klog.V(2).Infof("FluidServe tuner: %.1f%% of iterations over budget, "+
			"raising safety factor to %.2f", rate*100, t.cfg.zSafety)
	} else {
		t.quietWindows++
		if t.quietWindows >= fsQuietWindows && t.cfg.zSafety > fsZMin {
			t.quietWindows = 0
			t.cfg.zSafety = math.Max(fsZMin, t.cfg.zSafety-fsZStep)
			klog.V(3).Infof("FluidServe tuner: quiet, lowering safety factor to %.2f",
				t.cfg.zSafety)
		}
	}
	metrics.Gauge("scheduler_fluidserve_z_safety", metrics.Labels{}).Set(t.cfg.zSafety)
	metrics.Gauge("scheduler_fluidserve_overshoot_rate", metrics.Labels{}).Set(rate)
}

// newFluidserveDispatchFullMode assembles the policy. Only Neutral is defined
// because the fleet is co-located; baseDispatchPolicy is a map of pointers, so
// an infer type that is present in the cluster but missing here would be a nil
// dereference rather than a graceful skip, which is why calculateMetrics guards
// on it.
func newFluidserveDispatchFullMode(p *options.SchedulerConfig) *fluidserveDispatchPolicy {
	predictor := GetLatencyPredictor(p.TtftProfilingDataPath, p.TpotProfilingDataPath)
	profile, lengths := loadFluidserveProfile(p.FluidserveProfilePath)

	budgets, err := parseClassBudgets(p.FluidserveClassBudgets)
	if err != nil {
		panic(fmt.Sprintf("invalid --fluidserve-class-budgets: %v", err))
	}

	cfg := fluidserveConfig{
		horizonSteps:            p.FluidserveHorizonSteps,
		zSafety:                 p.FluidserveZSafety,
		alphaExternality:        p.FluidserveAlphaExternality,
		enablePend:              p.FluidserveEnablePend,
		enableExternality:       p.FluidserveEnableExternality,
		enableFlux:              p.FluidserveEnableFlux,
		enableOnlineCalibration: p.FluidserveEnableOnlineCalibration,
		pendGraceMs:             float64(p.FluidservePendGraceMs),
		ttftSafetyMs:            float64(p.FluidserveTtftSafetyMs),
	}
	if cfg.horizonSteps <= 0 {
		panic("--fluidserve-horizon-steps must be positive")
	}

	policy := &fluidserveDispatchPolicy{
		cfg:      cfg,
		capacity: newCapacityModel(profile, predictor),
		lengths:  lengths,
		registry: newRequestRegistry(lengths, budgets),
		lastObs:  map[string]stepObservation{},
	}
	policy.tuner = newSafetyTuner(&policy.cfg)
	policy.baseDispatchPolicy = baseDispatchPolicy{
		consts.InferTypeNeutral: {
			// No load metrics: the decision reads the instance state directly
			// rather than through the metric abstraction, because it needs
			// several quantities together and their joint interpretation.
			metrics: map[string]func() instanceSchedulingMetric{},
			globalFilters: []globalFilter{
				&failoverFilter{failoverDomain: p.FailoverDomain},
			},
			// Only liveness filters here. Anything that removed instances on
			// load grounds would also trigger the fallback pass, and the
			// fallback pass exists to relax constraints -- which is the opposite
			// of what should happen when no instance can meet a budget. Holding
			// the request is the correct response, and that is expressed by the
			// selector returning nothing.
			singleInstanceFilters: []singleInstanceFilter{
				&schedulabilityFilter{},
				&stalenessFilter{instanceStalenessSeconds: p.InstanceStalenessSeconds},
			},
			selectors: &fluidserveSelector{policy: policy},
		},
	}

	klog.Infof("FluidServe dispatch policy created: horizon %d steps, z=%.2f, "+
		"alpha=%.2f, pend=%v (grace %dms, ttft margin %dms), externality=%v, flux=%v, "+
		"onlineCalibration=%v, budgets %q",
		cfg.horizonSteps, cfg.zSafety, cfg.alphaExternality, cfg.enablePend,
		p.FluidservePendGraceMs, p.FluidserveTtftSafetyMs, cfg.enableExternality,
		cfg.enableFlux, cfg.enableOnlineCalibration, p.FluidserveClassBudgets)

	go policy.reportLoop()
	return policy
}

// reportLoop publishes state that is otherwise only visible in per-decision log
// lines, which do not survive a run long enough to be analysed: the scheduler
// rotates its container log in about a minute at the verbosity these lines need.
func (p *fluidserveDispatchPolicy) reportLoop() {
	for range time.Tick(5 * time.Second) {
		metrics.Gauge("scheduler_fluidserve_capacity_correction",
			metrics.Labels{}).Set(p.capacity.correctionFactor())
		byCount, bySurvival, byAge := p.registry.counters()
		metrics.Gauge("scheduler_fluidserve_retired_total",
			metrics.Labels{{Name: "reason", Value: "count"}}).Set(float64(byCount))
		metrics.Gauge("scheduler_fluidserve_retired_total",
			metrics.Labels{{Name: "reason", Value: "survival"}}).Set(float64(bySurvival))
		metrics.Gauge("scheduler_fluidserve_retired_total",
			metrics.Labels{{Name: "reason", Value: "age"}}).Set(float64(byAge))
		p.registry.logSummary()
	}
}
