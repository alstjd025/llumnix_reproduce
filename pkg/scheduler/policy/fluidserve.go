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

// The decision is a ladder, and each rung is settled by ONE quantity. An
// earlier version scored every instance with a weighted sum of four terms and
// picked the maximum, which needed four weights and produced a failure that no
// weight could fix: under overload every instance is over its budget, so the
// free-space term is large and negative for all of them and its differences are
// compressed, while the class-affinity term still spans its full range. Affinity
// then decided the ranking, and affinity is positive feedback -- the instance
// already holding the most of a class attracts more of it. One instance in the
// dynamic run ended up holding a queue of 5,569 requests while the other three
// engines were completely idle for 27 minutes, and because a dispatched request
// cannot be recalled, that was unrecoverable.
//
// Separating the rungs removes every weight and makes the ordering structural:
//
//	feasible instances exist        route to the one already holding the most of
//	                                this class, breaking ties by free space.
//	                                Affinity is applied INSIDE the feasible set,
//	                                so a preference can never override a capacity
//	                                limit -- that is the capacity stop the
//	                                weighted sum lacked.
//	none feasible, time to wait     hold the request at the gateway.
//	none feasible, no time left,
//	  and placing it now still
//	  misses its own budget         reject it. Rejecting a request that is
//	                                already certain to miss frees the capacity
//	                                that decides whether the requests around it
//	                                miss too.
//	otherwise                       place it where it destroys the least: the
//	                                instance whose still-achievable requests lose
//	                                the least slack. On an instance already
//	                                saturated by one class that quantity is near
//	                                zero, so overload collects where it is
//	                                already lost instead of spreading.

const (
	// Fraction of physical KV capacity treated as usable. The engine begins
	// preempting before the pool is literally full, and a request's last block
	// is partially used, so the last few percent are not available in practice.
	fsMemorySafety = 0.95
	// How much of the budget an instance is allowed to plan against. The rest is
	// left for the requests already there to absorb a burst without immediately
	// violating.
	fsAllowanceUtilisation = 0.90
	// Ceiling on how much one incumbent can contribute to the damage estimate. A
	// request whose remaining slack is nearly zero would otherwise divide by
	// almost nothing and let a single incumbent decide the ranking.
	fsHarmCap = 10.0
)

type fluidserveConfig struct {
	horizonSteps int
	zSafety      float64
	ttftSafetyMs float64
	// Ablation switches. These select which mechanism is in play, they are not
	// quantities to tune.
	enablePend     bool
	enableShed     bool
	enableAffinity bool
	enableFlux     bool
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
	expectedToks float64
	// nominalMs is the per-token pace this class is promised: the tier key for a
	// per-token budget, and the whole budget divided by the expected output for
	// an end-to-end one.
	nominalMs float64
	// isE2E and budgetMs describe an end-to-end budget, where time spent waiting
	// is spent out of the same account as time spent decoding. For a per-token
	// budget the wait is judged separately, against ttftSloMs.
	isE2E    bool
	budgetMs float64
}

// instanceFlux is one instance's state at the moment of a decision.
type instanceFlux struct {
	id string

	kvLogical  float64 // KV tokens as the latency model counts them
	kvPhysical float64 // blocks the engine has allocated
	kvCapacity float64 // physical pool size
	// sharing is the ratio of physical blocks to logical tokens, which is how
	// far prefix reuse stretches the pool. It converts the physical capacity
	// into the logical units everything else is counted in.
	sharing        float64
	nDecode        float64
	pendingPrefill float64
	chunk          float64
	stepID         int64

	live []liveRequest
	// gateAllowance sets what this instance may take on: the tightest NOMINAL
	// pace among the requests on it that are still achievable. Nominal rather
	// than remaining, because the remaining budget shrinks as an instance falls
	// behind, so using it here would mean lateness reduces capacity, which
	// causes more lateness -- a feedback loop that was measured collapsing the
	// fleet. The nominal pace is a property of the class, so the gate is stable.
	gateAllowance float64
	// tightestAllowance is the tightest REMAINING budget on the instance. It
	// does not gate admission; it is what the damage estimate is measured
	// against and what the telemetry reports.
	tightestAllowance float64
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

	obsMu   sync.Mutex
	lastObs map[string]stepObservation

	// shedMu guards the ids the selector decided to reject. The gateway holds a
	// request across many scheduling calls, so "no endpoint" on its own cannot
	// tell the difference between "not yet" and "never"; the id is recorded here
	// and consumed by the one Schedule() call that is unwinding, which is what
	// turns the decision into a 503 the client sees immediately instead of one
	// it sees when the hold times out.
	shedMu  sync.Mutex
	shedIDs map[string]int64
}

