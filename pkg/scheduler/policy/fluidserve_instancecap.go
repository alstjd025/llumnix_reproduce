package policy

// The class-instance cap (EXP-107).
//
// An instance's gate is the tightest nominal per-token budget among the
// requests running on it, so a single request of a tight class converts a
// loose-paced instance (large admissible batch) into a tight-paced one. That
// conversion is instant, while the reverse requires the class to drain from
// the instance for a full residence time -- and as long as the router keeps
// sending the class there, the drain never starts. Left alone, the number of
// instances a class gates therefore tracks the running maximum of its past
// demand rather than its current demand (measured: at 25 req/s the whole
// fleet sat at chat's 50 ms in 72-85% of scrapes while chat's work share was
// about 57%).
//
// The cap makes that number a governed quantity:
//
//	demandInstances = ceil( arrival rate / per-instance service rate at the
//	                        class's pace )
//
// and enforces it as a GUARDRAIL, not a partition. Below the limit,
// placements are unrestricted. At or past it, the cap first asks whether any
// OTHER class with arrivals is short of its own demand-derived count; if
// nobody is, spreading costs nothing and nothing is filtered. Only while
// somebody is short may the class be placed solely on (a) the `limit`
// gate-holding instances with the most of its residents -- so the surplus
// gate-holders stop receiving the class and drain within one residence --
// or (b) instances whose gate is at least as tight already, where the
// placement cannot move the gate ("riding"). Placements that would newly
// gate any other instance are removed from the candidate list before
// sorting, exactly as applyClassPin removes candidates, so every downstream
// path (route, pend, shed, force) sees only permitted destinations.
//
// What the cap is NOT: it does not create capacity (the gate/memory trade
// measured in EXP-93 section 9.5 still holds inside the permitted set), it
// does not spread a class within its limit, and it does not defend against a
// class whose demand genuinely fills the fleet -- a sustained flood raises
// its own limit, which is intended; weighting between classes is operator
// policy, not measurement.
import (
	"math"
	"sort"
	"strconv"

	"k8s.io/klog/v2"

	"llumnix/pkg/metrics"
)

const (
	// fsCapBucketMs is how much wall clock a per-tier arrival bucket spans
	// before it is folded into the rate estimate. Five seconds keeps the
	// estimate responsive while giving the busiest tier hundreds of arrivals
	// per sample.
	fsCapBucketMs = 5000.0
	// The smoothing time constant is capWindowMult residence times, clamped to
	// this range. The lower clamp keeps a very short class from tracking
	// bursts; the upper clamp keeps deepresearch (residence ~98 s) from
	// lagging a whole trace segment behind its demand -- the limit is an
	// integer, so it does not need the full smoothing its residence would ask
	// for.
	fsCapTauMinS = 15.0
	fsCapTauMaxS = 120.0
	// Hysteresis on the integer limit: raise only when the demand exceeds the
	// current limit by this margin. Without it a tier whose demand sits at an
	// integer boundary (swe's measured demand is 0.995 instances on the
	// mix-shift trace) flips its limit on estimator noise.
	fsCapRaiseEps = 0.05
)

// tierArrivalState is the per-class demand estimate behind the cap. All fields
// are guarded by fluidserveDispatchPolicy.capMu.
type tierArrivalState struct {
	bucketStartMs int64
	count         int
	promptSum     float64

	// lambda is the offered arrival rate in requests per second, an EWMA over
	// completed buckets. Offered rather than admitted, deliberately: sizing
	// the cap to admitted demand would let a rejection shrink the limit and
	// the smaller limit cause more rejections. Counted once per request (the
	// caller passes only first sightings), so gateway retries of a held
	// request do not inflate the tier's rate.
	lambda float64
	// meanPrompt is the EWMA of prompt tokens per request, the per-request
	// prefill work the service-rate model charges.
	meanPrompt float64
	// ready flips when the first bucket completes; until then the cap is
	// inactive for the tier (fail-open, ~5 s after the first arrival).
	ready bool
	// prevLimit is the hysteresis state; see capLimitFor.
	prevLimit int
}

// capMetricTiers returns the tiers the profile knows, for metric
// pre-registration: a labelled counter that has never been incremented exports
// no series at all, which makes "the smoke run never exercised the cap" and
// "the collector is not picking the series up" indistinguishable.
func (m *lengthModel) tiers() []int {
	out := make([]int, 0, len(m.byTier))
	for t := range m.byTier {
		out = append(out, t)
	}
	sort.Ints(out)
	return out
}

func capTierLabel(tier int) metrics.Labels {
	return metrics.Labels{{Name: "tier", Value: strconv.Itoa(tier)}}
}

