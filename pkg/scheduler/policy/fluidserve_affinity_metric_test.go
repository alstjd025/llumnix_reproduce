package policy

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

// The defect the `count` metric exists to remove: classShare is a ratio, so an
// instance holding one request of the class outscores one holding eighty of
// them among a hundred, and two instances the class dominates score identically
// and are then separated by free space, which prefers the emptier -- spreading
// the class instead of filling one instance.
func candWith(id string, same, total int, room float64) candidate {
	live := make([]liveRequest, 0, total)
	for i := 0; i < total; i++ {
		t := 100
		if i < same {
			t = 50
		}
		live = append(live, liveRequest{tier: t})
	}
	f := &instanceFlux{id: id, live: live}
	return candidate{
		flux: f, feasible: true, room: room,
		share:     classShare(f, 50),
		sameCount: classCount(f, 50),
	}
}

func TestShareMetricPrefersTheNearlyEmptyInstance(t *testing.T) {
	full := candWith("full", 80, 100, 0.20) // 80 of the class, little room
	seed := candWith("seed", 1, 1, 0.99)    // one of the class, nearly empty
	c := []candidate{full, seed}
	sortCandidates(c, 1.0, affinityMetricShare)
	assert.Equal(t, "seed", c[0].flux.id,
		"share saturates: 1/1 beats 80/100, which is the behaviour being replaced")
}

func TestCountMetricFillsTheFullestInstance(t *testing.T) {
	full := candWith("full", 80, 100, 0.20)
	seed := candWith("seed", 1, 1, 0.99)
	c := []candidate{full, seed}
	sortCandidates(c, 1.0, affinityMetricCount)
	assert.Equal(t, "full", c[0].flux.id,
		"count does not saturate, so the instance holding most of the class wins")
}

// Two instances the class dominates score the same under `share` and are then
// separated by free space. Under `count` they are separated before the
// tie-break is reached, which is the whole point.
func TestCountMetricBreaksTheSaturationTie(t *testing.T) {
	a := candWith("a", 40, 40, 0.30) // pure, share 1.0
	b := candWith("b", 10, 10, 0.90) // pure, share 1.0, emptier
	assert.Equal(t, a.share, b.share, "both are pure, so share cannot tell them apart")

	c := []candidate{a, b}
	sortCandidates(c, 1.0, affinityMetricShare)
	assert.Equal(t, "b", c[0].flux.id, "share ties, so the emptier instance wins the tie-break")

	c = []candidate{a, b}
	sortCandidates(c, 1.0, affinityMetricCount)
	assert.Equal(t, "a", c[0].flux.id, "count separates them and fills the fuller one")
}

// The normalisation has to keep the score inside 0..1, because the weighted sum
// with free space is only meaningful between commensurable quantities.
func TestCountMetricStaysCommensurableWithRoom(t *testing.T) {
	a := candWith("a", 40, 40, 0.00)
	b := candWith("b", 10, 10, 1.00)
	c := []candidate{a, b}
	// At w=0 the class must play no part whichever metric is selected.
	sortCandidates(c, 0.0, affinityMetricCount)
	assert.Equal(t, "b", c[0].flux.id, "at weight 0 the ordering is by free space alone")
}

// An instance holding none of the class must never outrank one holding some.
func TestCountMetricRanksEmptyLast(t *testing.T) {
	some := candWith("some", 3, 50, 0.10)
	none := candWith("none", 0, 50, 0.95)
	c := []candidate{none, some}
	sortCandidates(c, 1.0, affinityMetricCount)
	assert.Equal(t, "some", c[0].flux.id, "zero of the class scores zero")
}
