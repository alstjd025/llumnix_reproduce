package policy

import (
	"fmt"
	"math"
	"sort"
	"sync"
	"time"

	"k8s.io/klog/v2"

	"llumnix/cmd/scheduler/app/options"
	"llumnix/pkg/cms"
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
	classHarm      bool
	forceMargin    bool
	ownBudgetGate  bool
	// kvSlopeProjection replaces the modelled inflow/outflow balance with the
	// instance's own observed rate of change of KV occupancy. Candidate H2.
	kvSlopeProjection bool
	// gateSlack is how far past the tightest promise on an instance that
	// instance may be driven in order to serve a class promised more. 1 is the
	// shipped behaviour; large enough is candidate C. See evaluate().
	gateSlack float64
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
	// recheckMs is how long it has been since this request was last considered,
	// which is the cadence the gateway re-asks on and therefore the granularity
	// at which the request can act on its own deadline. Zero on first sighting.
	recheckMs    float64
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
	// arrivingPrefill is the prefill work expected to REACH this instance over
	// the horizon, from the rate it is being sent prompts and the measured share
	// of a prompt the engine actually computes. Adding it is what makes the
	// prediction describe the interval the engine will run rather than the
	// instant the status was sampled.
	arrivingPrefill float64
	// effectivePrefill is the sum of the two, which is what the capacity model
	// is queried with everywhere.
	effectivePrefill float64
	chunk            float64
	stepID           int64

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

	// fluxMu guards the per-instance view cache. See flux().
	fluxMu    sync.Mutex
	fluxCache map[string]cachedFlux

	// probeMu guards the placement probe below, which exists to answer one
	// question and is instrumentation only -- it changes no decision.
	//
	// §40.3 measured an engine taking 3.2 times its own headroom of new work in
	// one minute, and §41 measured that 50.4% of 500 ms status intervals carry
	// more than one placement to the same engine. Whether the second placement
	// in an interval SEES the first is the difference between a defect and a
	// design working as intended, and §42 could not settle it from a 1 Hz gauge.
	//
	// By design it should: onDispatch increments dispatchVersion and flux()
	// rebuilds the view when that changes. The probe records what actually
	// happened -- how many placements go out per status step, and whether the
	// headroom the decision read moved between them.
	probeMu   sync.Mutex
	lastProbe map[string]placementProbe
}

// cachedFlux is one instance's state as of a particular engine status and a
// particular number of placements onto it.
type cachedFlux struct {
	stepID    int64
	version   uint64
	instances int
	flux      *instanceFlux
}

// flux returns the instance's state, rebuilding it only when something it
// depends on has changed.
//
// The state depends on two things: what the engine last reported, which is
// refreshed once per status pull, and what this scheduler has placed since,
// which changes on every dispatch. Between those events, rebuilding it produces
// the same answer at a cost that is paid on every scheduling call -- and a held
// request re-enters this path at every retry, so the call rate is a multiple of
// the arrival rate rather than equal to it. Measured, one call cost 2.05 ms
// against 0.109 ms for a filter-and-pick policy, which at a few hundred calls a
// second is most of a core spent recomputing an unchanged answer.
//
// The staleness this admits is bounded by the same two events. It is not bounded
// by time, deliberately: a view is reused only while neither the engine nor this
// scheduler has done anything that would change it.
func (p *fluidserveDispatchPolicy) flux(
	view *instanceViewScheduling, nowMs int64, instances int) *instanceFlux {
	if view.cmsView == nil || view.cmsView.Status == nil {
		return nil
	}
	id := view.GetInstanceId()
	stepID := int64(view.cmsView.Status.StepId)
	version := p.registry.version(id)

	p.fluxMu.Lock()
	if c, ok := p.fluxCache[id]; ok && c.stepID == stepID && c.version == version &&
		c.instances == instances {
		p.fluxMu.Unlock()
		return c.flux
	}
	p.fluxMu.Unlock()

	f := p.buildFlux(view, nowMs, instances)
	if f == nil {
		return nil
	}
	p.fluxMu.Lock()
	if p.fluxCache == nil {
		p.fluxCache = map[string]cachedFlux{}
	}
	p.fluxCache[id] = cachedFlux{stepID: stepID, version: version,
		instances: instances, flux: f}
	p.fluxMu.Unlock()
	return f
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
	arrived, recheckMs := p.registry.noteArrival(
		request.Id, request.PromptNumTokens, now)

	tier := request.TpotSloMs
	nominal, expected, isE2E, budgetMs := p.registry.requestBudget(tier)

	ctx := &fluidserveRequest{
		id:           request.Id,
		tier:         tier,
		ttftSloMs:    float64(request.TtftSloMs),
		promptTokens: request.PromptNumTokens,
		arrivedMs:    arrived,
		recheckMs:    recheckMs,
		nowMs:        now,
		expectedToks: expected,
		nominalMs:    nominal,
		isE2E:        isE2E,
		budgetMs:     budgetMs,
	}

	for _, view := range instanceViews {
		observed := p.observeInstance(view)
		view.schedulingCtx.fluidserveRequest = ctx
		f := p.flux(view, now, len(instanceViews))
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
				metrics.Gauge("scheduler_fluidserve_arriving_prefill_tokens", lbl).
					Set(f.arrivingPrefill)
				metrics.Gauge("scheduler_fluidserve_queued_prefill_tokens", lbl).
					Set(f.pendingPrefill)
				metrics.Gauge("scheduler_fluidserve_prefill_duty", lbl).
					Set(p.prefillDutyOf(f.id))
				// The remaining two terms of the prediction.
				//
				// decode_law_ms is the decode law at the batch the PREDICTION is
				// evaluated at, which is the current status rather than the one
				// that was running over the measured interval. Published beside
				// decode_only_ms so that the difference between the two -- how
				// far the batch moved between the two statuses -- is a readable
				// quantity rather than an assumption.
				//
				// pace_ms is the iteration time the planning horizon is
				// converted with. It enters the prefill term twice over: the
				// horizon in milliseconds is horizonSteps x pace, and the
				// arriving prefill is duty x that. Algebraically the prefill term
				// collapses to duty x pace, so an error in pace appears in the
				// prediction multiplied by the duty cycle and is invisible in
				// every other series.
				metrics.Gauge("scheduler_fluidserve_decode_law_ms", lbl).
					Set(p.capacity.decodeStepMs(f.kvLogical, f.nDecode))
				metrics.Gauge("scheduler_fluidserve_pace_ms", lbl).
					Set(p.paceMs(f.id, f.kvLogical, f.nDecode))
				// Telemetry only. This series is how a run is checked for how far
				// past the fleet's capacity it was driven; no decision reads it,
				// which is the whole point of the change that removed it from the
				// projection.
				metrics.Gauge("scheduler_fluidserve_offered_rate_tokens_per_ms",
					metrics.Labels{}).Set(p.registry.offeredRate())
				// What canWait now reserves for everything that happens after a
				// placement, and the mean it is bounded from. Published as a
				// pair because the gap between them IS the correction: the
				// modelled version this replaced had no spread at all, so a run
				// where the two are close is a run where this change could not
				// have mattered.
				if b, n := p.registry.PlacementDelayBound(p.cfg.zSafety); n >= fsMinPlacementDelaySamples {
					metrics.Gauge("scheduler_fluidserve_placement_delay_bound_ms",
						metrics.Labels{}).Set(b)
					metrics.Gauge("scheduler_fluidserve_placement_delay_mean_ms",
						metrics.Labels{}).Set(p.registry.PlacementDelayMean())
				}
				// The two fleet-wide scalars -- the multiplicative correction and
				// the prefill fraction -- are already published by reportLoop as
				// scheduler_fluidserve_capacity_correction and
				// scheduler_fluidserve_prefill_fraction. Publishing them again
				// here under new names was a duplicate and is not done.
			}
		}
		view.schedulingCtx.fluidserveFlux = f
	}
	p.baseDispatchPolicy.calculateMetrics(inferType, request, instanceViews)
}