// preRegisterCapMetrics exports every series this file and the shed path emit,
// at zero, so a run's first scrape carries them whether or not they ever fire.
// The limit gauge starts at -1, which is the sentinel for "cap not active for
// this tier yet" -- 0 is a meaningful limit and must not be the idle value.
func (p *fluidserveDispatchPolicy) preRegisterCapMetrics() {
	for _, tier := range p.lengths.tiers() {
		l := capTierLabel(tier)
		metrics.Gauge("scheduler_fluidserve_instcap_limit", l).Set(-1)
		metrics.Gauge("scheduler_fluidserve_instcap_gate_count", l).Set(0)
		metrics.Gauge("scheduler_fluidserve_instcap_lambda", l).Set(0)
		metrics.Counter("scheduler_fluidserve_instcap_excluded_total", l).Add(0)
		metrics.Counter("scheduler_fluidserve_instcap_blocked_feasible_total", l).Add(0)
		metrics.Counter("scheduler_fluidserve_instcap_empty_fallback_total", l).Add(0)
		metrics.Counter("scheduler_fluidserve_instcap_free_gate_total", l).Add(0)
	}
	for _, r := range []string{"cannot_meet", "no_feasible"} {
		metrics.Counter("scheduler_fluidserve_shed_reason_total",
			metrics.Labels{{Name: "reason", Value: r}}).Add(0)
	}
}

// noteTierArrival feeds one FIRST sighting of a request into its tier's demand
// estimate. The caller guarantees dedup (registry.noteArrival's first return),
// so a request held and re-examined every 500 ms counts once.
func (p *fluidserveDispatchPolicy) noteTierArrival(tier, promptTokens int, nowMs int64) {
	if !p.cfg.classInstanceCap {
		return
	}
	p.capMu.Lock()
	defer p.capMu.Unlock()
	st := p.tierArr[tier]
	if st == nil {
		st = &tierArrivalState{bucketStartMs: nowMs}
		p.tierArr[tier] = st
	}
	st.count++
	st.promptSum += float64(promptTokens)
	elapsedS := float64(nowMs-st.bucketStartMs) / 1000.0
	if elapsedS*1000.0 < fsCapBucketMs {
		return
	}
	rate := float64(st.count) / elapsedS
	prompt := st.promptSum / float64(st.count)
	if !st.ready {
		// Warm start: the first completed bucket becomes the estimate. The
		// limit downstream is an integer, so first-bucket noise is tolerable,
		// and the alternative -- ramping up from zero -- keeps the cap
		// inactive for a whole time constant at the start of every condition,
		// which under --restart-per-condition is the start of every run.
		st.lambda, st.meanPrompt, st.ready = rate, prompt, true
	} else {
		alpha := p.capAlphaLocked(tier, elapsedS)
		st.lambda += alpha * (rate - st.lambda)
		st.meanPrompt += alpha * (prompt - st.meanPrompt)
	}
	st.count, st.promptSum, st.bucketStartMs = 0, 0, nowMs
}

// capAlphaLocked is the EWMA gain for one completed bucket of this tier:
// bucket length over the tier's smoothing time constant, which is
// capWindowMult residence times clamped to [fsCapTauMinS, fsCapTauMaxS].
// Residence is approximated as the decode phase alone -- prefill adds well
// under a second against 21-98 s -- because this constant only sets how fast
// the estimate moves, not what it converges to.
func (p *fluidserveDispatchPolicy) capAlphaLocked(tier int, bucketS float64) float64 {
	nominal, expected, _, _ := p.registry.requestBudget(tier)
	residenceS := nominal * expected / 1000.0
	tau := p.cfg.capWindowMult * residenceS
	if tau < fsCapTauMinS {
		tau = fsCapTauMinS
	}
	if tau > fsCapTauMaxS {
		tau = fsCapTauMaxS
	}
	alpha := bucketS / tau
	if alpha > 1 {
		alpha = 1
	}
	return alpha
}

// gateIsFree reports whether letting this request's class set the gate on this
// instance would cost the instance no admissible capacity.
//
// It answers false when the cost cannot be computed -- an instance with no live
// request has an infinite capKv, and extrapolating from infinity would report
// "free" for the one placement the riding comment above calls the new gate the
// cap exists to refuse. False there means the cap decides as it did before.
func (p *fluidserveDispatchPolicy) gateIsFree(f *instanceFlux, req *fluidserveRequest) bool {
	if f == nil || req.nominalMs <= 0 {
		return false
	}
	now := f.gateAllowance
	if math.IsInf(now, 0) {
		return false
	}
	after := math.Min(now, req.nominalMs)
	cost, ok := p.capacity.gateTighteningCost(
		f.id, f.capKv, f.capMem,
		now*fsAllowanceUtilisation, after*fsAllowanceUtilisation)
	return ok && cost <= 0
}

