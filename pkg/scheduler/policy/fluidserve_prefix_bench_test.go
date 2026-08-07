package policy

// Measured on the three prompt sizes this workload actually sends (chat 673,
// deep research 4,358, agent 6,472 mean input tokens), so that the split
// between what is cached per request and what is redone per candidate can be
// justified by numbers rather than by argument.
//
// 2026-08-08, 72-core host, block size 16:
//
//	                hash        lookup
//	chat            5.2 us      0.42 us
//	deep research  34.1 us      3.0 us
//	agent          50.7 us      4.5 us
//	mix-weighted   13.2 us      1.1 us     (76.9 / 15.4 / 7.7 % of arrivals)
//
// Hashing is twelve times the lookup, which is why the hash is computed once
// per request and cached in the registry while the lookup is redone on every
// candidate of every scheduling call -- where other requests have gone since is
// exactly what changes between two rechecks of a held request.
//
// At 70 req/s with four candidates and, say, three scheduling calls per request
// that is 70 x (13.2 + 3 x 4 x 1.1) = 1.8 ms of CPU per second, which is 0.2%
// of one core. llm-d's equivalent machinery measured 69 us per request in the
// same cluster (EXP-66 EPP plugin durations); ours is cheaper because the
// gateway has already tokenised the prompt.


import "testing"

func mkTokens(n int) []int64 {
	t := make([]int64, n)
	for i := range t {
		t[i] = int64(i * 7919 % 128000)
	}
	return t
}

func benchHash(b *testing.B, n int) {
	t := mkTokens(n)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		hashPrompt(t, 16)
	}
}

func benchLookup(b *testing.B, n int) {
	idx := newPrefixIndex(16, 500000)
	h := hashPrompt(mkTokens(n), 16)
	idx.note(h, "eng-0")
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		idx.hitTokens(h, "eng-0")
	}
}

func BenchmarkHashChat(b *testing.B)   { benchHash(b, 673) }
func BenchmarkHashDR(b *testing.B)     { benchHash(b, 4358) }
func BenchmarkHashSWE(b *testing.B)    { benchHash(b, 6472) }
func BenchmarkLookupChat(b *testing.B) { benchLookup(b, 673) }
func BenchmarkLookupDR(b *testing.B)   { benchLookup(b, 4358) }
func BenchmarkLookupSWE(b *testing.B)  { benchLookup(b, 6472) }