// decodeBatchOf is the decode batch as the latency model counts it: the KV in
// logical tokens and the number of requests producing them.
//
// It exists as one function because it is needed in two places -- to PREDICT an
// iteration and to MEASURE one -- and the two must count the same thing. They did
// not. The measurement used SchedulerRunningToDecodeTokensNum and
// SchedulerRunningToDecodeRequestsNum alone while the prediction summed five
// fields, so the decode-only cost subtracted during the measurement was computed
// for a smaller batch than the engine was actually running, and the difference
// was attributed to prefill.
//
// Measured at 80 req/s that put the prefill duty cycle at 0.20 on instances the
// engine reported as carrying no prefill at all (all_prefills_tokens_num = 0,
// waiting_requests = 0). The projection added 8.7 ms to a predicted iteration of
// 46.3 against an observed 37.6, and the gate is 45.0, so no instance was ever
// feasible: 84% of decisions became holds, and deep research spent 8.5 s of its
// 10 s budget waiting for a fleet that was running at 21% KV.
func decodeBatchOf(st *cms.InstanceStatus, view *cms.InstanceView) (kv, n float64) {
	kv = float64(st.HybridSchedulerWaitingToDecodeTokensNum +
		st.SchedulerWaitingToDecodeTokensNum +
		st.SchedulerRunningToDecodeTokensNum +
		st.NumTokensLoadingRequests)
	n = float64(st.HybridSchedulerWaitingToDecodeRequestsNum +
		st.NumLoadingRequests +
		st.SchedulerWaitingToDecodeRequestsNum +
		st.SchedulerRunningToDecodeRequestsNum)
	if view != nil {
		kv += float64(view.NumTokensInflightDispatchDecodeRequests)
		n += float64(view.NumInflightDispatchDecodeRequests)
	}
	return kv, n
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
	// meanMs is the mean iteration time measured over the interval that ENDED
	// at this observation, or 0 before one has been measured. It is kept so the
	// planning horizon, which is counted in iterations, can be converted into
	// the milliseconds over which arrivals accumulate.
	meanMs float64
	// prefillDuty is the share of this instance's engine time spent on prefill,
	// smoothed over recent intervals. It is the RATIO of the two smoothed times
	// below rather than a smoothed ratio; see observeInstance for why.
	prefillDuty float64
	// prefillMsEwma and totalMsEwma are the two times the duty cycle is the
	// ratio of, each smoothed separately: milliseconds the engine spent beyond
	// what the decode law accounts for, and milliseconds it spent in total.
	// Kept as times rather than as a ratio because the quantity wanted is a
	// share of engine TIME, and an interval that covers three times as much time
	// is three times as much evidence about that share.
	prefillMsEwma float64
	totalMsEwma   float64
	// elapsedEwma and stepsEwma are what meanMs is the ratio of, for the same
	// reason. One status interval can cover a single 436 ms prefill iteration or
	// twelve 38 ms decode ones; averaging the two intervals' per-iteration times
	// with equal weight answers "the mean over intervals", and the quantity every
	// budget in this policy is expressed in is time per TOKEN, which is the total
	// time divided by the total iterations.
	//
	// Measured over ten million tokens at 80 req/s: the engines' own
	// inter-token latency reads 42.6 ms, the equal-weight average of the same
	// intervals reads 58.7, and the median reads 38.7.
	elapsedEwma float64
	stepsEwma   float64
	// kvSlope is the rate at which this instance's logical KV occupancy has
	// recently been changing, in tokens per millisecond, smoothed over status
	// intervals. Candidate H2 projects with this instead of modelling the growth
	// of the resident set and the release from completions separately.
	//
	// It is not the same quantity as inflow minus outflow, and the difference is
	// the point. Those two terms describe what the requests already resident will
	// do; the measured slope also contains the requests the scheduler places
	// during the interval, which is the term the modelled balance omits. Measured
	// over 14,676 paired samples of the `full` hour, the modelled balance
	// under-predicted the occupancy one horizon later 88.5% of the time by a mean
	// of 101,633 tokens, while the slope projection was unbiased (+1,393) with a
	// mean absolute error of 29,106 against the modelled balance's 106,416.
	kvSlope float64
}

