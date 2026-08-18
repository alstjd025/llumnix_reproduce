package policy

import (
	"math"
	"testing"
)

// The deadline conjunct added in EXP-87. Four things are asserted, and the first
// is the one that matters: with the flag off the predicate must be bit-for-bit
// what it was, because every measurement taken before EXP-87 is the control.

func ttftReq(waitedMs, ttftSloMs float64) *fluidserveRequest {
	return &fluidserveRequest{
		arrivedMs: 0, nowMs: int64(waitedMs), ttftSloMs: ttftSloMs, isE2E: false,
		nominalMs: 100,
	}
}

func cand(prefillMs, meanAfter float64) candidate {
	return candidate{prefillMs: prefillMs, meanAfter: meanAfter}
}

// The situation 28_backlog_at_placement.md measured: a deepresearch request held
// 3,300 ms, routed onto an instance carrying ~88,000 tokens of queued prefill so
// its modelled time to a first token is ~11 s, against a 10 s budget.
func TestMissesTtftDeadline_TheMeasuredCase(t *testing.T) {
	p := &fluidserveDispatchPolicy{}
	if !p.missesTtftDeadline(ttftReq(3300, 10000), cand(11000, 68)) {
		t.Fatal("3,300 ms waited + 11,000 ms to first token is inside a 10,000 ms budget?")
	}
}

// chat: held a few milliseconds, half a second of prefill, 5 s budget. The queue
// on the instances chat lives on measured a median of 0 tokens, so this must not
// fire -- if it did, the change would touch 76.9% of the arrivals.
func TestMissesTtftDeadline_ChatIsUntouched(t *testing.T) {
	p := &fluidserveDispatchPolicy{}
	if p.missesTtftDeadline(ttftReq(6, 5000), cand(500, 46)) {
		t.Fatal("chat fired the deadline test; it should have 4.5 s of room")
	}
}

// swe is judged end to end and is the unchanged control in EXP-87.
func TestMissesTtftDeadline_E2EClassIsExcluded(t *testing.T) {
	p := &fluidserveDispatchPolicy{}
	r := ttftReq(20000, 0)
	r.isE2E = true
	r.budgetMs = 30000
	if p.missesTtftDeadline(r, cand(11000, 68)) {
		t.Fatal("the end-to-end class must not be judged by the first-token form")
	}
}

// An unpredictable prefill is a refusal, the same direction the rest of the
// predicate takes for an unpredictable step time.
func TestMissesTtftDeadline_InfinitePrefill(t *testing.T) {
	p := &fluidserveDispatchPolicy{}
	if !p.missesTtftDeadline(ttftReq(0, 10000), cand(math.Inf(1), 68)) {
		t.Fatal("an unpredictable prefill should not be treated as feasible")
	}
}

// The refactor must not have changed missesOwnBudget: it now calls
// missesTtftDeadline for its first-token branch, and the answer has to be what
// the inlined version gave over the whole grid the two branches divide.
func TestMissesOwnBudget_UnchangedByTheRefactor(t *testing.T) {
	p := &fluidserveDispatchPolicy{}
	for _, waited := range []float64{0, 500, 3300, 9000, 20000} {
		for _, prefill := range []float64{100, 500, 6000, 11000} {
			for _, mean := range []float64{40, 68, 120} {
				for _, ttft := range []float64{5000, 10000} {
					r := ttftReq(waited, ttft)
					r.nominalMs = 100
					c := cand(prefill, mean)
					want := waited+prefill > ttft || mean > 100 // the inlined original
					if got := p.missesOwnBudget(r, c); got != want {
						t.Fatalf("waited=%v prefill=%v mean=%v ttft=%v: got %v want %v",
							waited, prefill, mean, ttft, got, want)
					}
				}
			}
		}
	}
}
