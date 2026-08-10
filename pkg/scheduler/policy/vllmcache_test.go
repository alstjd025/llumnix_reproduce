package policy

import (
	"testing"

	"llumnix/pkg/cms"
	"llumnix/pkg/consts"
	"llumnix/pkg/types"
)

// The tests below check the three branches the cache_aware algorithm can take
// and the one property the tree has to have. They exist because the port was
// written from another project's source, and the failure mode for that is a
// policy that runs, produces plausible routing, and is not the algorithm being
// compared against -- which no end-to-end measurement would catch.

func TestTokenTriePrefixNotSetIntersection(t *testing.T) {
	tr := newTokenTrie()
	tr.insert([]uint32{1, 2, 3, 4})
	// Shares the first two tokens, then diverges. A set-intersection match would
	// report 3 here by counting the 4; a prefix match stops at the divergence.
	if got := tr.matchLen([]uint32{1, 2, 9, 4}); got != 2 {
		t.Fatalf("prefix match = %d, want 2", got)
	}
	if got := tr.matchLen([]uint32{5, 1, 2}); got != 0 {
		t.Fatalf("match on a different first token = %d, want 0", got)
	}
	if got := tr.matchLen([]uint32{1, 2, 3, 4, 5}); got != 4 {
		t.Fatalf("match longer than stored = %d, want 4", got)
	}
}

func TestTokenTrieNodeCountAndEviction(t *testing.T) {
	tr := newTokenTrie()
	tr.insert([]uint32{1, 2, 3})
	if tr.nodes != 4 { // root + three
		t.Fatalf("nodes = %d, want 4", tr.nodes)
	}
	// A second prompt sharing a prefix must add only its divergent tail, which
	// is the whole point of a tree rather than a list of prompts.
	tr.insert([]uint32{1, 2, 9})
	if tr.nodes != 5 {
		t.Fatalf("nodes after shared-prefix insert = %d, want 5", tr.nodes)
	}
	tr.evictLRU(3)
	if tr.nodes > 3 {
		t.Fatalf("nodes after eviction = %d, want <= 3", tr.nodes)
	}
}

func TestVllmCacheImbalanceTestNeedsBothConditions(t *testing.T) {
	cfg := vllmCacheConfig{balanceAbs: 64, balanceRel: 1.5}
	imbalanced := func(maxLoad, minLoad int32) bool {
		return (maxLoad-minLoad) > cfg.balanceAbs &&
			float64(maxLoad) > cfg.balanceRel*float64(minLoad)
	}
	// Absolute gap large, ratio small: NOT imbalanced. This is the case that a
	// port written from the one-line README description gets wrong, because that
	// line reads as if either condition were enough.
	// 300 vs 220: gap 80 clears the absolute test, but 300 < 1.5*220 = 330, so
	// the ratio test fails and the fleet is balanced.
	if imbalanced(300, 220) {
		t.Fatal("300 vs 220 must not be imbalanced: 300 < 1.5*220")
	}
	if !imbalanced(200, 100) {
		t.Fatal("200 vs 100 should be imbalanced: gap 100 > 64 and ratio 2.0 > 1.5")
	}
	// Ratio large, absolute gap small: NOT imbalanced. Without the absolute
	// test, an idle fleet with 3 and 1 requests would be declared imbalanced and
	// the prefix tree would never be used at low load.
	if imbalanced(3, 1) {
		t.Fatal("3 vs 1 must not be imbalanced: absolute gap 2 is below 64")
	}
	if imbalanced(0, 0) {
		t.Fatal("an empty fleet must not be imbalanced")
	}
}

func TestVllmCacheThresholdIsStrictlyGreater(t *testing.T) {
	// The source says "if match rate > cache_threshold" route to the match, and
	// "if match rate <= cache_threshold" route to the smallest tree. A port using
	// >= would send a request whose match rate is exactly the threshold down the
	// other branch, which matters most at threshold 0.0.
	cfg := vllmCacheConfig{cacheThreshold: 0.3}
	byPrefix := func(match, total int) bool {
		if total == 0 {
			return false
		}
		return float64(match)/float64(total) > cfg.cacheThreshold
	}
	if byPrefix(3, 10) {
		t.Fatal("rate exactly 0.3 must NOT take the prefix branch")
	}
	if !byPrefix(4, 10) {
		t.Fatal("rate 0.4 must take the prefix branch")
	}
	if byPrefix(0, 0) {
		t.Fatal("an empty prompt must not take the prefix branch")
	}
}

// vllmCacheView builds an instance view whose engine-reported queue depth is
// fixed, so that the only thing that can move the load between two decisions is
// the dispatch accounting.
func vllmCacheView(id string, waiting, running int32) *instanceViewScheduling {
	cmsView := &cms.InstanceView{
		Instance: &types.LLMInstance{InferType: consts.InferTypeNeutral},
		Status: &cms.InstanceStatus{
			InstanceId:         id,
			NumWaitingRequests: waiting,
			NumRunningRequests: running,
		},
		Metadata: &cms.InstanceMetadata{InstanceId: id},
	}
	return &instanceViewScheduling{cmsView: cmsView, InstanceViewInterface: cmsView}
}