const (
	// Weight of one status interval in the per-instance prefill duty cycle.
	//
	// A status interval is about 500 ms and carries roughly 25 iterations, of
	// which only a few carry a prefill chunk, so a single interval is a noisy
	// estimate of the share. At 0.1 the effective window is around ten intervals
	// (five seconds, some 250 iterations), which averages that noise out while
	// still following a change in the mix within a few seconds.
	//
	// A fast filter is safe here in a way it was not for the step correction,
	// and the reason is the sign of the loop it sits in. Charging more prefill
	// admits less work to this instance, which makes the engine spend LESS time
	// on prefill, which lowers the estimate: the feedback is negative and
	// settles. The correction's loop and the offered-rate loop it replaces were
	// positive in the rejection rate, which is why they needed a time constant
	// far slower than the loop to avoid running away.
	fsPrefillDutyAlpha = 0.1
)

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

	curKv, curN := decodeBatchOf(st, view.cmsView)
	cur := stepObservation{
		stepID:      int64(st.StepId),
		timestampMs: st.TimestampMs,
		kvLogical:   curKv,
		nDecode:     curN,
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

	// The same interval also measures how much of the prompts sent here the
	// engine actually had to compute. The decode-only prediction is taken at the
	// START of the interval, because that is the state the engine ran under for
	// most of it, and anything the engine spent beyond it is prefill.
	chunk := 8192.0
	if view.cmsView.Metadata != nil && view.cmsView.Metadata.MaxNumBatchedTokens > 0 {
		chunk = float64(view.cmsView.Metadata.MaxNumBatchedTokens)
	}
	decodeOnly := p.capacity.decodeStepMs(prev.kvLogical, prev.nDecode)
	p.capacity.notePrefill(measured, decodeOnly,
		float64(steps), chunk, p.registry.takePromptTokens(id))

	// The three quantities that decompose the predicted iteration, published
	// here because this is the one place that holds both ends of a measured
	// interval. Without them the only readable series are the prediction and the
	// measurement, and a gap between those two can be produced at four different
	// places -- the decode law, the batch the law is evaluated at, the prefill
	// term, or the multiplicative correction -- with no way to tell which from
	// the outside. Three attempts at this gap have been made by reasoning from
	// the two endpoints alone and all three were wrong, so the decomposition is
	// measured instead.
	//
	//	decode_only_ms   the decode law at the batch that was RUNNING, which is
	//	                 what the prefill attribution subtracts from the measured
	//	                 mean. observed - decode_only is prefill time actually
	//	                 spent, by definition of the attribution.
	//	obs_kv_tokens    the KV the law was evaluated at, so that a gap caused by
	//	                 evaluating it at the wrong batch is visible as such
	//	                 rather than as a law error.
	//	obs_decode_batch same, for the request count term.
	obsLbl := metrics.Labels{{Name: "instance", Value: id}}
	metrics.Gauge("scheduler_fluidserve_decode_only_ms", obsLbl).Set(decodeOnly)
	metrics.Gauge("scheduler_fluidserve_obs_kv_tokens", obsLbl).Set(prev.kvLogical)
	metrics.Gauge("scheduler_fluidserve_obs_decode_batch", obsLbl).Set(prev.nDecode)

	// The same difference measures how much of THIS instance's engine time went
	// to prefill rather than decode. Everything the engine did beyond what the
	// decode law accounts for is prefill work, by the same attribution
	// notePrefill uses; the difference is that this is kept per instance and as
	// a rate, which is what the projection below needs.
	// The duty cycle is a ratio of two accumulated TIMES, smoothed separately
	// and divided at the end, rather than a smoothed average of per-interval
	// ratios clamped at zero.
	//
	// The previous form computed max(0, (measured - decodeOnly) / measured) for
	// each interval and smoothed that. Two things were wrong with it and they
	// pull in opposite directions, which is why the error was not visible as a
	// consistent bias:
	//
	//   the clamp   Half the intervals carry no prefill at all, and on those the
	//               measurement scatters either side of the decode law. Clamping
	//               each sample at zero keeps the intervals that ran slower than
	//               the law and discards the ones that ran faster, so the average
	//               of the clamped samples is the average of the POSITIVE PART,
	//               not the average. Measured at 80 req/s the instantaneous duty
	//               had mean 0.099 and median -0.007, and the smoothed value sat
	//               at 0.163 -- 65% above the quantity it claims to estimate,
	//               entirely from rectified noise.
	//   equal weight  A prefill-carrying interval covers several hundred
	//               milliseconds and a decode-only one about forty. Averaging the
	//               two intervals' ratios with equal weight answers "what share
	//               of a typical interval is prefill", which is not what the
	//               projection needs; carriedPrefillTokens multiplies this by a
	//               horizon in milliseconds, so it needs the share of engine TIME.
	//               Time-weighted, the same run reads 0.32.
	//
	// The two errors are not small and do not cancel: rectification raised the
	// estimate by half, equal weighting halved it. What came out sat between the
	// median observed iteration (38.7 ms) and the mean (57.5 ms), which is why
	// comparing the prediction against one statistic said it was 8 ms high and
	// against the other said it was 6 ms low.
	//
	// Rectifying the RATIO rather than each sample keeps the guarantee that
	// matters -- no negative prefill time is ever projected -- without discarding
	// the intervals that carry the evidence the law is not biased.
	prefillMs := (measured - decodeOnly) * float64(steps)
	totalMs := measured * float64(steps)

	p.obsMu.Lock()
	weighted := measured
	if o, ok := p.lastObs[id]; ok && o.stepID == cur.stepID {
		o.prefillMsEwma = prev.prefillMsEwma +
			fsPrefillDutyAlpha*(prefillMs-prev.prefillMsEwma)
		o.totalMsEwma = prev.totalMsEwma +
			fsPrefillDutyAlpha*(totalMs-prev.totalMsEwma)
		duty := 0.0
		if o.totalMsEwma > 0 {
			duty = o.prefillMsEwma / o.totalMsEwma
		}
		if duty < 0 {
			duty = 0
		}
		if duty > 1 {
			duty = 1
		}
		o.prefillDuty = duty

		// Time per token, as a ratio of two separately smoothed accumulations
		// rather than an average of per-interval ratios. Same correction as the
		// duty cycle above and for the same reason: an interval carrying one
		// 436 ms prefill iteration and an interval carrying twelve 38 ms decode
		// ones are not equal evidence about the time a token waits.
		//
		// This is what the whole policy is anchored to. meanMs sets the pace the
		// planning horizon is converted with, and the value returned from here is
		// what noteResidual trains the multiplicative correction against, so the
		// prediction converges to whatever this says. Anchoring it to the mean
		// over intervals put that target 16 ms above the latency the engines
		// report, and no change to any other term could move it: measured at
		// 80 req/s, halving the duty cycle simply drove the correction up by the
		// same amount and left the prediction where it was.
		// The rate this instance's occupancy is moving at, for candidate H2.
		// Smoothed with the same constant and for the same reason as the duty
		// cycle: one status interval is about 500 ms and a single interval is a
		// noisy estimate, while the feedback through it is negative -- a rising
		// slope raises the projection, which lowers the headroom, which places
		// less here, which lowers the slope -- so a fast filter settles rather
		// than running away.
		o.kvSlope = prev.kvSlope +
			fsPrefillDutyAlpha*((cur.kvLogical-prev.kvLogical)/elapsed-prev.kvSlope)

		o.elapsedEwma = prev.elapsedEwma + fsPrefillDutyAlpha*(elapsed-prev.elapsedEwma)
		o.stepsEwma = prev.stepsEwma +
			fsPrefillDutyAlpha*(float64(steps)-prev.stepsEwma)
		if o.stepsEwma > 0 {
			o.meanMs = o.elapsedEwma / o.stepsEwma
		} else {
			o.meanMs = measured
		}
		weighted = o.meanMs
		p.lastObs[id] = o
	}
	p.obsMu.Unlock()

	obsLbl2 := metrics.Labels{{Name: "instance", Value: id}}
	metrics.Gauge("scheduler_fluidserve_obs_steps", obsLbl2).Set(float64(steps))
	metrics.Gauge("scheduler_fluidserve_raw_step_ms", obsLbl2).Set(measured)

	return weighted
}

