package policy

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"llumnix/pkg/cms"
	"llumnix/pkg/consts"
)

func TestResidencyPlaceCompleteAndTightest(t *testing.T) {
	r := Residency()
	r.reset()
	defer r.reset()

	assert.Equal(t, 0, r.MinTpotSloMs("i1"), "an empty instance has no resident budget")
	r.Place("a", "i1", 100)
	r.Place("b", "i1", 50)
	r.Place("c", "i2", 75)
	assert.Equal(t, 50, r.MinTpotSloMs("i1"))
	assert.Equal(t, 75, r.MinTpotSloMs("i2"))

	// The 50 ms request finishes; the instance is judged by what is left.
	r.Complete("b")
	assert.Equal(t, 100, r.MinTpotSloMs("i1"))
	assert.True(t, r.Completed("b"))
	assert.False(t, r.Completed("a"))

	// A retried request moves: it is no longer on its old instance.
	r.Place("c", "i1", 75)
	assert.Equal(t, 0, r.MinTpotSloMs("i2"))
	assert.Equal(t, 75, r.MinTpotSloMs("i1"))
}

func TestResidencyCompletionThatOvertakesThePlacementWins(t *testing.T) {
	// /complete and /schedule are served concurrently, so the notice can land
	// before the placement is recorded. The request must not come back as
	// resident afterwards.
	r := Residency()
	r.reset()
	defer r.reset()
	r.Complete("x")
	r.Place("x", "i1", 50)
	assert.Equal(t, 0, r.Count("i1"))
	assert.Equal(t, 0, r.MinTpotSloMs("i1"))
}

func TestSloIncumbentGateJudgesAgainstTheTightestResident(t *testing.T) {
	r := Residency()
	r.reset()
	defer r.reset()

	view := func(id string, predicted float32, reqTpotMs int) *instanceViewScheduling {
		cv := &cms.InstanceView{Metadata: &cms.InstanceMetadata{InstanceId: id}}
		return &instanceViewScheduling{
			cmsView:               cv,
			InstanceViewInterface: cv,
			schedulingCtx: schedulingCtx{
				metrics: map[string]instanceSchedulingMetric{
					consts.SchedulingMetricPredictedTpot: &staticMetric{value: predicted},
				},
				requestTpotSloMs: reqTpotMs,
			},
		}
	}
	own := &metricBasedFilter{
		metricName:      consts.SchedulingMetricPredictedTpot,
		perRequestSloMs: sloBudgetTpot,
		sloMultiplier:   0.9,
		threshold:       50 * 0.9,
	}
	inc := &metricBasedFilter{
		metricName:      consts.SchedulingMetricPredictedTpot,
		perRequestSloMs: sloBudgetTpot,
		sloMultiplier:   0.9,
		threshold:       50 * 0.9,
		incumbentTpot:   true,
	}
	r.Place("chat", "busy", 50)

	// A 100 ms request on an instance predicted at 60 ms, where a 50 ms request
	// is resident: by its own budget it fits (60 < 90); by the resident's it
	// does not (60 >= 45).
	assert.False(t, own.instanceFilteredOut(view("busy", 60, 100)))
	assert.True(t, inc.instanceFilteredOut(view("busy", 60, 100)))

	// On an empty instance the incumbent gate is the request's own budget.
	assert.False(t, inc.instanceFilteredOut(view("empty", 60, 100)))
	assert.True(t, inc.instanceFilteredOut(view("empty", 60, 50)))

	// A resident looser than the request never loosens the request's own budget.
	r.Place("dr", "loose", 100)
	assert.True(t, inc.instanceFilteredOut(view("loose", 46, 50)))

	// Once the 50 ms resident completes, the busy instance is judged by the
	// request's own budget again.
	r.Complete("chat")
	assert.False(t, inc.instanceFilteredOut(view("busy", 60, 100)))
}

