package policy

import (
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
)

// perInstanceCapacity is testCapacity with the per-instance correction on. The
// map has to be non-nil for the fleet-value fallback to be exercised rather
// than the lazy-init path, which is what production does after one sample.
func perInstanceCapacity(t *testing.T) *capacityModel {
	t.Helper()
	m := testCapacity(t)
	m.perInstance = true
	m.corrections = map[string]float64{}
	return m
}

// The default is the shipped behaviour: with the mode off, an instance id
// changes nothing, so every result before this change is reproduced exactly.
func TestFleetCorrectionIgnoresTheInstanceWhenTheModeIsOff(t *testing.T) {
	m := testCapacity(t)
	base := m.meanStepMs("", 200000, 20, 0, 8192, 100)
	for i := 0; i < 20000; i++ {
		m.noteResidual("engine-a", m.meanStepMs("engine-a", 200000, 20, 0, 8192, 100), base*1.3)
	}
	assert.InDelta(t, 1.3, m.correctionFactor(), 0.05)
	// Every instance sees the fleet factor, including one never mentioned.
	assert.Equal(t, m.correctionFactor(), m.correctionFor("engine-a"))
	assert.Equal(t, m.correctionFactor(), m.correctionFor("engine-b"))
	assert.Equal(t, m.correctionFactor(), m.correctionFor(""))
}

// The defect this change is for: two instances whose errors have opposite signs
// must each converge to their own factor instead of to the average. The numbers
// are the ones EXP-97 section 10 measured -- chat-holding engines run at about
// 1.15 times their prediction, dedicated deep-research engines at about 0.80.
func TestOppositeErrorsConvergeSeparatelyPerInstance(t *testing.T) {
	m := perInstanceCapacity(t)
	// The measurement is a FIXED time, not a multiple of the current prediction.
	// Feeding back a multiple would make the ratio 1.15 forever and drive the
	// factor to its clamp, which is not what a real engine does: as the
	// correction rises the prediction approaches what the engine achieves and
	// the ratio returns to one. A fixed target reproduces that fixed point.
	base := m.meanStepMs("", 200000, 20, 0, 8192, 100)
	pred := func(id string) float64 { return m.meanStepMs(id, 200000, 20, 0, 8192, 100) }
	for i := 0; i < 20000; i++ {
		m.noteResidual("chat", pred("chat"), base*1.15)
		m.noteResidual("deep", pred("deep"), base*0.80)
	}
	// Each instance's factor converges toward its own ratio, so the ratio of the
	// two factors approaches 1.15 / 0.80.
	assert.InDelta(t, 1.15/0.80, m.correctionFor("chat")/m.correctionFor("deep"), 0.20,
		"the two factors should separate by the ratio of the two errors")
	assert.Greater(t, m.correctionFor("chat"), m.correctionFor("deep"),
		"an under-predicted instance must end above an over-predicted one")
	// And the fleet value sits between them, which is exactly the averaging the
	// change is meant to stop being the only thing available.
	fleet := m.correctionFactor()
	assert.Less(t, m.correctionFor("deep"), fleet)
	assert.Greater(t, m.correctionFor("chat"), fleet)
}

// A newly seen instance starts from the fleet value, not from 1.0, so its first
// predictions are not the uncorrected law.
func TestUnseenInstanceStartsFromTheFleetValue(t *testing.T) {
	m := perInstanceCapacity(t)
	base := m.meanStepMs("", 200000, 20, 0, 8192, 100)
	for i := 0; i < 20000; i++ {
		m.noteResidual("a", m.meanStepMs("a", 200000, 20, 0, 8192, 100), base*1.3)
	}
	assert.InDelta(t, m.correctionFactor(), m.correctionFor("fresh"), 1e-9)
	assert.Greater(t, m.correctionFor("fresh"), 1.1,
		"a fresh instance must not fall back to the uncorrected law")
}

