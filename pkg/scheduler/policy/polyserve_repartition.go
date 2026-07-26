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

	"llumnix/pkg/metrics"
	"llumnix/pkg/types"
)

// Dynamic tier -> server repartitioning.
//
// PolyServe has no static placement algorithm: its autoscaler grows and shrinks
// each tier's cluster, and the "route to the highest-load server that still
// meets the SLO" rule exists to build a load gradient that autoscaler can act
// on. We were told to leave autoscaling out, so instead of the mechanism we
// reproduce its fixed point -- the allocation the tiers would converge to -- by
// redividing a fleet of constant size in proportion to demand.
//
// Reassignment costs nothing because a tier label only gates NEW dispatches.
// Requests already running finish where they are, so no KV is moved and no
// migration is needed.

const (
	// How much work one request of a tier costs a server is estimated from the
	// same profiling tables the admission test uses, so the allocation and the
	// admission decisions cannot disagree about the hardware.
	repartitionPeriod = 10 * time.Second
	// Applying a new allocation only after it has been computed this many times
	// in a row keeps a burst from shuffling servers back and forth; a server
	// that changes tier drains its old tier's queue first, so churn is not free
	// even though reassignment itself is.
	repartitionStableRounds = 2
	// Weight of the newest window in the demand estimate.
	repartitionEwmaAlpha = 0.5
	// Batch sizes probed when solving for the largest batch that still meets a
	// tier's TPOT. Matches the profiling table's own axis.
	maxProbeBatch = 2048
)

type tierObservation struct {
	requests    int64
	inputTokens int64
}

// tierRepartitioner watches arrivals per tier and redivides the fleet.
type tierRepartitioner struct {
	mu sync.Mutex

	period       time.Duration
	stableRounds int
	ewmaAlpha    float64

	decodeTokens *tierDecodeTokens
	predictor    *LatencyPredictor
	partition    *tierPartition

	windowStart time.Time
	observed    map[int]*tierObservation

	// demand is server-seconds of work arriving per second, i.e. how many whole
	// servers a tier needs to keep up.
	demand map[int]float64

	pendingTarget map[int]int
	pendingRounds int
	current       map[int]int
}

func newTierRepartitioner(
	decodeTokens *tierDecodeTokens, predictor *LatencyPredictor, partition *tierPartition,
) *tierRepartitioner {
	return &tierRepartitioner{
		period:       repartitionPeriod,
		stableRounds: repartitionStableRounds,
		ewmaAlpha:    repartitionEwmaAlpha,
		decodeTokens: decodeTokens,
		predictor:    predictor,
		partition:    partition,
		observed:     map[int]*tierObservation{},
		demand:       map[int]float64{},
		current:      map[int]int{},
	}
}