// prefillDutyOf is the share of engine time instance `id` has recently spent on
// prefill. Zero before anything has been measured, which is the permissive
// direction: a fresh process starts by admitting more rather than less, and the
// first measurement arrives within one status interval.
func (p *fluidserveDispatchPolicy) prefillDutyOf(id string) float64 {
	p.obsMu.Lock()
	defer p.obsMu.Unlock()
	if o, ok := p.lastObs[id]; ok {
		return o.prefillDuty
	}
	return 0
}

// kvSlopeOf is the rate instance `id`'s logical KV occupancy is moving at, in
// tokens per millisecond. Zero before anything has been measured, which makes
// the projection fall back to the current occupancy -- the permissive direction,
// and the first measurement arrives within one status interval.
func (p *fluidserveDispatchPolicy) kvSlopeOf(id string) float64 {
	p.obsMu.Lock()
	defer p.obsMu.Unlock()
	if o, ok := p.lastObs[id]; ok {
		return o.kvSlope
	}
	return 0
}

// carriedPrefillTokens converts an observed prefill duty cycle into the units
// the capacity model works in.
//
// The model takes queued prefill in TOKENS and turns it into a count of
// chunk-carrying iterations. Here the measurement is a share of engine TIME, so
// it is converted the other way through the same constant: the time the engine
// will spend on prefill over the horizon, divided by what one chunk-carrying
// iteration costs beyond a decode-only one, is the number of such iterations,
// and multiplying by the chunk size puts it back in tokens. Passing it through
// the same conversion the model uses in reverse is what makes the two agree.
func (p *fluidserveDispatchPolicy) carriedPrefillTokens(
	duty, horizonMs, chunk float64) float64 {

	if duty <= 0 || horizonMs <= 0 || chunk <= 0 {
		return 0
	}
	perChunk := p.capacity.prefillStepMs(chunk) - p.capacity.c0
	if math.IsInf(perChunk, 0) || perChunk <= 0 {
		return 0
	}
	return duty * horizonMs / perChunk * chunk
}

// paceMs is the iteration time to convert the planning horizon into a duration.
// The measured one is preferred; before anything has been measured the decode
// law stands in, which understates the duration and therefore the arrivals, so
// a fresh process starts by admitting more rather than less.
func (p *fluidserveDispatchPolicy) paceMs(id string, kvLogical, nDecode float64) float64 {
	p.obsMu.Lock()
	o, ok := p.lastObs[id]
	p.obsMu.Unlock()
	if ok && o.meanMs > 0 {
		return o.meanMs
	}
	return p.capacity.decodeStepMs(kvLogical, nDecode)
}

