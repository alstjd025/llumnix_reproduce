package policy

import (
	"math"
	"sync"
	"time"

	"k8s.io/klog/v2"

	"llumnix/pkg/metrics"
)

// residencyTracker is the exact record of which requests are on which
// instance: a request enters when the scheduler places it and leaves when the
// gateway reports that its response has finished (or failed). It replaces
// inference about completion with an observation, the way llm-d's endpoint
// picker and Llumnix's own lite-mode request-state tracking do: both sit on the
// request path, see the response end, and need nothing from the engine.
//
// Why this exists (2026-10-19). In full-mode scheduling the engine reports only
// aggregate counts, so FluidServe's registry inferred departures: a record was
// retired when the request's class length distribution said it had probably
// finished, or -- for 96% of retirements on a 35 req/s run -- when the registry
// held more records than the engine's running count and the EARLIEST-DISPATCHED
// ones were dropped. That keeps the count right and the membership wrong: a
// later short chat request that has finished stays, an earlier long deep-
// research request that is still decoding goes, and the instance's tightest
// budget is computed from the wrong set.
//
// The engine is not modified. The gateway already proxies every response and
// already calls the scheduler (/schedule, /release); this adds one call,
// /complete, sent when --notify-completion is set on the gateway.
//
// Semantics, decided 2026-10-19: resident from the moment of placement until
// the completion notice. A request queued inside the engine is resident, since
// it is load the instance has accepted. Completion is remembered for a while
// (a tombstone), so a notice that overtakes the placement record -- possible
// because /complete and /schedule are served concurrently -- still wins.
type residencyTracker struct {
	mu         sync.Mutex
	byInstance map[string]map[string]residentRecord
	instanceOf map[string]string
	// completed holds the time each completion notice arrived, for
	// residencyTombstoneMs. A placement that arrives for an id in here is ignored.
	completed map[string]int64

	lastSweepMs int64
	expiredAge  int64
}

type residentRecord struct {
	tpotSloMs  int
	placedAtMs int64
}

const (
	// Backstop for a completion notice that never arrives (gateway restart,
	// network loss). Long enough that no request in these workloads outlives it.
	residencyMaxAgeMs = 20 * 60 * 1000
	// How long a completion is remembered. It only has to outlast the window in
	// which a placement record for the same id can still be in flight.
	residencyTombstoneMs  = 5 * 60 * 1000
	residencySweepEveryMs = 10 * 1000
)

var globalResidency = &residencyTracker{
	byInstance: map[string]map[string]residentRecord{},
	instanceOf: map[string]string{},
	completed:  map[string]int64{},
}

// Residency is the process-wide tracker. Placement is recorded by the dispatch
// path and completion by the scheduler service's /complete handler, which do
// not share any other object.
func Residency() *residencyTracker { return globalResidency }

// Place records that requestID now occupies instanceID. A request rescheduled
// by the gateway's retry path moves to the new instance.
func (t *residencyTracker) Place(requestID, instanceID string, tpotSloMs int) {
	if requestID == "" || instanceID == "" {
		return
	}
	now := time.Now().UnixMilli()
	t.mu.Lock()
	defer t.mu.Unlock()
	t.sweepLocked(now)
	if _, done := t.completed[requestID]; done {
		return
	}
	if prev, ok := t.instanceOf[requestID]; ok && prev != instanceID {
		delete(t.byInstance[prev], requestID)
	}
	m := t.byInstance[instanceID]
	if m == nil {
		m = map[string]residentRecord{}
		t.byInstance[instanceID] = m
	}
	m[requestID] = residentRecord{tpotSloMs: tpotSloMs, placedAtMs: now}
	t.instanceOf[requestID] = instanceID
}

// Complete records that requestID's response has ended.
func (t *residencyTracker) Complete(requestID string) {
	if requestID == "" {
		return
	}
	now := time.Now().UnixMilli()
	t.mu.Lock()
	defer t.mu.Unlock()
	if inst, ok := t.instanceOf[requestID]; ok {
		delete(t.byInstance[inst], requestID)
		delete(t.instanceOf, requestID)
	}
	t.completed[requestID] = now
	t.sweepLocked(now)
}

// Completed reports whether a completion notice has been received for
// requestID (within the tombstone window).
func (t *residencyTracker) Completed(requestID string) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	_, ok := t.completed[requestID]
	return ok
}

// MinTpotSloMs is the tightest per-token budget among the requests resident on
// instanceID, in milliseconds, or 0 when none is resident or none carries one.
func (t *residencyTracker) MinTpotSloMs(instanceID string) int {
	t.mu.Lock()
	defer t.mu.Unlock()
	best := math.MaxInt32
	for _, r := range t.byInstance[instanceID] {
		if r.tpotSloMs > 0 && r.tpotSloMs < best {
			best = r.tpotSloMs
		}
	}
	if best == math.MaxInt32 {
		return 0
	}
	return best
}

// Count is the number of requests resident on instanceID.
func (t *residencyTracker) Count(instanceID string) int {
	t.mu.Lock()
	defer t.mu.Unlock()
	return len(t.byInstance[instanceID])
}

// sweepLocked drops placements older than residencyMaxAgeMs and tombstones
// older than residencyTombstoneMs, at most once per residencySweepEveryMs.
func (t *residencyTracker) sweepLocked(now int64) {
	if now-t.lastSweepMs < residencySweepEveryMs {
		return
	}
	t.lastSweepMs = now
	for inst, m := range t.byInstance {
		for id, r := range m {
			if now-r.placedAtMs > residencyMaxAgeMs {
				delete(m, id)
				delete(t.instanceOf, id)
				t.expiredAge++
				klog.Warningf("residency: request %s on %s expired without a completion notice", id, inst)
			}
		}
		metrics.Gauge("scheduler_residency_requests", metrics.Labels{{Name: "instance", Value: inst}}).
			Set(float64(len(m)))
	}
	for id, at := range t.completed {
		if now-at > residencyTombstoneMs {
			delete(t.completed, id)
		}
	}
	metrics.Gauge("scheduler_residency_expired_total", metrics.Labels{}).Set(float64(t.expiredAge))
}

// reset empties the tracker. Tests only.
func (t *residencyTracker) reset() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.byInstance = map[string]map[string]residentRecord{}
	t.instanceOf = map[string]string{}
	t.completed = map[string]int64{}
	t.lastSweepMs = 0
	t.expiredAge = 0
}