func TestExactResidencyRetiresOnlyOnCompletion(t *testing.T) {
	res := Residency()
	res.reset()
	defer res.reset()

	r := testRegistry(t)
	r.exactResidency = true
	now := int64(1_000_000)
	// Two requests dispatched in order; the engine then reports one running.
	r.onDispatch("e0", "early", 50, 1000, 8192, 1000, now, 0, 0, 0)
	r.onDispatch("e0", "late", 50, 1000, 8192, 1100, now, 0, 0, 0)

	// The inferred rule would drop the EARLIEST-dispatched one to match the
	// count. Under exact residency nothing leaves without a completion notice.
	live := r.reconcile("e0", 1, 1300, now+5000)
	assert.Len(t, live, 2)

	// The LATER one is the one that actually finished.
	res.Complete("late")
	live = r.reconcile("e0", 1, 1310, now+5500)
	assert.Len(t, live, 1)
	assert.Equal(t, "early", live[0].id)
	assert.Equal(t, int64(1), r.completions())

	// With the flag off the same sequence drops the earliest-dispatched record:
	// the count is right, the membership is wrong.
	res.reset()
	old := testRegistry(t)
	old.onDispatch("e0", "early", 50, 1000, 8192, 1000, now, 0, 0, 0)
	old.onDispatch("e0", "late", 50, 1000, 8192, 1100, now, 0, 0, 0)
	live = old.reconcile("e0", 1, 1300, now+5000)
	assert.Len(t, live, 1)
	assert.Equal(t, "late", live[0].id)
}

func TestExactProgressFreezesQueuedRequests(t *testing.T) {
	res := Residency()
	res.reset()
	defer res.reset()

	r := testRegistry(t)
	r.exactResidency = true
	r.exactProgress = true
	now := int64(1_000_000)
	// One prefill chunk; dispatched at engine step 1000.
	r.onDispatch("e0", "q", 50, 1000, 8192, 1000, now, 0, 0, 0)

	// The engine reports it waiting (with its id decorated the way the engine
	// decorates ids) for 200 steps: no progress, and it is marked waiting.
	r.noteWaiting("e0", []string{"cmpl-q-0"})
	live := r.reconcile("e0", 0, 1100, now+1000)
	require.Len(t, live, 1)
	assert.True(t, live[0].waiting)
	assert.Equal(t, 0, live[0].j)
	live = r.reconcile("e0", 0, 1200, now+2000)
	assert.Equal(t, 0, live[0].j)

	// It leaves the queue: progress counts only the steps since.
	r.noteWaiting("e0", nil)
	live = r.reconcile("e0", 1, 1300, now+3000)
	assert.False(t, live[0].waiting)
	assert.Equal(t, 1300-1000-1-200, live[0].j)

	// Without the flag the same history credits every step since dispatch.
	old := testRegistry(t)
	old.exactResidency = true
	old.onDispatch("e0", "q", 50, 1000, 8192, 1000, now, 0, 0, 0)
	old.noteWaiting("e0", []string{"cmpl-q"})
	live = old.reconcile("e0", 0, 1200, now+2000)
	assert.Equal(t, 1200-1000-1, live[0].j)
	assert.False(t, live[0].waiting)
}

func TestNoteWaitingMatchesVLLMCompletionIDs(t *testing.T) {
	r := testRegistry(t)
	r.exactProgress = true
	// vLLM 0.12 /v1/completions reports a single-prompt request as "cmpl-<id>-0".
	// A UUID whose last group is all digits must still match as itself.
	r.noteWaiting("e0", []string{"cmpl-4b418342-8d56-43b6-9f4a-a9b4c40f1705-0", "cmpl-4b418342-8d56-43b6-9f4a-123456789012"})
	_, a := r.waitingNow["e0"]["4b418342-8d56-43b6-9f4a-a9b4c40f1705"]
	_, b := r.waitingNow["e0"]["4b418342-8d56-43b6-9f4a-123456789012"]
	assert.True(t, a)
	assert.True(t, b)
}

func TestTrimEngineRequestID(t *testing.T) {
	assert.Equal(t, "abc", trimEngineRequestID("cmpl-abc"))
	assert.Equal(t, "abc", trimEngineRequestID("chatcmpl-abc+3"))
	assert.Equal(t, "abc", trimEngineRequestID("abc"))
}