// The correction reaches maxKvForAllowance as a divisor on the allowance, so an
// instance corrected upward may hold LESS at the same promised pace. That is the
// coupling between this change and the pace-cap one, and it is asserted here so
// that a later edit which drops the id from that path fails a test rather than
// silently reverting to the fleet factor.
func TestPerInstanceCorrectionReachesTheKvCeiling(t *testing.T) {
	m := perInstanceCapacity(t)
	base := m.meanStepMs("", 200000, 20, 0, 8192, 100)
	// Comparing against an unseen instance would not work: the fleet value is
	// updated on every sample too, so an instance falling back to it moves with
	// the measured one. Two instances measured in opposite directions is the
	// comparison that isolates the per-instance path.
	for i := 0; i < 20000; i++ {
		m.noteResidual("slow", m.meanStepMs("slow", 200000, 20, 0, 8192, 100), base*1.3)
		m.noteResidual("fast", m.meanStepMs("fast", 200000, 20, 0, 8192, 100), base*0.8)
	}
	slow := m.maxKvForAllowance("slow", 60, 20, 0, 8192, 100)
	fast := m.maxKvForAllowance("fast", 60, 20, 0, 8192, 100)
	assert.Less(t, slow, fast,
		"an instance measured slower than predicted must be allowed less KV at the same allowance")
}

// The pace cap enters the memory predicate only behind its flag, and an instance
// with no live request has an infinite allowance, so the minimum falls back to
// the physical cap in both modes.
func TestPaceCapIsInertOnAnUnconstrainedInstance(t *testing.T) {
	m := testCapacity(t)
	capKv := m.maxKvForAllowance("", math.Inf(1), 20, 0, 8192, 100)
	assert.True(t, math.IsInf(capKv, 1), "no live request means no latency constraint")
	capMem := 800000.0
	assert.Equal(t, capMem, math.Min(capKv, capMem))
}

// And where the pace ceiling IS finite and below the physical one, the minimum
// is the pace ceiling -- which is the whole point of the flag.
func TestPaceCapBindsBelowThePhysicalCap(t *testing.T) {
	m := testCapacity(t)
	// 50 ms is chat's budget and 800k tokens is roughly the physical pool on
	// this fleet. The queued prefill matters: it is what pushes the pace ceiling
	// below the physical one, and with an empty queue the ceiling is above it,
	// which is the regime where this flag changes nothing.
	capKv := m.maxKvForAllowance("", 50*fsAllowanceUtilisation, 190, 4*8192, 8192, 100)
	capMem := 800000.0
	assert.Less(t, capKv, capMem)
	assert.Equal(t, capKv, math.Min(capKv, capMem))
}

// The cost of splitting the coefficient, asserted so it cannot be forgotten:
// each instance now sees only its own samples, so a fleet that used to reach a
// given factor in N samples needs about N per instance. The filter is
// deliberately slow -- the comment on fsCorrectionAlpha explains why -- and at
// one status pull per instance per second on this deployment that is minutes,
// not seconds. A run shorter than that measures a policy still converging.
func TestPerInstanceConvergenceNeedsItsOwnSamples(t *testing.T) {
	m := perInstanceCapacity(t)
	base := m.meanStepMs("", 200000, 20, 0, 8192, 100)
	// Measured here rather than asserted from the constant: 500 samples is not
	// enough and 2,000 nearly is. On this deployment a status pull arrives about
	// twice a second per instance, so 2,000 samples is roughly a quarter of an
	// hour -- which is why the segment this change is aimed at, minutes 46 to 61
	// of the hour trace, is well past convergence while the first quarter of a
	// run is not.
	for i := 0; i < 500; i++ {
		m.noteResidual("a", m.meanStepMs("a", 200000, 20, 0, 8192, 100), base*1.3)
	}
	early := m.correctionFor("a")
	assert.Greater(t, early, 1.0, "it has started moving")
	assert.Less(t, early, 1.25, "500 samples is not enough")
	for i := 0; i < 1500; i++ {
		m.noteResidual("a", m.meanStepMs("a", 200000, 20, 0, 8192, 100), base*1.3)
	}
	assert.InDelta(t, 1.3, m.correctionFor("a"), 0.05, "2,000 nearly is")
}
