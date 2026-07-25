package policy

import (
	"testing"

	"github.com/stretchr/testify/assert"

	"llumnix/pkg/consts"
	"llumnix/pkg/types"
)

// Filters and selectors are handed an instance view and no request, so a
// per-request SLO can only reach them by being stamped onto the views first.
// These tests pin that contract down: every view sees the budget, and the
// grouped and flat maps stay consistent because they alias the same pointers.
func TestSetRequestSloStampsEveryView(t *testing.T) {
	views := genInstanceViewInternals()
	grouped := map[consts.InferType]map[string]*instanceViewScheduling{
		consts.InferTypeNeutral: views,
	}
	cv := clusterViewScheduling{groupedInstanceViews: grouped, instanceViews: views}

	cv.setRequestSlo(&types.SchedulingRequest{TtftSloMs: 11800, TpotSloMs: 25})

	assert.NotEmpty(t, views)
	for id, v := range views {
		assert.Equal(t, 11800, v.schedulingCtx.requestTtftSloMs, "ttft on %s", id)
		assert.Equal(t, 25, v.schedulingCtx.requestTpotSloMs, "tpot on %s", id)
	}
	// The grouped map must observe the same values; if these ever stop aliasing,
	// a policy reading through groupedInstanceViews would silently see zeros.
	for id, v := range grouped[consts.InferTypeNeutral] {
		assert.Equal(t, 11800, v.schedulingCtx.requestTtftSloMs, "grouped ttft on %s", id)
	}
}

// An unspecified SLO must stay zero rather than inherit whatever the previous
// request left behind; policies read zero as "fall back to the global SLO".
func TestSetRequestSloUnspecifiedIsZero(t *testing.T) {
	views := genInstanceViewInternals()
	cv := clusterViewScheduling{instanceViews: views}

	cv.setRequestSlo(&types.SchedulingRequest{TtftSloMs: 5000, TpotSloMs: 50})
	cv.setRequestSlo(&types.SchedulingRequest{})

	for id, v := range views {
		assert.Zero(t, v.schedulingCtx.requestTtftSloMs, "ttft on %s", id)
		assert.Zero(t, v.schedulingCtx.requestTpotSloMs, "tpot on %s", id)
	}
}

func TestSetRequestSloNilRequest(t *testing.T) {
	views := genInstanceViewInternals()
	cv := clusterViewScheduling{instanceViews: views}
	assert.NotPanics(t, func() { cv.setRequestSlo(nil) })
}

// End-to-end of the wire contract: what the client packs into "priority" is
// what a filter ends up judging against. Guards the gateway decode and the
// scheduler injection agreeing on units (milliseconds) and on which half of the
// packed integer is which.
func TestPackedPriorityReachesInstanceViews(t *testing.T) {
	for _, c := range []struct {
		name     string
		priority int
		ttft     int
		tpot     int
	}{
		{"chat", 5_000_050, 5000, 50},
		{"deepresearch", 10_000_100, 10000, 100},
		{"swe", 11_800_025, 11800, 25},
	} {
		t.Run(c.name, func(t *testing.T) {
			ttft, tpot, ok := types.DecodePackedSlo(c.priority)
			assert.True(t, ok)

			views := genInstanceViewInternals()
			cv := clusterViewScheduling{instanceViews: views}
			cv.setRequestSlo(&types.SchedulingRequest{TtftSloMs: ttft, TpotSloMs: tpot})

			for _, v := range views {
				assert.Equal(t, c.ttft, v.schedulingCtx.requestTtftSloMs)
				assert.Equal(t, c.tpot, v.schedulingCtx.requestTpotSloMs)
			}
		})
	}
}