// vllmCacheDispatch does to the view what the scheduler does after a selection:
// scheduling_policy.go calls cmsClient.AddRequestLocalAccount, which increments
// exactly this field. That binding is asserted in
// pkg/cms/instance_status_local_account_test.go rather than here, because this
// package cannot reach the unexported editor that owns the counter.
func vllmCacheDispatch(v *instanceViewScheduling) {
	v.cmsView.NumInflightDispatchRequests++
}

func vllmCacheTestPolicy(cfg vllmCacheConfig) *vllmCacheDispatchPolicy {
	return &vllmCacheDispatchPolicy{cfg: cfg, trees: map[string]*tokenTrie{}}
}

func vllmCacheDecide(
	s *vllmCacheSelector, views map[string]*instanceViewScheduling,
	tokens []uint32) *instanceViewScheduling {
	req := &vllmCacheRequest{id: "r", tokens: tokens}
	for _, v := range views {
		v.schedulingCtx.vllmCacheRequest = req
	}
	return s.selectInstance(views, false)
}

func TestVllmCacheLoadCountsDispatchesSinceTheLastPoll(t *testing.T) {
	// The property the port was missing: with the engine's report held fixed --
	// which is what happens between two CMS polls, 500 ms apart in this
	// deployment -- a second decision must see the first one's dispatch.
	v := vllmCacheView("a", 2, 3)
	if got := vllmCacheLoad(v); got != 5 {
		t.Fatalf("load before any dispatch = %d, want 5 (2 waiting + 3 running)", got)
	}
	vllmCacheDispatch(v)
	if got := vllmCacheLoad(v); got != 6 {
		t.Fatalf("load after one dispatch = %d, want 6; a decision taken between "+
			"two polls is not seeing the dispatch the previous decision made", got)
	}
	vllmCacheDispatch(v)
	if got := vllmCacheLoad(v); got != 7 {
		t.Fatalf("load after two dispatches = %d, want 7", got)
	}
	// And the release: the engine's next report names the request, the addend
	// drops, and the depth it reports covers it instead. The load must not
	// double-count across that handover.
	v.cmsView.NumInflightDispatchRequests -= 2
	v.cmsView.Status.NumWaitingRequests += 2
	if got := vllmCacheLoad(v); got != 7 {
		t.Fatalf("load after the poll absorbed both dispatches = %d, want 7", got)
	}
}

func TestVllmCacheShortestQueueDoesNotPileOntoAStaleMinimum(t *testing.T) {
	// Two instances, one idle and one holding five requests, with the imbalance
	// test set to fire on any gap so that every decision below takes the
	// shortest-queue branch until the fleet is actually level.
	//
	// Reading the poll alone, instance a stays at zero for the whole 500 ms
	// between polls and every arrival in that window goes to it. Counting the
	// dispatches, a fills up and the branch stops firing once the gap closes:
	// four decisions, not five, and the fifth goes elsewhere.
	p := vllmCacheTestPolicy(vllmCacheConfig{
		cacheThreshold: 0.3, balanceAbs: 1, balanceRel: 1.0,
		evictionSecs: 3600, maxTreeSize: 1 << 20,
	})
	s := &vllmCacheSelector{policy: p}
	a := vllmCacheView("a", 0, 0)
	b := vllmCacheView("b", 0, 5)
	views := map[string]*instanceViewScheduling{"a": a, "b": b}

	prompt := []uint32{10, 11, 12, 13}
	for i := 0; i < 4; i++ {
		got := vllmCacheDecide(s, views, prompt)
		if got.GetInstanceId() != "a" {
			t.Fatalf("decision %d went to %s, want a: it is still the shorter queue "+
				"at %d against %d", i+1, got.GetInstanceId(),
				vllmCacheLoad(a), vllmCacheLoad(b))
		}
		vllmCacheDispatch(got)
	}
	if got := vllmCacheLoad(a); got != 4 {
		t.Fatalf("instance a load after four dispatches = %d, want 4", got)
	}
	// Gap is now 1, which does not clear balanceAbs, so the fleet reads balanced
	// and the decision falls to the tree. A prompt sharing nothing with the four
	// already sent to a takes the smallest-tree branch, and b's tree is empty.
	got := vllmCacheDecide(s, views, []uint32{90, 91, 92, 93})
	if got.GetInstanceId() != "b" {
		t.Fatalf("fifth decision went to %s, want b: reading the poll alone is the "+
			"only way a still looks like the emptiest instance", got.GetInstanceId())
	}
}
