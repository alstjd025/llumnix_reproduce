package policy

import (
	"sort"
	"strconv"
	"sync"
	"time"

	"k8s.io/klog/v2"

	"llumnix/pkg/metrics"
)

// The idle pool of section 4.3, and the two events that move servers between it
// and the SLO tiers.
//
// PolyServe computes no demand. A tier grows by one server when its requests
// start pending and the pool is not empty; it gives one back when the last
// server of the tier runs empty. The quota IS the demand estimate: a queueing
// event is +1 and an empty last server is -1. This file is that state machine.
//
// It replaces, rather than corrects, the demand model in polyserve_repartition.go
// -- server-seconds per request from a profiled batch limit, times the arrival
// rate, divided by largest remainder. That model was this port's own invention
// and it reacts only to arrivals: the same rate of heavier requests, a fleet
// running out of KV, or a prefill queue that no longer fits the budget all leave
// its allocation exactly where it was. The pending signal reacts to all three,
// because a request only pends when a server has actually refused it.
//
// Reassignment costs nothing because a tier label gates only NEW dispatches.
// Requests already running finish where they are, so no KV moves.

const (
	// A server is returned to the pool only after it has been seen empty this
	// many consecutive ticks. Section 4.3 has the scaler "periodically check the
	// requests in the last serving instance"; the streak is what stops a
	// momentary gap between two batches from being read as an idle server.
	poolReleaseStreak = 2
)

// polyserveFleet holds which tier each instance currently serves. Tier 0 means
// the instance is in the idle pool.
type polyserveFleet struct {
	mu sync.Mutex

	tierOf      map[string]int
	emptyStreak map[string]int

	period    time.Duration
	lastTick  time.Time
	seenTiers map[int]struct{}
}

func newPolyserveFleet() *polyserveFleet {
	return &polyserveFleet{
		tierOf:      map[string]int{},
		emptyStreak: map[string]int{},
		period:      repartitionPeriod,
		seenTiers:   map[int]struct{}{},
	}
}

// observe records that a tier exists. A tier the scheduler has never seen holds
// no servers and is not owed one; a tier it has seen is never left with zero
// while another tier holds more than one, because a tier with no servers can
// only pend and would otherwise be starved for the rest of the run.
func (f *polyserveFleet) observe(tier int) {
	if tier <= 0 {
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.seenTiers[tier] = struct{}{}
}

// sync brings the membership table in line with the live instance set: a new
// instance joins the idle pool, an instance that has gone joins nothing.
func (f *polyserveFleet) sync(instances map[string]*instanceViewScheduling) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for id := range instances {
		if _, known := f.tierOf[id]; !known {
			f.tierOf[id] = 0
		}
	}
	for id := range f.tierOf {
		if _, alive := instances[id]; !alive {
			delete(f.tierOf, id)
			delete(f.emptyStreak, id)
		}
	}
}

// serving returns the live instances currently assigned to a tier.
func (f *polyserveFleet) serving(
	tier int, instances map[string]*instanceViewScheduling,
) map[string]*instanceViewScheduling {

	f.mu.Lock()
	defer f.mu.Unlock()
	out := map[string]*instanceViewScheduling{}
	for id, view := range instances {
		if f.tierOf[id] == tier {
			out[id] = view
		}
	}
	return out
}

// tighterThan lists the tiers that hold servers and whose per-token budget is
// stricter than the given one, tightest first. Section 4.4 promotes upward only,
// and taking the tightest first means the guest lands where its own budget is
// furthest from binding on the host.
func (f *polyserveFleet) tighterThan(tier int) []int {
	f.mu.Lock()
	defer f.mu.Unlock()
	held := map[int]struct{}{}
	for _, t := range f.tierOf {
		if t > 0 && t < tier {
			held[t] = struct{}{}
		}
	}
	out := make([]int, 0, len(held))
	for t := range held {
		out = append(out, t)
	}
	sort.Ints(out)
	return out
}

func (f *polyserveFleet) tierOfInstance(id string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.tierOf[id]
}

