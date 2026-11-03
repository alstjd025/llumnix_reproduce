package policy

import (
	"sync/atomic"
	"time"
)

// Read-only view of the FluidServe registry for diagnosing records that stay
// after their request has ended (EXP-145). Nothing on the decision path reads
// it; it takes the registry lock once per call.

var activeRegistry atomic.Pointer[requestRegistry]

// RegistrySnapshot is what /debug/fluidserve-registry returns.
type RegistrySnapshot struct {
	TimeMs    int64                            `json:"t_ms"`
	Instances map[string][]RegistrySnapshotRec `json:"instances"`
	Completed map[string]int64                 `json:"completed"`
}

// RegistrySnapshotRec is one registry record.
type RegistrySnapshotRec struct {
	ID           string `json:"id"`
	DispatchedMs int64  `json:"dispatched_ms"`
	StepAt       int64  `json:"step_at_dispatch"`
	Waiting      bool   `json:"waiting"`
}

// FluidserveRegistrySnapshot returns nil when no FluidServe policy is running.
func FluidserveRegistrySnapshot() *RegistrySnapshot {
	r := activeRegistry.Load()
	if r == nil {
		return nil
	}
	snap := &RegistrySnapshot{TimeMs: time.Now().UnixMilli(), Instances: map[string][]RegistrySnapshotRec{},
		Completed: Residency().CompletedIDs()}
	r.mu.Lock()
	for inst, recs := range r.byInstance {
		list := make([]RegistrySnapshotRec, 0, len(recs))
		for id, rec := range recs {
			list = append(list, RegistrySnapshotRec{ID: id, DispatchedMs: rec.dispatchedMs,
				StepAt: rec.stepAtDispatch, Waiting: rec.waitingNow})
		}
		snap.Instances[inst] = list
	}
	r.mu.Unlock()
	return snap
}