// noteShed records that this request was rejected on purpose.
func (p *fluidserveDispatchPolicy) noteShed(requestID string) {
	p.shedMu.Lock()
	defer p.shedMu.Unlock()
	if p.shedIDs == nil {
		p.shedIDs = map[string]int64{}
	}
	now := nowMillis()
	p.shedIDs[requestID] = now
	// The entry is consumed by the same call in the normal path, so anything
	// still here is from a call that unwound another way.
	if len(p.shedIDs) > 4096 {
		for id, t := range p.shedIDs {
			if now-t > 60_000 {
				delete(p.shedIDs, id)
			}
		}
	}
}

// admissionRejected reports and clears the flag. It satisfies the interface the
// generic scheduling path uses to turn an empty result into a rejection rather
// than a retry.
func (p *fluidserveDispatchPolicy) admissionRejected(requestID string) bool {
	p.shedMu.Lock()
	defer p.shedMu.Unlock()
	if _, ok := p.shedIDs[requestID]; !ok {
		return false
	}
	delete(p.shedIDs, requestID)
	return true
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
	nominal, expected, isE2E, budgetMs := p.registry.requestBudget(tier)

	ctx := &fluidserveRequest{
		id:           request.Id,
		tier:         tier,
		ttftSloMs:    float64(request.TtftSloMs),
		promptTokens: request.PromptNumTokens,
		arrivedMs:    arrived,
		nowMs:        now,
		expectedToks: expected,
		nominalMs:    nominal,
		isE2E:        isE2E,
		budgetMs:     budgetMs,
	}

	for _, view := range instanceViews {
		observed := p.observeInstance(view)
		view.schedulingCtx.fluidserveRequest = ctx
		f := p.buildFlux(view, now)
		if f != nil {
			f.observedStep = observed
			if observed >= 0 {
				// The correction is fed here, where the prediction and the
				// measurement of the same interval are both in hand.
				p.capacity.noteResidual(f.meanStep, observed)
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
				metrics.Gauge("scheduler_fluidserve_cap_kv_tokens", lbl).
					Set(finiteOrMinusOne(f.capKv))
				metrics.Gauge("scheduler_fluidserve_projected_kv_tokens", lbl).Set(f.proj)
				metrics.Gauge("scheduler_fluidserve_outflow_tokens", lbl).Set(f.outflow)
				metrics.Gauge("scheduler_fluidserve_live_requests", lbl).Set(float64(len(f.live)))
				metrics.Gauge("scheduler_fluidserve_unachievable_requests", lbl).
					Set(float64(f.unachievable))
				metrics.Gauge("scheduler_fluidserve_tightest_allowance_ms", lbl).
					Set(finiteOrMinusOne(f.tightestAllowance))
				metrics.Gauge("scheduler_fluidserve_gate_allowance_ms", lbl).
					Set(finiteOrMinusOne(f.gateAllowance))
			}
		}
		view.schedulingCtx.fluidserveFlux = f
	}
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

	// The pace the instance is delivering right now. It is needed before the
	// loop because a request the instance is ALREADY failing cannot be made to
	// fail by admitting another one, and should therefore not be able to close
	// the instance to everything else.
	f.meanStep = p.capacity.meanStepMs(f.kvLogical, f.nDecode, f.pendingPrefill,
		f.chunk, p.cfg.horizonSteps)

	floor := p.capacity.floorStepMs()
	f.tightestAllowance = math.Inf(1)
	f.gateAllowance = math.Inf(1)
	for i := range f.live {
		if f.live[i].allowanceMs < floor {
			// No batch composition can serve this request within its remaining
			// budget, because even an iteration on an empty instance costs more
			// than the time it has left per token. Holding the instance to that
			// request would stop it accepting anything else without helping this
			// request, so it is excluded from the gate and from the damage
			// estimate, and served on a best-effort basis.
			f.live[i].unachievable = true
			f.unachievable++
			continue
		}
		f.achievable++
		if f.live[i].nominalMs > 0 && f.live[i].nominalMs < f.gateAllowance {
			f.gateAllowance = f.live[i].nominalMs
		}
		// The protection is against CAUSING a miss. A request whose remaining
		// budget per token is already below what the instance is delivering is
		// being missed now, and refusing new work does not rescue it: the batch
		// it is in keeps running at the pace it is running at, and only shrinks
		// as those requests finish. Letting it set the limit would close the
		// instance to every other class while saving nobody. Measured, that is
		// what happened: instances sat at a tightest allowance of 28 ms against
		// a delivered 32 ms and refused work while holding a third of their
		// admissible occupancy.
		if f.live[i].allowanceMs < f.meanStep {
			continue
		}
		if f.live[i].allowanceMs < f.tightestAllowance {
			f.tightestAllowance = f.live[i].allowanceMs
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
		f.gateAllowance*fsAllowanceUtilisation, f.nDecode,
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
	if ratio > 1 {
		ratio = 1
	}
	f.capMem = f.kvCapacity * fsMemorySafety / ratio
	f.sharing = ratio

	limit := math.Min(f.capKv, f.capMem)
	f.headroom = limit - f.proj
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
// acceptable" followed by "which is best" -- waiting and rejecting are also
// options, and choosing either needs to compare against every instance at once.
type fluidserveSelector struct {
	policy *fluidserveDispatchPolicy
}

type candidate struct {
	flux     *instanceFlux
	view     *instanceViewScheduling
	feasible bool
	// share is the fraction of the requests on this instance that belong to the
	// arriving request's class. It orders the FEASIBLE set only.
	share float64
	// harm is the slack that the still-achievable requests on this instance
	// would lose. It orders the INFEASIBLE set only.
	harm float64
	// room is free space after admitting, as a fraction of the instance's
	// physical capacity. It breaks ties in both sets.
	room          float64
	meanBefore    float64
	meanAfter     float64
	gateAfter     float64
	headroomAfter float64
	// missesOwnBudget is true when placing the request here, right now, would
	// still miss the budget the request itself is judged by.
	missesOwnBudget bool
	prefillMs       float64
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
	sortCandidates(cands)

	best := cands[0]
	if best.feasible {
		p.commit(best, req, "route")
		return best.view
	}

	// Nothing can take the request within the pace its class was promised.
	// Waiting at the gateway costs the fleet nothing and keeps every option
	// open, so it is preferred for as long as the request can still afford it.
	if p.cfg.enablePend && p.canWait(best, req) {
		metrics.Counter("scheduler_fluidserve_decisions_total",
			metrics.Labels{{Name: "decision", Value: "pend"}}).Inc()
		klog.V(3).Infof("FluidServe pends request %s (tier %dms, waited %dms): best "+
			"instance %s would run at %.1fms per token against a gate of %.1fms",
			req.id, req.tier, req.nowMs-req.arrivedMs, best.flux.id,
			best.meanAfter, best.gateAfter)
		return nil
	}

	// Out of time to wait. Either the request can still meet its own budget on
	// the best instance available, in which case place it there even though
	// doing so pushes that instance past the pace it was holding; or it cannot,
	// in which case it is going to be a violation whatever happens next and the
	// only remaining question is whether it takes other requests down with it.
	// The test is against the placement that would actually be made, which is
	// the least damaging one, not against the best outcome available anywhere.
	// Another instance can sometimes still serve this request, and the reason
	// not to use it is the reason it was not chosen: doing so would push
	// requests that can still meet their budgets past them. Trading several
	// incumbents that are on track for one arrival is a losing trade under a
	// per-request rule, so if the placement we are willing to make would miss,
	// the placement is not worth making at all.
	if p.cfg.enableShed && best.missesOwnBudget {
		p.registry.forget(req.id)
		metrics.Counter("scheduler_fluidserve_decisions_total",
			metrics.Labels{{Name: "decision", Value: "shed"}}).Inc()
		p.noteShed(req.id)
		klog.V(3).Infof("FluidServe sheds request %s (tier %dms, waited %dms): "+
			"placing it on %s would give %.1fms per token and %.0fms to first "+
			"token, and its budget no longer allows either",
			req.id, req.tier, req.nowMs-req.arrivedMs, best.flux.id,
			best.meanAfter, float64(req.nowMs-req.arrivedMs)+best.prefillMs)
		return nil
	}

	p.commit(best, req, "force")
	return best.view
}

// sortCandidates puts the feasible instances first and orders each group by the
// quantity that group is chosen on.
//
// Feasible: most of this class first. An instance that already holds a class is
// the one to keep giving it, because an instance's admissible occupancy is set
// by the tightest pace on it, so mixing classes wastes capacity in both
// directions -- a tight request on a loose instance pulls the whole instance
// down, and a loose request on a tight instance gets less room than its budget
// entitles it to. Filling one instance with one class until it can take no more
// is what produces a separation without any instance being assigned to a class,
// and the feasibility test is what stops the filling.
//
// Infeasible: least damage first. Nothing here can serve the request at its
// promised pace, so the question is no longer where it runs best but which
// instance loses the least by taking it.
func sortCandidates(c []candidate) {
	sort.Slice(c, func(a, b int) bool {
		if c[a].feasible != c[b].feasible {
			return c[a].feasible
		}
		if c[a].feasible {
			if c[a].share != c[b].share {
				return c[a].share > c[b].share
			}
		} else if c[a].harm != c[b].harm {
			return c[a].harm < c[b].harm
		}
		if c[a].room != c[b].room {
			return c[a].room > c[b].room
		}
		// Deterministic order so a run can be reproduced; Go randomises map
		// iteration, which would otherwise make two identical runs differ.
		return c[a].flux.id < c[b].flux.id
	})
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
	//
	// The prompt is charged in full as prefill work, which over-states it: this
	// workload replays byte-identical prompts, so most of a prompt's tokens are
	// already resident and the engine skips them. Discounting it by the measured
	// physical-to-logical ratio was implemented and reverted, because that ratio
	// measures block SHARING AMONG RESIDENT REQUESTS, which is a different
	// quantity from the cache hit an arriving prompt will get, and using it
	// loosens admission for exactly the class whose arrival damages the others
	// most: a 22k-token agent prompt would be charged as 2k and would then pass
	// the test that is supposed to keep it away from requests on a tight budget.
	// A discount needs a direct signal -- the engine reporting the hit length for
	// a scheduled prefill -- not a proxy that happens to have a similar value.
	// Until then the charge stays conservative in the direction that protects
	// the requests already running.
	newPending := f.pendingPrefill + float64(req.promptTokens)
	newN := f.nDecode + 1
	newKv := f.proj + cost
	c.meanBefore = f.meanStep
	c.meanAfter = p.capacity.meanStepMs(newKv, newN, newPending, f.chunk, p.cfg.horizonSteps)
	c.headroomAfter = math.Min(f.capKv, f.capMem) - newKv
	c.prefillMs = p.prefillEstimateMs(req, f)

	// Two separate conditions, because they protect two different things.
	//
	// The gate is the tightest pace PROMISED to anything that would then be on
	// the instance, including the arriving request itself. It is built from
	// nominal budgets, which are properties of the classes rather than of how
	// this instance has fared, so it does not move as the instance ages.
	//
	// The second condition protects the requests that are actually at risk
	// right now: no request that can still meet its budget may be pushed past
	// it by this placement. That one has to use the REMAINING budget, because
	// what is at stake is what each incumbent has left, not what it was
	// promised. It is the condition that keeps a class out of an instance
	// serving another class -- a 22k-token prompt raises the mean iteration by
	// about 15 ms, which is inside the 45 ms an interactive class is promised
	// but outside the slack its requests still have once they are running.
	// Requests already past saving are excluded from it, so a broken instance
	// does not become permanently unusable.
	c.gateAfter = math.Min(f.gateAllowance, req.nominalMs) * fsAllowanceUtilisation
	c.feasible = !math.IsInf(c.meanAfter, 0) &&
		c.meanAfter <= c.gateAfter &&
		c.meanAfter <= f.tightestAllowance &&
		newKv <= f.capMem

	c.missesOwnBudget = p.missesOwnBudget(req, c)

	if p.cfg.enableAffinity {
		c.share = classShare(f, req.tier)
	}
	c.harm = p.harmToIncumbents(f, c.meanBefore, c.meanAfter)

	// Free space is expressed as a fraction of the instance's physical capacity
	// so that the tie-break means the same thing regardless of instance size.
	// The memory capacity is the scale rather than the binding limit because the
	// binding limit goes negative once the pace cannot be met at any occupancy,
	// and dividing by a quantity that changes sign makes the comparison
	// meaningless exactly where it matters most.
	c.room = c.headroomAfter / math.Max(f.capMem, 1)
	return c
}

// missesOwnBudget asks whether placing this request on this candidate, right
// now, would still break the rule the request is judged by. It is the test that
// separates a placement worth making from one that only spends capacity.
//
// The two budget forms are asked different questions because they are scored
// differently. A request judged on time to first token and then on its mean time
// between tokens has two independent ways to fail, and the wait so far counts
// only against the first. A request judged end to end has one account, and the
// wait, the prefill and the whole decode all come out of it.
func (p *fluidserveDispatchPolicy) missesOwnBudget(
	req *fluidserveRequest, c candidate) bool {

	waited := float64(req.nowMs - req.arrivedMs)
	if math.IsInf(c.meanAfter, 0) || math.IsInf(c.prefillMs, 0) {
		return true
	}
	if req.isE2E {
		return waited+c.prefillMs+req.expectedToks*c.meanAfter > req.budgetMs
	}
	if req.ttftSloMs > 0 && waited+c.prefillMs > req.ttftSloMs {
		return true
	}
	return req.nominalMs > 0 && c.meanAfter > req.nominalMs
}

// harmToIncumbents prices what placing this request does to the requests
// already on the instance.
//
// It is a statement about the WORK a request brings, not about the budget it
// declares. A long prompt makes several of the next iterations carry a prefill
// chunk, and every request decoding on that instance waits through them, so
// admitting one alongside requests that are close to their budget is what breaks
// them. Charging only for binding an instance to a tighter budget, as an earlier
// version did, never fired in the case that matters: an agent request with a
// 22k-token prompt has a LOOSER per-token budget than an interactive one, so it
// was charged nothing for landing on an instance full of interactive requests
// and pushing all of them past their limit.
//
// Requests whose budget is already spent contribute nothing. They are going to
// miss whatever happens next, so further delay is not an additional loss, and
// counting it would make the instance that has already absorbed the heavy work
// look expensive -- which is exactly the instance that should keep absorbing it.
// That asymmetry is what makes overload collect on the instances it has already
// broken and leave the others clean, with none of them reserved in advance.
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

// classShare is the fraction of the requests on an instance that belong to the
// same tier as the one being placed.
//
// Every other quantity here is symmetric in the instances, which means a fleet
// where all instances hold the same mixture is a fixed point: each instance
// looks identical to every request, so nothing pushes any class towards any
// instance, and the mixture stays uniform. Seven configurations were measured
// and all of them sat at that fixed point, with the interactive class spread
// almost perfectly evenly (concentration 0.05 to 0.11 against PolyServe's 1.00).
//
// This quantity is deliberately not symmetric, and that is what makes the
// uniform mixture unstable: an instance already holding a class becomes the one
// that class goes to, and a separation grows from whatever imbalance the
// arrivals happen to produce. It is the same positive feedback an explicit
// assignment provides, without the assignment -- which instance a class collects
// on is decided by traffic rather than by configuration, and it dissolves on its
// own when that class stops arriving. Applying it only within the feasible set
// is what keeps the feedback bounded.
func classShare(f *instanceFlux, tier int) float64 {
	if len(f.live) == 0 {
		return 0
	}
	same := 0
	for _, r := range f.live {
		if r.tier == tier {
			same++
		}
	}
	return float64(same) / float64(len(f.live))
}

// canWait decides whether the request still has room to be held at the gateway.
//
// The limit is the last instant at which a placement could still succeed. It is
// not the whole budget: the request must still be prefilled once placed, and
// prefill of a long prompt takes several iterations of several hundred
// milliseconds each, so waiting past the point where prefill no longer fits
// converts a request that could have been served late into one certain to miss.
//
// The pace assumed after the wait is the cost of an iteration on an EMPTY
// instance, which is the fastest the engine can physically go. That is the right
// assumption for a wait decision specifically: the question being asked is
// whether success is still possible at all, not whether it is likely. Assuming
// the current pace instead would refuse to wait in exactly the situation waiting
// is for, which is a fleet that is momentarily full.
//
// An earlier version also capped the wait at a quarter of the time-to-first-token
// budget, on the grounds that a request held longer was a miss whatever happened
// next. That was wrong: the rule is time to first token AND mean time between
// tokens, so a request held 4.5 s of a 5 s budget and then served immediately
// meets both. The cap is gone and with it one hand-set fraction.
func (p *fluidserveDispatchPolicy) canWait(best candidate, req *fluidserveRequest) bool {
	waited := float64(req.nowMs - req.arrivedMs)
	prefillMs := best.prefillMs
	if math.IsInf(prefillMs, 0) {
		return false
	}
	var deadline float64
	if req.isE2E {
		deadline = req.budgetMs - prefillMs -
			req.expectedToks*p.capacity.floorStepMs() - p.cfg.ttftSafetyMs
	} else {
		if req.ttftSloMs <= 0 {
			return false
		}
		deadline = req.ttftSloMs - prefillMs - p.cfg.ttftSafetyMs
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
	metrics.Histogram("scheduler_fluidserve_class_share",
		metrics.Labels{}).Observe(c.share)

	klog.V(3).Infof("FluidServe %s request %s (tier %dms, prompt %d) -> %s: "+
		"headroom %.0f->%.0f cap(kv %.0f mem %.0f) proj %.0f meanStep %.1f->%.1fms "+
		"gate %.1fms harm %.2f share %.2f live %d (%d unachievable)",
		kind, req.id, req.tier, req.promptTokens, c.flux.id,
		c.flux.headroom, c.headroomAfter, c.flux.capKv, c.flux.capMem, c.flux.proj,
		c.flux.meanStep, c.meanAfter, c.gateAfter, c.harm, c.share,
		len(c.flux.live), c.flux.unachievable)
}

func finiteOrMinusOne(v float64) float64 {
	if math.IsInf(v, 0) || math.IsNaN(v) {
		return -1
	}
	return v
}

// C6, the slow safety loop, was implemented and removed. It adjusted the
// safety factor z from the rate at which measured iterations ran slower than
// predicted. In its first form it compared the measurement against the BUDGET,
// which under sustained overload is exceeded on every instance no matter what is
// routed where, so it fired continuously and pinned z at its ceiling; that
// throttled the fleet without making any request meet its SLO. Rebuilt against
// the model residual instead, it never moved in any measured run -- the offline
// law reads within a few percent of the engine -- and it cost seven constants
// (trigger rate, residual threshold, backoff factor, step, two bounds, quiet
// window count) to produce a quantity that stayed at its initial value. z is now
// what it was configured as. The loop is worth rebuilding only if a run is
// observed where the residual is both large and persistent.

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
		horizonSteps:   p.FluidserveHorizonSteps,
		zSafety:        p.FluidserveZSafety,
		ttftSafetyMs:   float64(p.FluidserveTtftSafetyMs),
		enablePend:     p.FluidserveEnablePend,
		enableShed:     p.FluidserveEnableShed,
		enableAffinity: p.FluidserveEnableAffinity,
		enableFlux:     p.FluidserveEnableFlux,
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
		shedIDs:  map[string]int64{},
	}
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
		"ttft margin %dms, pend=%v, shed=%v, affinity=%v, flux=%v, budgets %q",
		cfg.horizonSteps, cfg.zSafety, p.FluidserveTtftSafetyMs, cfg.enablePend,
		cfg.enableShed, cfg.enableAffinity, cfg.enableFlux, p.FluidserveClassBudgets)

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
