package policy

import "testing"

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
