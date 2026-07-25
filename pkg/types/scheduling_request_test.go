package types

import "testing"

// The OpenAI API gives one integer channel ("priority") and two different
// encodings compete for it across our experiment arms, so the decoder has to
// accept the packed-SLO one and reject the rest rather than return garbage.
func TestDecodePackedSlo(t *testing.T) {
	cases := []struct {
		name     string
		priority int
		ttft     int
		tpot     int
		ok       bool
	}{
		// The three tiers actually used in the mix workload, packed by the
		// client as ttft_slo_ms*1000 + tpot_slo_ms.
		{"chat 5s/50ms", 5_000_050, 5000, 50, true},
		{"deepresearch 10s/100ms", 10_000_100, 10000, 100, true},
		{"swe 11.8s/25ms", 11_800_025, 11800, 25, true},

		// Client sent a TTFT budget but no per-token budget.
		{"tpot unset", 5_000_000, 5000, 0, true},

		// The EDF arm puts an absolute epoch deadline in the same field
		// (arrival_ms + slo_ms). Decoding it would claim a ~20-day TTFT budget,
		// which must be rejected so the policy falls back to its global SLO.
		{"edf absolute deadline", 1_784_980_000_000, 0, 0, false},

		// A best-effort tier (30 days) is likewise out of range, and falling
		// back to the global SLO is the right reading of "no special treatment".
		{"best-effort 30d", 30 * 24 * 3600 * 1000 * 1000, 0, 0, false},

		{"zero", 0, 0, 0, false},
		{"negative", -1, 0, 0, false},
		// Below the radix there is no TTFT budget at all, only a stray remainder.
		{"sub-radix", 999, 0, 0, false},

		// Boundary: exactly one hour is still plausible, one millisecond past
		// the radix beyond it is not.
		{"1h boundary", 3_600_000 * 1000, 3_600_000, 0, true},
		{"just over 1h", 3_600_001 * 1000, 0, 0, false},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ttft, tpot, ok := DecodePackedSlo(c.priority)
			if ok != c.ok || ttft != c.ttft || tpot != c.tpot {
				t.Errorf("DecodePackedSlo(%d) = (%d, %d, %v), want (%d, %d, %v)",
					c.priority, ttft, tpot, ok, c.ttft, c.tpot, c.ok)
			}
		})
	}
}

// The client packs; we unpack. Guard the round trip so the two sides cannot
// drift apart silently -- the packing also lives in
// patches/vllm-sched/slo_tier.py and the workload runner's _priority().
func TestDecodePackedSloRoundTrip(t *testing.T) {
	for _, ttft := range []int{1, 300, 5000, 11800, 60000} {
		for _, tpot := range []int{0, 25, 50, 100, 999} {
			priority := ttft*1000 + tpot
			gotTtft, gotTpot, ok := DecodePackedSlo(priority)
			if !ok || gotTtft != ttft || gotTpot != tpot {
				t.Errorf("round trip ttft=%d tpot=%d: got (%d, %d, %v)",
					ttft, tpot, gotTtft, gotTpot, ok)
			}
		}
	}
}
