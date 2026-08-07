package policy

import (
	"container/list"
	"sync"
)

// prefixIndex remembers which instance each block of prompt was last sent to, so
// that the cost of an arriving prompt can be charged per instance instead of at
// one fleet-wide rate.
//
// Why this exists. The prefill charge in `evaluate` was
// `promptTokens * prefillFractionOf()`, one scalar for the whole fleet measured
// from what the engines actually computed. That is right about the LEVEL and
// carries no signal about WHICH instance is cheap for this prompt, so a request
// whose prefix is resident on instance 2 is charged the same on all four. EXP-66
// measured what that costs: llm-d, which routes on prefix affinity, held a 93.5%
// engine-reported prefix hit rate at 70 req/s against our 75.1%, computed 4,585
// prompt tokens per second against our 12,274, and turned the difference into
// decode -- 20,661 tokens per second against 16,562.
//
// What is remembered is what THIS SCHEDULER DISPATCHED, not what the engine
// holds. The engine evicts and we do not see it, so this over-estimates hits.
// That error is measured rather than assumed: `capacityModel.notePrefill` now
// divides the engine's computed tokens by the charge this predicted, so its
// output is the ratio between the two and sits at 1.0 exactly when the
// prediction is right. See ms_dev/notes/fluidserve-prefix.md section 2.
//
// The alternative was the Llumnix cache-aware path, which queries a KVS
// metadata service and therefore sees eviction. It was not taken for two
// reasons: turning it on without that service deployed panics the scheduler at
// startup (scheduling_policy.go, the kvs.CreateOrGetClient call), and its own
// comment describes the lookup as having "ms-level latency", which this policy
// cannot afford because a pended request re-enters the scheduling path on every
// gateway recheck and the resulting call rate has saturated the scheduler
// before.
type prefixIndex struct {
	mu sync.Mutex

	blockTokens int
	capacity    int

	m     map[uint64]*prefixEntry
	order *list.List // front = most recently used

	instBit map[string]uint32
	nextBit uint

	hits, queries uint64
}

type prefixEntry struct {
	hash uint64
	mask uint32
	el   *list.Element
}

func newPrefixIndex(blockTokens, capacity int) *prefixIndex {
	if blockTokens <= 0 {
		blockTokens = 16
	}
	if capacity <= 0 {
		capacity = 500000
	}
	return &prefixIndex{
		blockTokens: blockTokens,
		capacity:    capacity,
		m:           make(map[uint64]*prefixEntry, capacity/4),
		order:       list.New(),
		instBit:     map[string]uint32{},
	}
}

// FNV-1a, chained so that a block's hash depends on every token before it.
//
// The chaining is what makes this a PREFIX index rather than a set of block
// identities: the same sixteen tokens appearing at a different offset, or after
// a different history, must not match. vLLM's own block hashing chains for the
// same reason, and the Llumnix token hasher does too
// (hasher.HashTokens: `prefixHash` is folded into the next block). A uint64
// hash is used here rather than that helper because the helper returns hex
// strings and this is on the scheduling path for every candidate.
const (
	fnvOffset64 = 14695981039346656037
	fnvPrime64  = 1099511628211
)

// hashPrompt turns a prompt into one chained hash per whole block.
//
// The trailing partial block is dropped rather than hashed. A partial block is
// not a unit the engine caches, so including it would produce a hash that can
// never be matched by a later request and would grow the index by one dead
// entry per request.
func hashPrompt(tokens []int64, blockTokens int) []uint64 {
	if blockTokens <= 0 || len(tokens) < blockTokens {
		return nil
	}
	n := len(tokens) / blockTokens
	out := make([]uint64, 0, n)
	h := uint64(fnvOffset64)
	for i := 0; i < n; i++ {
		for _, t := range tokens[i*blockTokens : (i+1)*blockTokens] {
			u := uint64(t)
			for b := 0; b < 8; b++ {
				h ^= u & 0xff
				h *= fnvPrime64
				u >>= 8
			}
		}
		out = append(out, h)
	}
	return out
}

func (p *prefixIndex) bitOf(instance string) uint32 {
	if b, ok := p.instBit[instance]; ok {
		return b
	}
	// More than 32 instances would silently alias, so the extra ones get no bit
	// and therefore never report a hit. Charging them the whole prompt is the
	// conservative direction and this fleet has four.
	if p.nextBit >= 32 {
		return 0
	}
	b := uint32(1) << p.nextBit
	p.nextBit++
	p.instBit[instance] = b
	return b
}

// hitTokens is how many tokens of this prompt the given instance is expected to
// already hold.
//
// Only a run of blocks from the FRONT counts. A prefix cache matches a prompt
// from its first token and stops at the first block it does not have, so a block
// that is resident but sits behind a missing one cannot be used. This is the
// same rule the Llumnix KVS path applies in calcInstancesPrefixCacheHitLen,
// where the `instanceBroken` map ends an instance's run at the first gap.
func (p *prefixIndex) hitTokens(hashes []uint64, instance string) int {
	if p == nil || len(hashes) == 0 {
		return 0
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	bit, ok := p.instBit[instance]
	p.queries++
	if !ok || bit == 0 {
		return 0
	}
	n := 0
	for _, h := range hashes {
		e, ok := p.m[h]
		if !ok || e.mask&bit == 0 {
			break
		}
		p.order.MoveToFront(e.el)
		n++
	}
	if n > 0 {
		p.hits++
	}
	return n * p.blockTokens
}

// note records that this prompt was dispatched to this instance.
//
// Called from commit, so PEND and SHED record nothing -- neither reached an
// engine, so neither put anything in a cache.
func (p *prefixIndex) note(hashes []uint64, instance string) {
	if p == nil || len(hashes) == 0 || instance == "" {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	bit := p.bitOf(instance)
	if bit == 0 {
		return
	}
	for _, h := range hashes {
		if e, ok := p.m[h]; ok {
			e.mask |= bit
			p.order.MoveToFront(e.el)
			continue
		}
		e := &prefixEntry{hash: h, mask: bit}
		e.el = p.order.PushFront(e)
		p.m[h] = e
	}
	for len(p.m) > p.capacity {
		back := p.order.Back()
		if back == nil {
			break
		}
		e := back.Value.(*prefixEntry)
		p.order.Remove(back)
		delete(p.m, e.hash)
	}
}

// stats reports the index size and how often a lookup found anything, for the
// report loop. The hit fraction here is NOT the engine's prefix hit rate: it
// counts lookups that matched at least one block, and the engine's figure is
// over tokens. Both are published so that a gap between them is visible.
func (p *prefixIndex) stats() (blocks int, lookups, matched uint64) {
	if p == nil {
		return 0, 0, 0
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.m), p.queries, p.hits
}