func (p *fluidserveDispatchPolicy) buildFlux(
	view *instanceViewScheduling, nowMs int64, instances int) *instanceFlux {

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
	f.kvLogical, f.nDecode = decodeBatchOf(st, view.cmsView)
	f.pendingPrefill = math.Max(0, float64(
		st.NumUncomputedTokensAllWaitingPrefills+
			st.NumUncomputedTokensSchedulerRunningPrefills+
			view.cmsView.NumUncomputedTokensInflightDispatchPrefillRequests))

	f.live = p.registry.reconcile(f.id, int(st.SchedulerRunningToDecodeRequestsNum),
		f.stepID, nowMs)

	// Prefill the instance will carry over the horizon, not just what is queued
	// at this instant.
	//
	// The anticipation is necessary. The engine drains its prefill queue well
	// inside one status pull, so the queued figure read at a sampling instant is
	// usually near zero, while over the horizon the engine spends a real share of
	// every iteration on prompts that arrived in between. Leaving it out does not
	// merely make the prediction low: the measured-against-predicted correction
	// then absorbs the whole difference as a multiplier on the mean, which scales
	// the KV and batch-size terms with it and shrinks the admissible occupancy far
	// more than the missing prefill would. Measured at 600 rpm, that correction
	// climbed to 1.48 on a fleet whose interactive classes were at 100%.
	//
	// What it is built from has changed twice, and the reason is a property the
	// two earlier forms lacked rather than a difference in accuracy.
	//
	// It was this instance's own recent dispatch rate. That reads the scheduler's
	// own output back as an input: a higher estimate tightens the instance's gate,
	// which sends it less, which lowers the estimate. Two runs of the same binary
	// at the same offered rate settled in different places, 8 points apart.
	//
	// It was then the fleet's OFFERED token rate divided by the instance count.
	// That broke the loop through the placement decision but opened one through
	// the admission decision: the offered rate counts requests this policy is
	// about to reject, so with rejection rate s the projection is the served rate
	// over 1-s, and a higher s tightens the gate, which raises s. Measured at
	// 3000 rpm it settled with 54.6% rejected while the engines ran at 32.9 ms
	// against a gate demanding 61.7 ms. Being a fleet average, it was also
	// identical on every instance, so it could not order candidates at all: its
	// entire effect was to raise the level on every instance at once, which
	// closed the fleet to every class simultaneously. That is why 44% of chat --
	// 674-token prompts, which PolyServe served in full -- was rejected.
	//
	// It is now the share of engine time THIS instance has been observed to spend
	// on prefill, projected over the horizon. That quantity is measured at the
	// engine, so no belief of the scheduler's feeds it; it differs between
	// instances, so it orders candidates as well as gating them; and the loop it
	// does sit in has the opposite sign: charging more admits less to this
	// instance, which makes it spend less time on prefill, which lowers the
	// charge.
	//
	// Queue and duty cycle describe overlapping work -- what is queued now is
	// part of what the engine will be observed to prefill next -- so they are
	// combined by taking the larger rather than by adding, which would charge the
	// same prompts twice. Each covers a case the other misses: a burst that has
	// just landed shows in the queue before it shows in the duty cycle, and a
	// steady stream that the engine absorbs between status pulls shows in the
	// duty cycle while the queue reads zero.
	//
	// The horizon is counted in iterations, so it becomes a duration at the pace
	// the instance is delivering. The result is not clamped here because the
	// capacity model already caps the prefill-carrying share of the horizon at
	// one: an engine cannot absorb more than one chunk per iteration however
	// much arrives.
	horizonMs := float64(p.cfg.horizonSteps) * p.paceMs(f.id, f.kvLogical, f.nDecode)
	f.arrivingPrefill = p.carriedPrefillTokens(p.prefillDutyOf(f.id), horizonMs, f.chunk)
	f.effectivePrefill = math.Max(f.pendingPrefill, f.arrivingPrefill)

	// The pace the instance is delivering right now. It is needed before the
	// loop because a request the instance is ALREADY failing cannot be made to
	// fail by admitting another one, and should therefore not be able to close
	// the instance to everything else.
	f.meanStep = p.capacity.meanStepMs(f.kvLogical, f.nDecode, f.effectivePrefill,
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
	if p.cfg.kvSlopeProjection {
		// Candidate H2. Project with the rate the engine's occupancy is actually
		// moving at instead of with the two modelled terms.
		//
		// The modelled balance has three terms where the quantity it estimates
		// has four. Occupancy one horizon ahead is the current occupancy, plus
		// the growth of the requests already resident, minus what completions
		// release, PLUS the footprint of the requests the scheduler places
		// during that horizon. The fourth is deliberately absent, because the
		// projection is meant to describe the instance before this one request is
		// added -- but at saturation about 48 placements go to an engine inside
		// one horizon (EXP-47 measured 5.24 per 500 ms status interval), which at
		// a mix-weighted footprint of some 1,732 tokens is about 83,000 tokens
		// the projection does not contain.
		//
		// The observed slope contains all four terms by construction, because it
		// is the difference of two occupancies the engine reported. What it gives
		// up is the ability to say WHY the occupancy is moving, which nothing in
		// the decision needs: the tests downstream compare a projected occupancy
		// against a capacity.
		//
		// The three properties that made the offered-rate projection wrong in v18
		// (section 15.3-15.4) are all absent here. The value differs per instance,
		// so it contributes to ordering candidates rather than shifting every
		// gate by the same amount. It is measured from the engine, so the loop is
		// negative and settles. And it counts requests that were actually placed
		// rather than requests that arrived, so a rising rejection rate cannot
		// feed back into a tighter gate.
		//
		// inflow and outflow are still published, as the positive and negative
		// part of the same movement, so the existing series keep a meaning.
		delta := p.kvSlopeOf(f.id) * horizonMs
		if delta >= 0 {
			f.inflow, f.outflow = delta, 0
		} else {
			f.inflow, f.outflow = 0, -delta
		}
	}
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
		f.effectivePrefill, f.chunk, p.cfg.horizonSteps)

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

// The per-decision log lines below are V(5), not V(3), and that is a
// performance decision rather than a taste one. The scheduler is deployed at
// -v 4, one line is emitted per scheduling call, and a held request re-enters
// the path at every retry, so at 3000 rpm the rate is around 400 lines a second
// through a mutex-protected writer. Measured, the whole call cost 4.0 ms
// against 0.109 ms for a policy that does not hold, and the scheduling path is
// serialised under the cluster-view lock, so that is 1.6 s of work offered per
// second and the requests queue in front of it. What the run is analysed from
// is the metric series, which the CLAUDE.md note already says; the lines are
// for reading one decision by hand at -v 5.
//
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

	// snapFeasible is what the SAME test would have returned had the instance
	// been judged on the KV it holds right now instead of on the projection.
	// Nothing reads it; it exists to be counted.
	//
	// The projection enters the decision at exactly one place, newKv = proj +
	// cost, and everything downstream follows from that. Measured at 80 req/s
	// the projection sits 48,501 tokens below the snapshot, which through the
	// decode law's KV coefficient is 0.68 ms on a predicted iteration of about
	// 46 against a gate of 45. So it can only change a decision when the
	// prediction lands within 0.68 ms of the gate, and how often that happens is
	// a fact about the workload that no amount of reasoning settles.
	//
	// Counted here rather than measured by running the ablation as a separate
	// arm, because that arm's effect reaches the score only after passing
	// through the feedback the change itself causes: fewer placements, a lighter
	// engine, a lower measured iteration time, and a correction factor that
	// follows it down. v25 was lost to exactly that loop. Computing both answers
	// from the same state at the same instant measures the projection where it
	// enters, before any of that.
	//
	// What this does NOT measure: the trajectory a snapshot-only policy would
	// have followed from the start. It is the marginal effect, not the system
	// effect, and it is meant to decide whether the system-effect experiment is
	// worth its two hours.
	snapFeasible bool
	snapRoom     float64
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
	// Would the projection have changed what happens to THIS request, as opposed
	// to what one instance looked like? Two different answers count separately:
	// whether the request gets placed at all, and if it does, whether it goes
	// somewhere else.
	anyFeasible, anySnap := false, false
	for i := range cands {
		anyFeasible = anyFeasible || cands[i].feasible
		anySnap = anySnap || cands[i].snapFeasible
	}
	metrics.Counter("scheduler_fluidserve_flux_evaluations_total",
		metrics.Labels{{Name: "level", Value: "decision"}}).Inc()
	if anyFeasible != anySnap {
		metrics.Counter("scheduler_fluidserve_flux_flips_total",
			metrics.Labels{{Name: "level", Value: "decision"}}).Inc()
	} else if anyFeasible {
		snap := make([]candidate, len(cands))
		copy(snap, cands)
		for i := range snap {
			snap[i].feasible = snap[i].snapFeasible
			snap[i].room = snap[i].snapRoom
		}
		sortCandidates(snap)
		sortCandidates(cands)
		if snap[0].flux.id != cands[0].flux.id {
			metrics.Counter("scheduler_fluidserve_flux_flips_total",
				metrics.Labels{{Name: "level", Value: "target"}}).Inc()
		}
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
		klog.V(5).Infof("FluidServe pends request %s (tier %dms, waited %dms): best "+
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
		klog.V(5).Infof("FluidServe sheds request %s (tier %dms, waited %dms): "+
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
	// The prompt is charged only for the part the engine will actually compute,
	// measured live (see capacityModel.notePrefill). Charging it in full is
	// wrong by an order of magnitude wherever prompts share prefixes, and wrong
	// in the direction that hurts: it made the heaviest class infeasible almost
	// everywhere and produced a 28.8% rejection rate at an offered rate the
	// fleet was carrying at 13.5 ms per iteration against budgets of 50 and 100.
	//
	// An earlier version discounted it by the physical-to-logical KV ratio and
	// was reverted: that ratio measures block sharing among resident requests,
	// which is a different quantity that merely happens to have a similar value.
	// The measurement used now is of the prefill work the engine performed.
	//
	// The KV footprint below is NOT discounted, because the latency model counts
	// logical tokens and a shared block is charged to every request holding it.
	newPending := f.effectivePrefill +
		float64(req.promptTokens)*p.capacity.prefillFractionOf()
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
	// Candidate C. The instance minimum in this expression is redundant
	// protection and it is the binding one at saturation.
	//
	// Two things are being protected. The arriving request must be able to run
	// at the pace ITS class was promised, which is req.nominalMs. The requests
	// already here must not be pushed past what they can still meet, which is
	// f.tightestAllowance on the next line and which uses each incumbent's
	// REMAINING budget. f.gateAllowance protects the incumbents a second time,
	// using their nominal budgets instead, and because it is a minimum over
	// every class present it becomes chat's 50 ms within seconds of a run
	// starting -- chat is 76.9% of arrivals.
	//
	// Measured over minutes 50-56 of EXP-41's `full` trace: gateAllowance read
	// exactly 50.0 on all four instances while the delivered pace was 55.6 and
	// tightestAllowance was 68.3-72.7. A deep research request with a 100 ms
	// budget was therefore tested against 45.0, failed on every instance, was
	// held for 8.85 s of its 10 s budget and then shed 15.7% of the time -- while
	// the fleet it could not enter was running at 55.6, inside its budget. The
	// Llumnix SLO arm dispatches the same requests at 0.44 s and scores 100 on
	// that class.
	//
	// With the instance minimum removed the arriving request is judged on its
	// own promise and the incumbents on what they have left. Deep research
	// passes 55.6 <= 90 and 55.6 <= 68.3 and routes; chat still fails 55.6 <=
	// 45.0 and does not, which is correct -- that instance cannot serve chat at
	// 50 ms.
	//
	// The risk is on the second line: tightestAllowance excludes incumbents
	// already past their budgets, so the protection weakens exactly when a class
	// has begun to miss. EXP-46's acceptance conditions require chat not to get
	// worse for that reason.
	// The instance minimum and the request's own budget are not two options but
	// the two ends of one axis, and gateSlack is where on it this fleet sits.
	//
	//	gateSlack = 1      the instance minimum binds as promised. An instance
	//	                   carrying chat is held to chat's 50 ms for every
	//	                   arriving request whatever that request was promised.
	//	gateSlack = 1/0.90 the instance minimum binds at exactly chat's budget
	//	                   rather than at the margin below it, so foreign work may
	//	                   use the whole of what chat was promised and none of the
	//	                   safety margin.
	//	gateSlack = k      an instance may be driven to k times the tightest
	//	                   promise it carries in order to serve a class whose own
	//	                   promise is looser.
	//	gateSlack >= max class-budget ratio, or ownBudgetGate
	//	                   the instance minimum never binds and every request is
	//	                   judged against its own budget alone. For this workload
	//	                   the ratio is 100/50 = 2, so gateSlack >= 2 and
	//	                   ownBudgetGate are the same policy.
	//
	// The axis is worth naming because the two ends fail in opposite directions
	// and both failures are measured. At 1, deep research with a 100 ms budget is
	// refused by a fleet delivering 45 ms, and at 45 req/s that produces a
	// bistability: 5 of 14 runs end with chat on all four engines, every gate at
	// 45.0 ms, and 15% of decisions routing (implementation.md section 52). At
	// the far end, EXP-50 measured the whole hour losing 4.4 points because the
	// capacity freed for the loose-budget classes comes out of chat, which is
	// 76.9% of arrivals. Neither end is the right answer and the choice of
	// default is a statement about which aggregation the fleet is scored on.
	//
	// The incumbents are protected on the next line regardless, by what each of
	// them has LEFT rather than by what its class was promised.
	gate := req.nominalMs
	if !p.cfg.ownBudgetGate {
		if inst := f.gateAllowance * p.cfg.gateSlack; inst < gate {
			gate = inst
		}
	}
	c.gateAfter = gate * fsAllowanceUtilisation
	unpredictable := math.IsInf(c.meanAfter, 0)
	overGate := c.meanAfter > c.gateAfter
	overIncumbents := c.meanAfter > f.tightestAllowance
	overMemory := newKv > f.capMem
	c.feasible = !unpredictable && !overGate && !overIncumbents && !overMemory

	// Which of the four conditions refused this placement. Instrumentation only;
	// no decision reads it.
	//
	// `feasible` is a conjunction and every analysis so far has had to infer from
	// the outside which term was binding -- §37 read it from the share of
	// decisions that routed, §47 from when preemptions began. Those inferences
	// were right about the shape and could not name the term. Each failing
	// condition is counted separately rather than only the first, so that two
	// conditions failing together is visible as such instead of being attributed
	// to whichever the code happens to test first.
	if !c.feasible {
		reason := func(v string) {
			metrics.Counter("scheduler_fluidserve_infeasible_total",
				metrics.Labels{{Name: "reason", Value: v}}).Inc()
		}
		if unpredictable {
			reason("unpredictable")
		}
		if overGate {
			reason("gate")
		}
		if overIncumbents {
			reason("incumbents")
		}
		if overMemory {
			reason("memory")
		}
	}

	c.missesOwnBudget = p.missesOwnBudget(req, c)

	if p.cfg.enableAffinity {
		c.share = classShare(f, req.tier)
	}
	c.harm = p.harmToIncumbents(f, req.tier, c.meanBefore, c.meanAfter)

	// Free space is expressed as a fraction of the instance's physical capacity
	// so that the tie-break means the same thing regardless of instance size.
	// The memory capacity is the scale rather than the binding limit because the
	// binding limit goes negative once the pace cannot be met at any occupancy,
	// and dividing by a quantity that changes sign makes the comparison
	// meaningless exactly where it matters most.
	c.room = c.headroomAfter / math.Max(f.capMem, 1)

	// The same test on the snapshot. Only newKv differs; the gate, the tightest
	// allowance and the memory capacity are properties of the instance and the
	// request, not of the projection.
	snapKv := f.kvLogical + cost
	snapMean := p.capacity.meanStepMs(snapKv, newN, newPending, f.chunk, p.cfg.horizonSteps)
	c.snapFeasible = !math.IsInf(snapMean, 0) &&
		snapMean <= c.gateAfter &&
		snapMean <= f.tightestAllowance &&
		snapKv <= f.capMem
	c.snapRoom = (math.Min(f.capKv, f.capMem) - snapKv) / math.Max(f.capMem, 1)
	if c.feasible != c.snapFeasible {
		metrics.Counter("scheduler_fluidserve_flux_flips_total",
			metrics.Labels{{Name: "level", Value: "candidate"}}).Inc()
	}
	metrics.Counter("scheduler_fluidserve_flux_evaluations_total",
		metrics.Labels{{Name: "level", Value: "candidate"}}).Inc()

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
	// The pace this is compared against is the same quantity `feasible` compares,
	// and until now the two comparisons used different thresholds: routing
	// required the predicted pace to be within fsAllowanceUtilisation of the
	// budget, while this test compared against the budget itself. A request whose
	// predicted pace sits between the two was therefore refused a routed
	// placement and then given a forced one.
	//
	// That gap is where the failures were. Measured at 60 req/s, chat's realised
	// mean inter-token latency has a median of 49.9 ms against a 50 ms budget and
	// 62% of admitted chat missed on it, while the prediction itself was accurate
	// to within 0.1 ms of the engine's own observation. The threshold, not the
	// prediction, was what let those placements through.
	//
	// Placing a request that then misses is not free: it holds a decode slot and
	// its KV for its whole life and returns nothing, and under the offered
	// denominator a rejection and a miss score the same. The margin is the one
	// already in use rather than a new constant, so this makes the two paths ask
	// the same question instead of introducing a second answer.
	budget := req.nominalMs
	if p.cfg.forceMargin {
		budget *= fsAllowanceUtilisation
	}
	return req.nominalMs > 0 && c.meanAfter > budget
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
//
// On its own the asymmetry has no notion of WHICH class did the breaking, and
// that is the gap the class term below closes: an instance broken by agent work
// and full of agent work should keep taking agent work, while an instance broken
// by one stray agent request and otherwise full of chat should not.
func (p *fluidserveDispatchPolicy) harmToIncumbents(
	f *instanceFlux, tier int, meanBefore, meanAfter float64) float64 {

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

	// The sum over incumbents is not the whole cost, and what it leaves out is
	// what decides where UNSERVABLE work goes.
	//
	// A request that has already passed its budget drops out of the sum, which is
	// correct as far as it goes: refusing to slow the instance does not rescue
	// it. The consequence is that an instance whose class has begun to miss reads
	// as costing nothing, so it attracts more foreign work, which makes the rest
	// of that class miss too, which lowers the reading further. Measured with
	// admission disabled at 3000 rpm, chat fell to 54.5% while PolyServe held it
	// at 100% by never letting agent work onto chat's servers at all.
	//
	// What the sum misses is that an instance is not only the requests on it now.
	// It is also where the class collecting there will arrive next, and that
	// claim does not disappear when the current occupants start missing. The
	// share of the instance belonging to OTHER classes measures that claim, and
	// it is charged at the scale of one maximally harmed request so that it
	// separates instances the incumbent sum cannot tell apart without overriding
	// a real, large difference between them.
	//
	// An empty instance is charged nothing: it belongs to no class yet, and it is
	// the placement that costs least by any reading.
	// Two switches, and they are separate on purpose. --fluidserve-enable-affinity
	// turns off ALL class preference, which is the ablation that asks where the
	// separation comes from at all. --fluidserve-class-harm turns off only this
	// term, which is the ablation that asks what it is worth to protect an
	// instance whose own class has already started missing -- a question the
	// feasible-set ordering cannot answer, because in that regime nothing is
	// feasible and that ordering never runs.
	if p.cfg.enableAffinity && p.cfg.classHarm && len(f.live) > 0 {
		harm += (1 - classShare(f, tier)) * fsHarmCap
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
// The pace assumed after the wait is the one the best available instance would
// deliver if the request were placed on it now. An earlier version assumed the
// cost of an iteration on an EMPTY instance instead, on the grounds that the
// question is whether success is possible at all rather than whether it is
// likely. That is the wrong question once the offered load is a multiple of the
// capacity: the fleet is not going to empty, so the wait is spent and the
// request is rejected at the end of it anyway, having occupied a client slot
// throughout and re-entered the scheduling path at every retry. Measured at
// 3000 rpm, agent requests held for around sixteen seconds before being
// rejected, and the resulting call rate was what saturated the scheduler.
//
// Using the delivered pace also makes this test and the rejection test agree:
// one asks whether a placement made later could succeed, the other whether one
// made now would, and they should differ only in the time still available.
//
// An earlier version also capped the wait at a quarter of the time-to-first-token
// budget, on the grounds that a request held longer was a miss whatever happened
// next. That was wrong: the rule is time to first token AND mean time between
// tokens, so a request held 4.5 s of a 5 s budget and then served immediately
// meets both. The cap is gone and with it one hand-set fraction.
//
// The deadline reserves the re-decision interval as well as the fixed margin,
// and the two are subtracted rather than one of them taken, because they cover
// different things. The fixed margin covers error in the prefill estimate. The
// re-decision interval covers the fact that the request is only looked at on the
// gateway's retry cadence: a deadline that leaves less margin than one interval
// can be passed in the gap between two calls, with nothing ever evaluating it
// inside its budget. Measured, the margin was 300 ms against a 500 ms cadence,
// so a chat request whose deadline fell at 4,650 ms was asked at 4,500 -- still
// allowed to wait -- and then not asked again until 5,000, already past its
// 5,000 ms budget. That is the shape of the observed distribution: a median
// time to first token of 4.08 s and a 90th percentile of 5.95 s.
//
// The interval is MEASURED, not configured, so this stays correct whatever
// cadence the gateway is set to and adds no constant of its own.
// What is subtracted for "everything that still happens after the placement" is
// a MEASURED one-sided upper bound, not a modelled prefill time plus a constant.
//
// The modelled version priced the prefill compute and nothing else: not the
// engine's own queue, not the tail of the iteration running when the request
// lands, and not the spread of either. At 80 req/s it came to about 375 ms
// including the 300 ms safety constant, against a realised 500 ms median with a
// tail past 2 s. The consequence is visible directly in the time-to-first-token
// distribution, which is bimodal: a flat stretch up to 3.5 s of requests placed
// while waiting, and a spike of 70% of all admitted chat requests at 3.75-5.25 s
// -- the deadline pile-up. Every violation was the part of that spike that
// overshot the 5.0 s budget, so the failures were not misjudged feasibility,
// they were the deadline being set with no room for the variance of what
// follows it.
//
// Using mean + z*sd here makes this the same treatment expectedOutflow gives the
// KV it expects back, with the same z. It also removes ttftSafetyMs: that
// constant existed to stand in for exactly this quantity and is now measured.
func (p *fluidserveDispatchPolicy) canWait(best candidate, req *fluidserveRequest) bool {
	waited := float64(req.nowMs - req.arrivedMs)
	// Two parts, because they are two quantities. The prefill scales with THIS
	// request's prompt and the capacity model predicts it per request; the queue
	// residual does not depend on the prompt and is measured with its spread.
	// Collapsing both into one fleet-wide number made it an average over a class
	// mix that is 76.9% chat, and applying chat's number to an agent request with
	// a ten-times-longer prompt moved that class the wrong way.
	after := best.prefillMs
	if queue, samples := p.registry.PlacementDelayBound(p.cfg.zSafety); samples >= fsMinPlacementDelaySamples {
		after += queue
	} else {
		after += p.cfg.ttftSafetyMs
	}
	if math.IsInf(after, 0) {
		return false
	}
	var deadline float64
	if req.isE2E {
		pace := best.meanAfter
		if math.IsInf(pace, 0) || pace <= 0 {
			return false
		}
		deadline = req.budgetMs - after - req.expectedToks*pace - req.recheckMs
	} else {
		if req.ttftSloMs <= 0 {
			return false
		}
		deadline = req.ttftSloMs - after - req.recheckMs
	}
	return waited < deadline
}

func (p *fluidserveDispatchPolicy) prefillEstimateMs(
	req *fluidserveRequest, f *instanceFlux) float64 {

	chunk := 8192.0
	if f != nil && f.chunk > 0 {
		chunk = f.chunk
	}
	// Same discount as the admission test: the time to a first token is set by
	// the work the engine does, not by the length of the prompt.
	steps := prefillSteps(
		float64(req.promptTokens)*p.capacity.prefillFractionOf(), chunk)
	if steps <= 0 {
		return 0
	}
	per := p.capacity.prefillStepMs(math.Min(float64(req.promptTokens), chunk))
	if math.IsInf(per, 0) {
		return 0
	}
	// The work already ahead of this request is priced at ITS OWN chunk size, not
	// at this request's. The two are different quantities and mixing them is
	// wrong by the ratio between the prompts: the count is in units of the
	// engine's chunk, so charging it at the cost of a step carrying a 666-token
	// chat prompt when each of those chunks is 8,192 tokens understates the wait
	// by a factor of eight. Measured at 4800 rpm the instance carried 11,478
	// tokens of prefill, which is 728 ms of engine time, and this returned 84.
	//
	// It is the same error v21 fixed in the capacity model, in the other
	// direction: there a fractional count was rounded up against a full-chunk
	// price, here a full-chunk count is charged at a partial-chunk price. Both
	// come from the count and the unit price being taken from different prompts.
	//
	// Under light load the queued figure is a fraction of a chunk and this
	// changes almost nothing, which is the intended behaviour: the estimate
	// should only tighten where there is actually a backlog.
	queued := 0.0
	if f != nil && f.effectivePrefill > 0 {
		perQueued := p.capacity.prefillStepMs(math.Min(f.effectivePrefill, chunk))
		if !math.IsInf(perQueued, 0) {
			queued = prefillSteps(f.effectivePrefill, chunk) * perQueued
		}
	}
	return steps*per + queued
}

// placementProbe is the previous placement on one instance: which engine step it
// was judged against, how many placements that step has now carried, and the
// headroom the decision read.
type placementProbe struct {
	stepID   int64
	ordinal  int
	headroom float64
}

// noteePlacement publishes the two quantities §42.4 asked for.
//
//	dispatch_ordinal_in_step  how many placements this engine step has carried.
//	                          1 everywhere means each placement gets a fresh
//	                          view; a long tail means several are judged against
//	                          the same status pull.
//	headroom_move_in_step     for the second and later placement in a step, the
//	                          change in the headroom the decision read. Negative
//	                          means the view moved and the earlier placement was
//	                          accounted for. ZERO IS THE DEFECT: it means the
//	                          same capacity was offered twice.
func (p *fluidserveDispatchPolicy) commit(c candidate, req *fluidserveRequest, kind string) {
	p.probeMu.Lock()
	if p.lastProbe == nil {
		p.lastProbe = map[string]placementProbe{}
	}
	prev, seen := p.lastProbe[c.flux.id]
	ord := 1
	if seen && prev.stepID == c.flux.stepID {
		ord = prev.ordinal + 1
		metrics.Histogram("scheduler_fluidserve_headroom_move_in_step",
			metrics.Labels{}).Observe(c.flux.headroom - prev.headroom)
	}
	p.lastProbe[c.flux.id] = placementProbe{
		stepID: c.flux.stepID, ordinal: ord, headroom: c.flux.headroom}
	p.probeMu.Unlock()
	metrics.Histogram("scheduler_fluidserve_dispatch_ordinal_in_step",
		metrics.Labels{}).Observe(float64(ord))

	p.registry.onDispatch(c.flux.id, req.id, req.tier, req.promptTokens,
		c.flux.chunk, c.flux.stepID, req.nowMs, c.prefillMs)

	metrics.Counter("scheduler_fluidserve_decisions_total",
		metrics.Labels{{Name: "decision", Value: kind}}).Inc()
	metrics.Histogram("scheduler_fluidserve_headroom_at_dispatch",
		metrics.Labels{}).Observe(c.headroomAfter)
	metrics.Histogram("scheduler_fluidserve_harm", metrics.Labels{}).Observe(c.harm)
	metrics.Histogram("scheduler_fluidserve_class_share",
		metrics.Labels{}).Observe(c.share)

	klog.V(5).Infof("FluidServe %s request %s (tier %dms, prompt %d) -> %s: "+
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
		classHarm:      p.FluidserveClassHarm,
		forceMargin:    p.FluidserveForceMargin,
		ownBudgetGate:  p.FluidserveOwnBudgetGate,

		kvSlopeProjection: p.FluidserveKvSlopeProjection,
		gateSlack:         p.FluidserveGateSlack,
	}
	if cfg.horizonSteps <= 0 {
		panic("--fluidserve-horizon-steps must be positive")
	}
	if cfg.gateSlack < 1 {
		panic("--fluidserve-gate-slack must be at least 1: below 1 the gate " +
			"would refuse work the tightest resident class was promised")
	}

	policy := &fluidserveDispatchPolicy{
		cfg:       cfg,
		capacity:  newCapacityModel(profile, predictor),
		lengths:   lengths,
		registry:  newRequestRegistry(lengths, budgets),
		lastObs:   map[string]stepObservation{},
		shedIDs:   map[string]int64{},
		fluxCache: map[string]cachedFlux{},
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
		"ttft margin %dms, pend=%v, shed=%v, affinity=%v, flux=%v, classharm=%v, "+
		"forcemargin=%v, ownbudgetgate=%v, kvslope=%v, gateslack=%.3f, budgets %q",
		cfg.horizonSteps, cfg.zSafety, p.FluidserveTtftSafetyMs, cfg.enablePend,
		cfg.enableShed, cfg.enableAffinity, cfg.enableFlux, cfg.classHarm,
		cfg.forceMargin, cfg.ownBudgetGate, cfg.kvSlopeProjection, cfg.gateSlack,
		p.FluidserveClassBudgets)

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
		metrics.Gauge("scheduler_fluidserve_prefill_fraction",
			metrics.Labels{}).Set(p.capacity.prefillFractionOf())
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