// capLimitFor turns a demand in instances into the integer limit, with
// hysteresis: the limit rises only when demand exceeds it by fsCapRaiseEps,
// and never falls below one -- a class with a request in hand may always hold
// one instance, or its first arrival after an idle period could be refused
// everywhere.
func capLimitFor(st *tierArrivalState, demand float64) int {
	target := int(math.Ceil(demand))
	if target < 1 {
		target = 1
	}
	prev := st.prevLimit
	if prev < 1 {
		return target
	}
	if target > prev && demand <= float64(prev)+fsCapRaiseEps {
		return prev
	}
	return target
}

// applyInstanceCap removes, from the candidate list, every destination the cap
// forbids for this request. It runs after applyClassPin and before the sort,
// and it REMOVES candidates rather than marking them infeasible, because the
// forced-placement path picks the least-damaging candidate whether or not it
// is feasible -- a marked candidate would still be a legal force destination
// and the cap would leak exactly under the load it exists for.
func (p *fluidserveDispatchPolicy) applyInstanceCap(
	cands []candidate, req *fluidserveRequest) []candidate {

	if !p.cfg.classInstanceCap || len(cands) == 0 {
		return cands
	}
	// An explicit pin is an operator assignment; the two filters composed can
	// produce an empty intersection, and the pin is the more explicit
	// statement, so it wins.
	if _, pinned := p.cfg.classPin[req.tier]; pinned {
		return cands
	}

	// The service-rate model needs the engine's prefill chunk size and a pool
	// size; both come from the instances at hand rather than from constants.
	chunk, kvCap := 0.0, 0.0
	for i := range cands {
		if f := cands[i].flux; f != nil {
			if f.chunk > chunk {
				chunk = f.chunk
			}
			if f.capMem > kvCap {
				kvCap = f.capMem
			}
		}
	}

	// Demand per tier, in instances, under one lock hold. Every ready tier is
	// updated (not only the arriving one) because the starvation test below
	// needs all of them, and because each tier's hysteresis state should
	// advance at the same cadence whether or not its own requests are
	// arriving.
	//
	// There is deliberately NO proportional rescale under scarcity here. The
	// first measured hour had one, and it ran the whole hour rather than in
	// some exceptional overload regime: the heavy-input classes' low
	// per-instance service rates put the summed demand at 12-14 instances on
	// a fleet of 4 at every loaded minute, so chat's limit was the rescaled
	// ceil(4*2/14) = 1 for three quarters of the run while its arrivals ran
	// 10-30 req/s -- a standing partition, which is the design this cap
	// exists to avoid, and it cost chat ten points of rejections
	// (EXP-107 rep 1 diagnosis). Instance-equivalent shares are not goodput
	// shares; under-fleet demand is admission control's job, and the cap's
	// job is only the clause below: no class may hold MORE than its own
	// demand while another class goes short.
	p.capMu.Lock()
	reqSt := p.tierArr[req.tier]
	if reqSt == nil || !reqSt.ready {
		// Fail-open until the tier's first bucket completes (~5 s after its
		// first arrival). The limit floor of one makes the open window cheap:
		// even a burst that colonises the fleet inside it is contracted once
		// the estimate exists.
		p.capMu.Unlock()
		return cands
	}
	type tierNeed struct {
		tier  int
		limit int
	}
	var others []tierNeed
	reqLimit := 0
	reqLambda := reqSt.lambda
	for tier, st := range p.tierArr {
		if !st.ready {
			continue
		}
		nominal, expected, _, _ := p.registry.requestBudget(tier)
		mu := p.capacity.tierServiceRate(
			nominal*fsAllowanceUtilisation, st.meanPrompt, expected, chunk, kvCap)
		demand := 0.0
		if mu > 0 {
			demand = st.lambda / mu
		}
		limit := capLimitFor(st, demand)
		st.prevLimit = limit
		if tier == req.tier {
			reqLimit = limit
		} else {
			others = append(others, tierNeed{tier, limit})
		}
	}
	p.capMu.Unlock()

	n := len(cands)
	limit := reqLimit
	if limit > n {
		limit = n
	}

	// The gate holders of this tier, over the SAME slice being filtered --
	// counting instances that are not in the list would let absent holders
	// exhaust the limit while contributing no permitted destination.
	type holder struct {
		idx       int
		residents int
		id        string
	}
	var pool []holder
	for i := range cands {
		if cands[i].flux.gateTier == req.tier {
			pool = append(pool, holder{i, classCount(cands[i].flux, req.tier), cands[i].flux.id})
		}
	}

	lbl := capTierLabel(req.tier)
	metrics.Gauge("scheduler_fluidserve_instcap_limit", lbl).Set(float64(limit))
	metrics.Gauge("scheduler_fluidserve_instcap_gate_count", lbl).Set(float64(len(pool)))
	metrics.Gauge("scheduler_fluidserve_instcap_lambda", lbl).Set(reqLambda)

	if len(pool) < limit {
		// Below the limit nothing is filtered: the class may open a new gate,
		// and where it opens is the sort's business.
		return cands
	}

	// The guardrail clause: holding gates at or past one's own demand is only
	// refused while it takes something from somebody -- some other class with
	// arrivals whose gate count is short of ITS demand. If every other class
	// has the instances its demand asks for (or asks for none), spreading
	// costs nothing and refusing it would only manufacture rejections; the
	// low-load evenings of a static sweep are exactly this case. The test
	// reads only quantities already computed -- the other tiers' limits and
	// their gate counts over the same candidate slice -- so it adds no
	// constant.
	starved := false
	for _, o := range others {
		count := 0
		for i := range cands {
			if cands[i].flux.gateTier == o.tier {
				count++
			}
		}
		want := o.limit
		if want > n {
			want = n
		}
		if count < want {
			starved = true
			break
		}
	}
	if !starved {
		return cands
	}

	// At or above the limit, the permitted gate holders are the `limit` with
	// the most residents of the class -- the instances the class preference
	// has already filled -- so the surplus holders stop receiving the class
	// and drain within one residence time. Ties break on instance id so two
	// consecutive decisions designate the same set.
	sort.Slice(pool, func(a, b int) bool {
		if pool[a].residents != pool[b].residents {
			return pool[a].residents > pool[b].residents
		}
		return pool[a].id < pool[b].id
	})
	designated := make(map[int]bool, limit)
	for i := 0; i < limit && i < len(pool); i++ {
		designated[pool[i].idx] = true
	}

	keep := cands[:0:0]
	for i := range cands {
		f := cands[i].flux
		switch {
		case designated[i]:
			keep = append(keep, cands[i])
		case f.gateTier != req.tier && f.gateTierNominal <= req.nominalMs:
			// Riding: the instance's gate is already at least as tight as this
			// class's promise, so the placement cannot move it. An empty
			// instance has an infinite gateTierNominal and lands in the
			// removal branch below, which is the point -- gating an empty
			// instance is exactly the new gate the cap exists to refuse.
			keep = append(keep, cands[i])
		case p.cfg.capCostsCapacity && f.gateTier != req.tier && p.gateIsFree(f, req):
			// The gate this placement would OPEN costs the instance no
			// admissible capacity, so there is nothing for the cap to protect
			// by refusing it. Restricted to instances this class does not
			// already gate: on one it does, the tightening is a no-op and the
			// cost is trivially zero, so without that guard this branch would
			// keep every surplus gate holder and the drain -- the only thing
			// that contracts a class's footprint -- would never run, on any
			// fleet. Admission takes min(capKv, capMem) and only capKv
			// moves with the gate, so this is the case where capMem is the
			// smaller ceiling on both sides. It is not a corner: measured at
			// 185 req/s on eight Llama-3.1-8B instances it holds for every
			// loaded scrape for deepresearch, 98.9% for swe and 77.0% for chat,
			// and chat's figure splits by how loaded the instance is -- 11.7%
			// and 37.7% on the two whose capKv had been pushed down, 84.5% to
			// 95.3% on the idler ones. On four 70B instances the pace ceiling
			// refused 38 placements per one the physical pool refused, so there
			// this branch is expected to fire rarely and the cap to behave as
			// it does today.
			metrics.Counter("scheduler_fluidserve_instcap_free_gate_total", lbl).Inc()
			keep = append(keep, cands[i])
		default:
			metrics.Counter("scheduler_fluidserve_instcap_excluded_total", lbl).Inc()
			if cands[i].feasible {
				metrics.Counter(
					"scheduler_fluidserve_instcap_blocked_feasible_total", lbl).Inc()
			}
		}
	}
	if len(keep) == 0 {
		// Unreachable while the designated set is drawn from this slice
		// (len(pool) >= limit >= 1 puts at least one candidate in keep), but
		// an empty list panics at cands[0] downstream, so refuse to filter
		// rather than refuse to serve, and say so loudly.
		metrics.Counter("scheduler_fluidserve_instcap_empty_fallback_total", lbl).Inc()
		klog.Warningf("FluidServe instance cap: filtering tier %d would empty "+
			"the candidate list (limit %d, gate holders %d of %d candidates); "+
			"leaving it unfiltered", req.tier, limit, len(pool), len(cands))
		return cands
	}
	return keep
}