// observe records one arrival. Requests with no SLO of their own carry tier 0;
// they are unrestricted by the tier filter, so counting them would inflate the
// demand of a partition they never occupy.
func (r *tierRepartitioner) observe(request *types.SchedulingRequest) {
	if request == nil || request.TpotSloMs <= 0 {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	obs := r.observed[request.TpotSloMs]
	if obs == nil {
		obs = &tierObservation{}
		r.observed[request.TpotSloMs] = obs
	}
	obs.requests++
	obs.inputTokens += int64(request.PromptNumTokens)
}

// serverSecondsPerRequest is what one request of a tier costs a server: its
// prefill, plus its decode charged at the largest batch that still honours the
// tier's TPOT. A tighter TPOT forces a smaller batch, so the same output length
// costs more server time -- which is exactly why a strict tier deserves more
// servers per request than a lax one.
func (r *tierRepartitioner) serverSecondsPerRequest(
	tierTpotMs int, meanInputTokens float64, maxNumBatchedTokens int32) float64 {

	outTokens := float64(r.decodeTokens.forTier(tierTpotMs))

	prefillSeconds := 0.0
	if tput := r.prefillThroughput(maxNumBatchedTokens); tput > 0 {
		prefillSeconds = meanInputTokens / tput
	}

	decodeSeconds := 0.0
	// Average KV over the request's life: it starts at the prompt and grows by
	// the output, so the mean is prompt + half the output.
	kvPerRequest := meanInputTokens + outTokens/2
	if batch := r.maxBatchForTpot(tierTpotMs, kvPerRequest); batch > 0 {
		// A server running `batch` requests emits `batch` tokens every TPOT, so
		// one request's output occupies outTokens * TPOT / batch server-seconds.
		decodeSeconds = outTokens * (float64(tierTpotMs) / 1000.0) / float64(batch)
	}
	return prefillSeconds + decodeSeconds
}

func (r *tierRepartitioner) prefillThroughput(maxNumBatchedTokens int32) float64 {
	if r.predictor == nil || maxNumBatchedTokens <= 0 {
		return 0
	}
	stepMs, err := r.predictor.predictPrefillStepLatency(maxNumBatchedTokens)
	if err != nil || stepMs <= 0 || math.IsInf(stepMs, 0) {
		return 0
	}
	return float64(maxNumBatchedTokens) / (stepMs / 1000.0)
}

// maxBatchForTpot solves for the largest batch whose predicted iteration still
// fits the tier's TPOT, by probing the profiling table.
func (r *tierRepartitioner) maxBatchForTpot(tierTpotMs int, kvPerRequest float64) int32 {
	if r.predictor == nil || tierTpotMs <= 0 {
		return 0
	}
	best := int32(0)
	for batch := int32(1); batch <= maxProbeBatch; batch *= 2 {
		iterMs, err := r.predictor.predictTpotLatency(batch, int32(float64(batch)*kvPerRequest))
		if err != nil || math.IsInf(iterMs, 0) {
			break
		}
		if iterMs > float64(tierTpotMs) {
			break
		}
		best = batch
	}
	if best == 0 {
		// Even a batch of one misses this TPOT on this hardware. Charge the
		// tier as if it got a whole server per concurrent request rather than
		// dividing by zero.
		best = 1
	}
	return best
}

// maybeRepartition recomputes the allocation once per period. It is called from
// the scheduling path rather than a background goroutine so that it always sees
// the same live instance set the request is about to be scheduled against.
func (r *tierRepartitioner) maybeRepartition(
	now time.Time, instanceViews map[string]*instanceViewScheduling) {

	r.mu.Lock()
	defer r.mu.Unlock()

	if r.windowStart.IsZero() {
		r.windowStart = now
		return
	}
	elapsed := now.Sub(r.windowStart)
	if elapsed < r.period {
		return
	}

	live := make([]string, 0, len(instanceViews))
	maxNumBatchedTokens := int32(0)
	for id, view := range instanceViews {
		live = append(live, id)
		if view.cmsView != nil && view.cmsView.Metadata != nil {
			if b := view.cmsView.Metadata.MaxNumBatchedTokens; b > maxNumBatchedTokens {
				maxNumBatchedTokens = b
			}
		}
	}
	sort.Strings(live)

	// Fold this window's arrivals into the smoothed demand. Tiers that went
	// quiet decay towards zero instead of holding their old allocation.
	seconds := elapsed.Seconds()
	for tier := range r.demand {
		if _, seen := r.observed[tier]; !seen {
			r.demand[tier] *= 1 - r.ewmaAlpha
		}
	}
	for tier, obs := range r.observed {
		rate := float64(obs.requests) / seconds
		meanInput := float64(obs.inputTokens) / float64(obs.requests)
		cost := r.serverSecondsPerRequest(tier, meanInput, maxNumBatchedTokens)
		r.demand[tier] = r.ewmaAlpha*(rate*cost) + (1-r.ewmaAlpha)*r.demand[tier]
	}
	r.observed = map[int]*tierObservation{}
	r.windowStart = now

	// Publish on every window, not only on the windows that move a server:
	// the point of the series is to show the allocation TRACKING demand, which
	// needs the flat stretches too. Deferred so the early returns below (no
	// live servers, hysteresis not satisfied) still emit a sample -- otherwise
	// the gauge would go stale exactly when nothing is changing. klog carries
	// the same information but only survives as long as the pod's log buffer,
	// which a long run outlives.
	defer r.publishAllocation(len(live))

	if len(live) == 0 {
		return
	}
	target := allocateServers(r.demand, len(live))
	if len(target) == 0 {
		return
	}

	// Hysteresis: only act once the same allocation has been asked for
	// repeatedly, so a single burst does not move servers between tiers.
	if sameAllocation(target, r.current) {
		r.pendingTarget, r.pendingRounds = nil, 0
		return
	}
	if !sameAllocation(target, r.pendingTarget) {
		r.pendingTarget, r.pendingRounds = target, 1
		return
	}
	r.pendingRounds++
	if r.pendingRounds < r.stableRounds {
		return
	}

	assignment := assignInstances(r.partition.snapshot(), target, live)
	r.partition.set(assignment)
	r.current = target
	r.pendingTarget, r.pendingRounds = nil, 0

	klog.Infof("PolyServe repartition: %s (demand %s, %d live servers)",
		formatAllocation(target), formatDemand(r.demand), len(live))
}

// publishAllocation exports the current tier -> server split and the smoothed
// demand that produced it, so a run's repartitioning history can be read back
// from the metrics scrape instead of from scheduler logs.
//
// Called with r.mu held (from the repartition path).
func (r *tierRepartitioner) publishAllocation(liveServers int) {
	metrics.Gauge("scheduler_polyserve_live_servers", nil).Set(float64(liveServers))
	// Iterate over demand, not over current: a tier that has gone quiet keeps
	// decaying in demand and must keep reporting (its servers were taken away),
	// whereas iterating over `current` would silently drop it from the series.
	for tier, d := range r.demand {
		labels := metrics.Labels{{Name: "tpot_slo_ms", Value: strconv.Itoa(tier)}}
		metrics.Gauge("scheduler_polyserve_tier_demand", labels).Set(d)
		metrics.Gauge("scheduler_polyserve_tier_servers", labels).
			Set(float64(r.current[tier]))
	}
}

// allocateServers divides n servers among tiers in proportion to demand, by
// largest remainder, then guarantees every tier at least one server.
func allocateServers(demand map[int]float64, n int) map[int]int {
	tiers := make([]int, 0, len(demand))
	total := 0.0
	for tier, d := range demand {
		tiers = append(tiers, tier)
		if d > 0 {
			total += d
		}
	}
	sort.Ints(tiers)
	if len(tiers) == 0 || n <= 0 {
		return map[int]int{}
	}
	// Fewer servers than tiers: the guarantee is impossible, so the busiest
	// tiers get one each and the rest stay unallocated -- which the filter
	// reads as unrestricted, not starved.
	if n < len(tiers) {
		byDemand := append([]int(nil), tiers...)
		sort.SliceStable(byDemand, func(i, j int) bool {
			if demand[byDemand[i]] != demand[byDemand[j]] {
				return demand[byDemand[i]] > demand[byDemand[j]]
			}
			return byDemand[i] < byDemand[j]
		})
		out := map[int]int{}
		for _, tier := range byDemand[:n] {
			out[tier] = 1
		}
		return out
	}

	out := make(map[int]int, len(tiers))
	if total <= 0 {
		// No traffic seen yet: spread evenly rather than pick a favourite.
		base, extra := n/len(tiers), n%len(tiers)
		for i, tier := range tiers {
			out[tier] = base
			if i < extra {
				out[tier]++
			}
		}
		return out
	}

	type remainder struct {
		tier int
		frac float64
	}
	assigned := 0
	rems := make([]remainder, 0, len(tiers))
	for _, tier := range tiers {
		exact := float64(n) * math.Max(demand[tier], 0) / total
		whole := int(math.Floor(exact))
		out[tier] = whole
		assigned += whole
		rems = append(rems, remainder{tier, exact - float64(whole)})
	}
	sort.SliceStable(rems, func(i, j int) bool {
		if rems[i].frac != rems[j].frac {
			return rems[i].frac > rems[j].frac
		}
		return rems[i].tier < rems[j].tier
	})
	for i := 0; assigned < n; i, assigned = i+1, assigned+1 {
		out[rems[i%len(rems)].tier]++
	}

	// Every tier keeps at least one server, taken from the largest holder, so a
	// tier is never left with nowhere to go.
	for _, tier := range tiers {
		if out[tier] > 0 {
			continue
		}
		donor, donorCount := -1, 1
		for _, candidate := range tiers {
			if out[candidate] > donorCount {
				donor, donorCount = candidate, out[candidate]
			}
		}
		if donor < 0 {
			continue
		}
		out[donor]--
		out[tier]++
	}
	return out
}

// assignInstances turns per-tier counts into concrete instance IDs, keeping as
// much of the previous assignment as possible so that a recomputation does not
// needlessly move servers.
func assignInstances(
	previous map[int]map[string]struct{}, target map[int]int, live []string,
) map[int]map[string]struct{} {

	liveSet := make(map[string]struct{}, len(live))
	for _, id := range live {
		liveSet[id] = struct{}{}
	}

	tiers := make([]int, 0, len(target))
	for tier := range target {
		tiers = append(tiers, tier)
	}
	sort.Ints(tiers)

	out := make(map[int]map[string]struct{}, len(tiers))
	taken := map[string]struct{}{}

	// Keep what we can from the previous assignment.
	for _, tier := range tiers {
		out[tier] = map[string]struct{}{}
		kept := make([]string, 0, target[tier])
		for id := range previous[tier] {
			if _, alive := liveSet[id]; alive {
				if _, used := taken[id]; !used {
					kept = append(kept, id)
				}
			}
		}
		sort.Strings(kept)
		if len(kept) > target[tier] {
			kept = kept[:target[tier]]
		}
		for _, id := range kept {
			out[tier][id] = struct{}{}
			taken[id] = struct{}{}
		}
	}

	// Fill the shortfalls from whatever is left, in a deterministic order.
	free := make([]string, 0, len(live))
	for _, id := range live {
		if _, used := taken[id]; !used {
			free = append(free, id)
		}
	}
	next := 0
	for _, tier := range tiers {
		for len(out[tier]) < target[tier] && next < len(free) {
			out[tier][free[next]] = struct{}{}
			next++
		}
	}
	return out
}

func sameAllocation(a, b map[int]int) bool {
	if len(a) != len(b) {
		return false
	}
	for tier, count := range a {
		if b[tier] != count {
			return false
		}
	}
	return true
}

func sortedTiers[V any](m map[int]V) []int {
	tiers := make([]int, 0, len(m))
	for tier := range m {
		tiers = append(tiers, tier)
	}
	sort.Ints(tiers)
	return tiers
}

func formatAllocation(allocation map[int]int) string {
	parts := make([]string, 0, len(allocation))
	for _, tier := range sortedTiers(allocation) {
		parts = append(parts, fmt.Sprintf("%dms=%d", tier, allocation[tier]))
	}
	return strings.Join(parts, " ")
}

func formatDemand(demand map[int]float64) string {
	parts := make([]string, 0, len(demand))
	for _, tier := range sortedTiers(demand) {
		parts = append(parts, fmt.Sprintf("%dms=%.2f", tier, demand[tier]))
	}
	return strings.Join(parts, " ")
}