// claim is the scale-up of section 4.3: the requests of this tier have started
// pending, so take a server for it. Instant, because a server joining a tier
// only has to stop accepting the old tier's work -- nothing is moved.
//
// Two sources, in order. The idle pool is the paper's. The second is the floor
// this port keeps: a tier the scheduler has seen but that holds no servers takes
// one from the largest holder, because with a fleet of four and three tiers the
// pool empties within seconds and a tier that lost the race at start-up would
// otherwise pend every request for the rest of the run. The paper does not need
// this rule because its pool is never the binding resource at its scale.
func (f *polyserveFleet) claim(tier int, instances map[string]*instanceViewScheduling) string {
	if tier <= 0 {
		return ""
	}
	f.mu.Lock()
	defer f.mu.Unlock()

	counts := map[int]int{}
	idle := make([]string, 0, len(instances))
	for id := range instances {
		t := f.tierOf[id]
		counts[t]++
		if t == 0 {
			idle = append(idle, id)
		}
	}
	if len(idle) > 0 {
		sort.Strings(idle)
		f.tierOf[idle[0]] = tier
		delete(f.emptyStreak, idle[0])
		klog.Infof("PolyServe scale-up: instance %s joins tier %dms from the idle pool (%s)",
			idle[0], tier, f.describeLocked(instances))
		metrics.Counter("scheduler_polyserve_scale_total",
			metrics.Labels{{Name: "event", Value: "from_pool"}}).Inc()
		return idle[0]
	}
	if counts[tier] > 0 {
		return ""
	}
	donor, donorCount := 0, 1
	for t, n := range counts {
		if t > 0 && n > donorCount {
			donor, donorCount = t, n
		}
	}
	if donor == 0 {
		return ""
	}
	taken := make([]string, 0, donorCount)
	for id := range instances {
		if f.tierOf[id] == donor {
			taken = append(taken, id)
		}
	}
	sort.Strings(taken)
	f.tierOf[taken[0]] = tier
	delete(f.emptyStreak, taken[0])
	klog.Infof("PolyServe scale-up: instance %s moves from tier %dms to tier %dms, "+
		"which held none (%s)", taken[0], donor, tier, f.describeLocked(instances))
	metrics.Counter("scheduler_polyserve_scale_total",
		metrics.Labels{{Name: "event", Value: "from_tier"}}).Inc()
	return taken[0]
}

// maybeRelease is the scale-down of section 4.3, run on a timer: the last server
// of a tier -- the one the greedy rule leaves emptiest -- goes back to the pool
// once it holds nothing. A tier is never reduced below one server, so a class
// that is quiet for a while does not lose its foothold and then have to pend to
// get it back.
func (f *polyserveFleet) maybeRelease(
	now time.Time, instances map[string]*instanceViewScheduling) {

	f.mu.Lock()
	defer f.mu.Unlock()

	if f.lastTick.IsZero() {
		f.lastTick = now
		return
	}
	if now.Sub(f.lastTick) < f.period {
		return
	}
	f.lastTick = now
	defer f.publishLocked(instances)

	byTier := map[int][]string{}
	for id := range instances {
		byTier[f.tierOf[id]] = append(byTier[f.tierOf[id]], id)
	}
	for tier, ids := range byTier {
		if tier == 0 || len(ids) < 2 {
			continue
		}
		sort.Strings(ids)
		// The last server is the emptiest one, which is what the greedy rule of
		// section 4.3 produces: earlier servers are filled first, so the tail of
		// the ordering carries the marginal load.
		last, lastBatch := "", float64(0)
		for _, id := range ids {
			b := heldRequests(instances[id])
			if last == "" || b < lastBatch {
				last, lastBatch = id, b
			}
		}
		if lastBatch > 0 {
			f.emptyStreak[last] = 0
			continue
		}
		f.emptyStreak[last]++
		if f.emptyStreak[last] < poolReleaseStreak {
			continue
		}
		f.tierOf[last] = 0
		f.emptyStreak[last] = 0
		klog.Infof("PolyServe scale-down: instance %s leaves tier %dms for the idle pool "+
			"after %d consecutive empty checks (%s)",
			last, tier, poolReleaseStreak, f.describeLocked(instances))
		metrics.Counter("scheduler_polyserve_scale_total",
			metrics.Labels{{Name: "event", Value: "to_pool"}}).Inc()
	}
}

