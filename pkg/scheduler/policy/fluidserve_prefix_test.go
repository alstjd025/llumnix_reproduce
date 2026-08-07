package policy

import "testing"

func seq(from, n int) []int64 {
	out := make([]int64, n)
	for i := range out {
		out[i] = int64(from + i)
	}
	return out
}

// The hash of a block has to depend on everything before it, otherwise the index
// is a set of block identities rather than a prefix index and two prompts that
// merely share a middle section would report a hit they cannot use.
func TestHashPromptIsChained(t *testing.T) {
	block := 4
	a := hashPrompt(seq(0, 16), block)   // 0..15
	b := hashPrompt(seq(100, 16), block) // 100..115

	if len(a) != 4 || len(b) != 4 {
		t.Fatalf("want 4 blocks each, got %d and %d", len(a), len(b))
	}
	// Same tokens in the same place -> same hash.
	if c := hashPrompt(seq(0, 16), block); c[0] != a[0] || c[3] != a[3] {
		t.Fatal("hashing the same prompt twice gave different hashes")
	}
	// A shared prefix must produce equal hashes for the shared part and differ
	// after it. This is the property the whole index rests on.
	long := hashPrompt(append(seq(0, 8), seq(900, 8)...), block)
	if long[0] != a[0] || long[1] != a[1] {
		t.Fatal("prompts sharing their first 8 tokens gave different block hashes")
	}
	if long[2] == a[2] {
		t.Fatal("prompts diverging at token 8 gave the same hash for block 2")
	}
	// The same four tokens at a different offset must NOT collide.
	off := hashPrompt(seq(0, 8), block)
	if off[0] != a[0] {
		t.Fatal("block 0 should not depend on what follows it")
	}
	if len(off) != 2 {
		t.Fatalf("want 2 blocks, got %d", len(off))
	}
}

// A prompt shorter than one block has nothing the engine caches as a unit, and
// the trailing partial block is dropped for the same reason.
func TestHashPromptDropsPartialBlocks(t *testing.T) {
	if h := hashPrompt(seq(0, 3), 4); h != nil {
		t.Fatalf("prompt shorter than a block should hash to nil, got %d", len(h))
	}
	if h := hashPrompt(seq(0, 11), 4); len(h) != 2 {
		t.Fatalf("11 tokens at block 4 should be 2 whole blocks, got %d", len(h))
	}
	if h := hashPrompt(seq(0, 8), 0); h != nil {
		t.Fatal("a non-positive block size should hash to nil rather than divide by zero")
	}
}

// Only a run from the FRONT counts. A prefix cache matches from the first token
// and stops at the first block it does not hold, so a block sitting behind a gap
// cannot be used and must not be counted.
func TestHitTokensCountsOnlyTheLeadingRun(t *testing.T) {
	idx := newPrefixIndex(4, 1000)
	full := hashPrompt(seq(0, 20), 4) // 5 blocks

	idx.note(full, "A")
	if got := idx.hitTokens(full, "A"); got != 20 {
		t.Fatalf("whole prompt on A should be 20 tokens, got %d", got)
	}
	if got := idx.hitTokens(full, "B"); got != 0 {
		t.Fatalf("an instance that was never sent this prompt should be 0, got %d", got)
	}

	// B gets everything except block 2.
	gapped := []uint64{full[0], full[1], full[3], full[4]}
	idx.note(gapped, "B")
	if got := idx.hitTokens(full, "B"); got != 8 {
		t.Fatalf("the run must stop at the gap: want 8 tokens, got %d", got)
	}

	// A shorter prompt that is a prefix of the longer one hits in full.
	short := hashPrompt(seq(0, 8), 4)
	if got := idx.hitTokens(short, "A"); got != 8 {
		t.Fatalf("a prefix of a dispatched prompt should hit in full, got %d", got)
	}
}

func TestHitTokensHandlesEmptyInput(t *testing.T) {
	idx := newPrefixIndex(4, 10)
	if got := idx.hitTokens(nil, "A"); got != 0 {
		t.Fatalf("no hashes should be no hit, got %d", got)
	}
	var nilIdx *prefixIndex
	if got := nilIdx.hitTokens(hashPrompt(seq(0, 8), 4), "A"); got != 0 {
		t.Fatalf("a nil index should report no hit rather than panic, got %d", got)
	}
	nilIdx.note(hashPrompt(seq(0, 8), 4), "A") // must not panic
}

// The index is bounded, and what it drops is the least recently used block. The
// bound is what keeps memory tied to the amount of distinct prompt content in
// flight rather than to the request rate.
func TestPrefixIndexEvictsLeastRecentlyUsed(t *testing.T) {
	idx := newPrefixIndex(4, 4)
	old := hashPrompt(seq(0, 16), 4) // 4 blocks, fills it
	idx.note(old, "A")
	if blocks, _, _ := idx.stats(); blocks != 4 {
		t.Fatalf("want 4 blocks, got %d", blocks)
	}

	// Touch the first two so they are the most recent, then push four more in.
	idx.hitTokens(old[:2], "A")
	fresh := hashPrompt(seq(500, 16), 4)
	idx.note(fresh, "A")

	blocks, _, _ := idx.stats()
	if blocks != 4 {
		t.Fatalf("capacity should hold at 4 blocks, got %d", blocks)
	}
	if got := idx.hitTokens(fresh, "A"); got != 16 {
		t.Fatalf("the newest prompt should still be present in full, got %d", got)
	}
}

// More than 32 instances would alias onto the same bit. Reporting no hit for the
// extras charges them the whole prompt, which is the conservative direction.
func TestPrefixIndexBitExhaustionIsConservative(t *testing.T) {
	idx := newPrefixIndex(4, 1000)
	h := hashPrompt(seq(0, 8), 4)
	for i := 0; i < 40; i++ {
		idx.note(h, string(rune('a'+i%26))+string(rune('0'+i/26)))
	}
	if got := idx.hitTokens(h, "a0"); got != 8 {
		t.Fatalf("the first instance should still hit, got %d", got)
	}
	// The 33rd distinct instance got no bit, so it must report nothing rather
	// than borrowing another instance's.
	if got := idx.hitTokens(h, "g1"); got != 0 {
		t.Fatalf("an instance past the bit limit should report no hit, got %d", got)
	}
}