// heldRequests is how many requests the instance is carrying, counted from the
// same status fields the decodeBatchSize metric sums. Read straight from the
// status because the release tick runs outside the per-request metric pass.
func heldRequests(view *instanceViewScheduling) float64 {
	if view == nil || view.cmsView == nil || view.cmsView.Status == nil {
		return 0
	}
	_, n := decodeBatchOf(view.cmsView.Status, view.cmsView)
	return n
}

// publishLocked exports the split so a run's allocation history can be read back
// from the metrics scrape. Emitted on every tick, not only on the ticks that
// move a server, because the flat stretches are what show the allocation
// tracking -- or failing to track -- the load.
//
// Called with f.mu held.
func (f *polyserveFleet) publishLocked(instances map[string]*instanceViewScheduling) {
	counts := map[int]int{}
	for id := range instances {
		counts[f.tierOf[id]]++
	}
	for tier := range f.seenTiers {
		metrics.Gauge("scheduler_polyserve_tier_servers",
			metrics.Labels{{Name: "tpot_slo_ms", Value: strconv.Itoa(tier)}}).
			Set(float64(counts[tier]))
	}
	metrics.Gauge("scheduler_polyserve_pool_servers", nil).Set(float64(counts[0]))
	metrics.Gauge("scheduler_polyserve_live_servers", nil).Set(float64(len(instances)))

	// EXP-127. The two sides of the section 4.5 memory test, per instance.
	//
	// WHY. The admission test refuses when projectedKvTokens exceeds the pool,
	// and on the four-instance 70B hour (EXP-109) 90.5% of all refusals cited
	// memory while no engine's observed KV ever passed 64%. Whether that is the
	// test being conservative or the pool genuinely being reached could not be
	// decided from the recorded series: the pool size is not in any of them, and
	// deriving it from instance_cms_all_decodes_tokens_num divided by
	// instance_cms_kv_cache_usage_ratio_projected gave 712k / 791k / 1,329k /
	// 793k across four identical instances, so that derivation is wrong -- the
	// gauge's numerator is not the quantity being divided, and the gap is widest
	// on the instance holding the class with 6,812-token prompts.
	//
	// Publishing the capacity alone would be enough to reconstruct the
	// projection offline from series that are already collected, but the
	// scheduler's own projection is published beside it so that the
	// reconstruction can be checked against the value the decision actually
	// used rather than trusted.
	//
	// Only the CAPACITY is published here. The projection is not: it lives in the
	// per-request scheduling context, and a fleet tick has no such context, so
	// projectedKvTokens returns its "not the polyserve estimate" zero. The probe
	// condition reported the capacity in 185 scrapes and the projection in none,
	// which is what that guard looks like from outside. The projection is
	// published from judge instead, at the refusal.
	for id, view := range instances {
		if capTokens := kvCapacityTokens(view); capTokens > 0 {
			metrics.Gauge("scheduler_polyserve_kv_capacity_tokens",
				metrics.Labels{{Name: "instance", Value: id}}).Set(capTokens)
		}
	}
}

// describeLocked renders the split for a log line. Called with f.mu held.
func (f *polyserveFleet) describeLocked(instances map[string]*instanceViewScheduling) string {
	counts := map[int]int{}
	for id := range instances {
		counts[f.tierOf[id]]++
	}
	tiers := make([]int, 0, len(counts))
	for t := range counts {
		tiers = append(tiers, t)
	}
	sort.Ints(tiers)
	out := ""
	for _, t := range tiers {
		if out != "" {
			out += " "
		}
		if t == 0 {
			out += "pool=" + strconv.Itoa(counts[0])
			continue
		}
		out += strconv.Itoa(t) + "ms=" + strconv.Itoa(counts[t])
	}
	return out
}

// countPlacement records which rung of the ladder placed (or failed to place)
// a request. Without it there is no way to tell a run where admission never
// binds from one where it binds and promotion absorbs everything: both look
// identical in the request-level metrics.
func countPlacement(stage string) {
	metrics.Counter("scheduler_polyserve_placement_total",
		metrics.Labels{{Name: "stage", Value: stage}}).Inc()
}

func countRefusals(stage string, reasons map[string]int) {
	for reason, n := range reasons {
		metrics.Counter("scheduler_polyserve_refused_total",
			metrics.Labels{
				{Name: "stage", Value: stage},
				{Name: "reason", Value: reason},
			}).Add(n)
	}
}
