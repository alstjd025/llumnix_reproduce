package policy

/*
The vLLM router's default routing policy, cache_aware, ported as a baseline.

WHICH ROUTER. "vLLM router" names two different systems. This is the PyPI
package `vllm-router` (Rust), whose default policy is `cache_aware`, and NOT
vllm-project/production-stack (Python), whose Helm chart deploys `roundrobin`.
vllm-router says of itself that it is a fork of the SGLang model gateway, and
that gateway is vendored in this repository, so the algorithm below was taken
from its source rather than from a README:

    lib/sglang/sgl-model-gateway/src/policies/cache_aware.rs

THE ALGORITHM, from that file's header comment. Two strategies, switched on load.

  imbalanced  (max_load - min_load) > balanceAbs  AND  max_load > balanceRel * min_load
              -> shortest queue: send to the least loaded instance.

  balanced    an approximate prefix tree per instance, built from the requests
              already sent to it.
              -> find the instance with the longest prefix match.
                 if match rate  >  cacheThreshold: send there.
                 if match rate <=  cacheThreshold: send to the instance with the
                 SMALLEST tree, i.e. the most free cache capacity.
              -> background LRU eviction keeps each tree under maxTreeSize.

WHAT THIS BASELINE IS FOR. It is the only arm in the comparison that uses the
prefix to CHOOSE A DESTINATION. FluidServe uses the prefix only to price prefill
per instance and its destination choice does not depend on it -- a distinction
motivation_v3 section 3.4.2 insists on and until now had no arm on the other side
of. It is also A0 in the admission taxonomy: there is no path here that refuses a
request, so overload appears only as queueing and violated SLOs.

TWO DEVIATIONS FROM THE ORIGINAL, both recorded in
ms_dev/notes/vllm-router-baseline.md rather than left in code comments alone.

  1. THE TREE IS OVER TOKEN IDS, NOT CHARACTERS. The original stores raw text
     precisely to avoid tokenising; this scheduler never sees text, only
     `PromptTokenIds`, so a character tree is not constructible here. The match
     rate therefore means "fraction of the prompt's TOKENS that some instance has
     already seen" instead of "fraction of its characters". The shapes agree and
     token-level matching is the more precise of the two, so if anything this
     favours the baseline.
  2. COMPLETION IS OBSERVED ON THE POLL; DISPATCH IS NOT. The original keeps its
     own per-worker counter, incremented when it dispatches and decremented when
     the response completes, so consecutive decisions see each other. There is no
     completion callback in this scheduler -- fluidserve infers completion the
     same way, by trimming its ledger to the count the engine reports -- so the
     load read by vllmCacheLoad below is the engine's own `NumWaitingRequests +
     NumRunningRequests` from the last poll PLUS `NumInflightDispatchRequests`,
     the dispatches made since that poll. The addend is incremented in
     scheduling_policy.go the moment an instance is selected and released when
     the engine's next status report names the request, so the increment side
     matches the original exactly and only the decrement waits for a poll.

     What that leaves: a request that finished less than one poll ago is still
     counted, so load is high by at most the completions in one poll interval
     (`--cms-pull-status-interval-ms`, 500 ms in this deployment). That error is
     the SAME for every instance under a roughly even completion rate, and both
     branches that read load -- the imbalance test and the shortest queue -- are
     comparisons between instances, so a common offset does not move either one.

     What it fixes, and why the arm could not be measured before this: reading
     only the poll made every decision between two polls see the same depth, so
     22 arrivals at 45 req/s all chose whichever instance looked emptiest 500 ms
     ago, and the policy could only notice that pile-up after it had formed. That
     is a defect of this port and not a property of the vLLM router, so measuring
     it would have attributed our own artifact to their algorithm.

     !! `NumInflightDispatchRequests` is only maintained when
     `--enable-instance-status-local-account` is true (the compile default). If
     it is false the addend is always zero and the stale-signal behaviour above
     returns silently, so the policy logs the flag on the startup line and logs
     an error naming this paragraph when it is off. Recorded in
     ms_dev/notes/vllm-router-baseline.md section 3.1.

THE CONSTANT THAT WOULD HAVE BEEN GOT WRONG. `cacheThreshold` is 0.3 in
vllm-router and 0.7 in the SGLang original the fork came from. It is the switch
between "choose by prefix" and "choose by free cache space", so the two give
different routers. 0.3 is used here because vllm-router is what is being compared
against, and the flag is exposed so the other value can be measured.
*/

import (
	"sync"
	"time"

	"k8s.io/klog/v2"

	"llumnix/cmd/scheduler/app/options"
	"llumnix/pkg/consts"
	"llumnix/pkg/metrics"
	"llumnix/pkg/types"
)

type vllmCacheConfig struct {
	cacheThreshold float64
	balanceAbs     int32
	balanceRel     float64
	evictionSecs   int64
	maxTreeSize    int
}

// vllmCacheRequest is what selectInstance needs and cannot get from its own
// arguments: the selector interface receives instance views and no request. It
// is stashed on each view in calculateMetrics, the same route fluidserve uses,
// and is race-free because a fresh set of views is allocated per request.
type vllmCacheRequest struct {
	id     string
	tokens []uint32
}

// tokenTrie is the per-instance approximate prefix tree. Nodes are keyed by
// token id, each node remembers when it was last on a matched path, and the
// tree tracks its own node count so the eviction pass and the "smallest tree"
// tie-break both have the number they need without walking it.
type tokenTrie struct {
	root  *trieNode
	nodes int
}

type trieNode struct {
	children map[uint32]*trieNode
	lastUsed int64 // unix milli
}

func newTokenTrie() *tokenTrie {
	return &tokenTrie{root: &trieNode{children: map[uint32]*trieNode{}}, nodes: 1}
}

// matchLen returns how many leading tokens of the prompt this instance has
// already been sent. Walking stops at the first token with no child, which is
// what makes this a PREFIX match rather than a set intersection.
func (t *tokenTrie) matchLen(tokens []uint32) int {
	n := t.root
	now := time.Now().UnixMilli()
	for i, tok := range tokens {
		c, ok := n.children[tok]
		if !ok {
			return i
		}
		c.lastUsed = now
		n = c
	}
	return len(tokens)
}

func (t *tokenTrie) insert(tokens []uint32) {
	n := t.root
	now := time.Now().UnixMilli()
	for _, tok := range tokens {
		c, ok := n.children[tok]
		if !ok {
			c = &trieNode{children: map[uint32]*trieNode{}, lastUsed: now}
			n.children[tok] = c
			t.nodes++
		}
		c.lastUsed = now
		n = c
	}
}

// evictLRU drops least-recently-used leaves until the tree is under max. The
// original runs this on a timer; here it runs at the same cadence but inline on
// the decision path, because a background goroutine would need its own lock
// discipline for no behavioural difference at these sizes.
func (t *tokenTrie) evictLRU(max int) {
	for t.nodes > max {
		parent, key, ok := t.oldestLeaf()
		if !ok {
			return
		}
		delete(parent.children, key)
		t.nodes--
	}
}

func (t *tokenTrie) oldestLeaf() (*trieNode, uint32, bool) {
	var bestParent *trieNode
	var bestKey uint32
	var bestTime int64
	found := false
	var walk func(n *trieNode)
	walk = func(n *trieNode) {
		for k, c := range n.children {
			if len(c.children) == 0 {
				if !found || c.lastUsed < bestTime {
					bestParent, bestKey, bestTime, found = n, k, c.lastUsed, true
				}
				continue
			}
			walk(c)
		}
	}
	walk(t.root)
	return bestParent, bestKey, found
}

type vllmCacheDispatchPolicy struct {
	baseDispatchPolicy
	cfg vllmCacheConfig

	mu        sync.Mutex
	trees     map[string]*tokenTrie
	lastEvict int64
}

func (p *vllmCacheDispatchPolicy) calculateMetrics(
	inferType consts.InferType,
	request *types.SchedulingRequest,
	instanceViews map[string]*instanceViewScheduling) {

	if p.baseDispatchPolicy[inferType] == nil {
		return
	}
	if inferType != consts.InferTypeNeutral || request == nil {
		p.baseDispatchPolicy.calculateMetrics(inferType, request, instanceViews)
		return
	}
	ctx := &vllmCacheRequest{id: request.Id, tokens: request.PromptTokenIds}
	for _, view := range instanceViews {
		view.schedulingCtx.vllmCacheRequest = ctx
	}
	p.baseDispatchPolicy.calculateMetrics(inferType, request, instanceViews)
}

// vllmCacheSelector is where the routing decision is made. It is a selector
// rather than an override of selectInstance so that the liveness filters above
// it still run: a dead or stale instance must not be a routing candidate, and
// the original router likewise only ever chooses among healthy workers.
type vllmCacheSelector struct {
	policy *vllmCacheDispatchPolicy
}

func (s *vllmCacheSelector) selectInstance(
	instanceViews map[string]*instanceViewScheduling,
	fallback bool) *instanceViewScheduling {

	p := s.policy
	if len(instanceViews) == 0 {
		return nil
	}

	var req *vllmCacheRequest
	for _, v := range instanceViews {
		if v.schedulingCtx.vllmCacheRequest != nil {
			req = v.schedulingCtx.vllmCacheRequest
			break
		}
	}
	if req == nil {
		// No request context: this is a path the neutral scheduler does not take
		// for a real arrival, and returning nothing here would look like a hold,
		// which this policy has no concept of. Any live instance is correct.
		for _, v := range instanceViews {
			return v
		}
		return nil
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	now := time.Now().UnixMilli()
	if now-p.lastEvict > p.cfg.evictionSecs*1000 {
		for _, t := range p.trees {
			t.evictLRU(p.cfg.maxTreeSize)
		}
		p.lastEvict = now
	}

	// Load is read per instance so that both branches below see the same
	// snapshot; taking it twice would let the imbalance test and the shortest
	// queue disagree about which instance is least loaded.
	type cand struct {
		view *instanceViewScheduling
		load int32
		tree *tokenTrie
	}
	cands := make([]cand, 0, len(instanceViews))
	var maxLoad, minLoad, inflightSum int32
	first := true
	for _, v := range instanceViews {
		load := vllmCacheLoad(v)
		inflightSum += vllmCacheInflight(v)
		id := v.GetInstanceId()
		t, ok := p.trees[id]
		if !ok {
			t = newTokenTrie()
			p.trees[id] = t
		}
		cands = append(cands, cand{view: v, load: load, tree: t})
		if first || load > maxLoad {
			maxLoad = load
		}
		if first || load < minLoad {
			minLoad = load
		}
		first = false
	}

	imbalanced := (maxLoad-minLoad) > p.cfg.balanceAbs &&
		float64(maxLoad) > p.cfg.balanceRel*float64(minLoad)

	var chosen cand
	var reason string
	if imbalanced {
		chosen = cands[0]
		for _, c := range cands[1:] {
			if c.load < chosen.load {
				chosen = c
			}
		}
		reason = "shortest_queue"
	} else {
		best := cands[0]
		bestMatch := 0
		for _, c := range cands {
			m := c.tree.matchLen(req.tokens)
			if m > bestMatch {
				bestMatch, best = m, c
			}
		}
		rate := 0.0
		if len(req.tokens) > 0 {
			rate = float64(bestMatch) / float64(len(req.tokens))
		}
		if rate > p.cfg.cacheThreshold {
			chosen, reason = best, "prefix_match"
		} else {
			// Smallest tree: the instance with the most free cache capacity.
			chosen = cands[0]
			for _, c := range cands[1:] {
				if c.tree.nodes < chosen.tree.nodes {
					chosen = c
				}
			}
			reason = "smallest_tree"
		}
	}

	chosen.tree.insert(req.tokens)
	metrics.Counter("scheduler_vllmcache_decisions_total",
		metrics.Labels{{Name: "reason", Value: reason}}).Inc()
	// The dispatches this decision could see that the last poll could not. A run
	// where this counter stays at zero is a run where the load signal collapsed
	// back to the poll snapshot -- see deviation 2 in the file header -- so it is
	// emitted rather than left as an assumption about the local-account flag.
	metrics.Counter("scheduler_vllmcache_inflight_seen_total",
		metrics.Labels{}).Add(int(inflightSum))
	klog.V(5).Infof("vllmcache routes %s to %s (%s, load %d, inflight %d, tree %d)",
		req.id, chosen.view.GetInstanceId(), reason, chosen.load,
		vllmCacheInflight(chosen.view), chosen.tree.nodes)
	return chosen.view
}

// vllmCacheInflight is the number of requests this instance has been sent since
// the engine's last status report: incremented in scheduling_policy.go at the
// moment an instance is selected, released when the engine's next report names
// the request. It is separated out only so that the quantity can be counted and
// logged on its own, because it is the part of the load that distinguishes this
// from reading the poll alone.
func vllmCacheInflight(v *instanceViewScheduling) int32 {
	if v == nil || v.cmsView == nil {
		return 0
	}
	return v.cmsView.NumInflightDispatchRequests
}

// vllmCacheLoad is the number of requests this instance has been sent and is not
// known to have finished, which is the quantity the original router keeps in its
// own per-worker counter.
//
// Two parts, because there is no completion callback in this scheduler and so no
// single place that holds the whole number. The engine's `NumWaitingRequests +
// NumRunningRequests` is what it reported at the last poll, and the inflight
// count above covers the dispatches made since. Adding them double-counts
// nothing: a request leaves the inflight count in the same update that first
// reports it to the engine.
//
// A request that completed less than one poll interval ago is still counted.
// Deviation 2 in the file header says why that does not change either decision
// this feeds.
func vllmCacheLoad(v *instanceViewScheduling) int32 {
	if v == nil || v.cmsView == nil {
		return 0
	}
	var engine int32
	if v.cmsView.Status != nil {
		engine = v.cmsView.Status.NumWaitingRequests + v.cmsView.Status.NumRunningRequests
	}
	return engine + v.cmsView.NumInflightDispatchRequests
}

func newVllmCacheDispatchFullMode(p *options.SchedulerConfig) *vllmCacheDispatchPolicy {
	policy := &vllmCacheDispatchPolicy{
		cfg: vllmCacheConfig{
			cacheThreshold: p.VllmCacheThreshold,
			balanceAbs:     int32(p.VllmCacheBalanceAbs),
			balanceRel:     p.VllmCacheBalanceRel,
			evictionSecs:   int64(p.VllmCacheEvictionSecs),
			maxTreeSize:    p.VllmCacheMaxTreeSize,
		},
		trees:     map[string]*tokenTrie{},
		lastEvict: time.Now().UnixMilli(),
	}
	policy.baseDispatchPolicy = baseDispatchPolicy{
		consts.InferTypeNeutral: {
			// No load metrics: the decision reads the queue depth directly,
			// because the imbalance test and the shortest-queue tie-break must
			// see the same snapshot of it.
			metrics: map[string]func() instanceSchedulingMetric{},
			globalFilters: []globalFilter{
				&failoverFilter{failoverDomain: p.FailoverDomain},
			},
			// Liveness only. This policy has no admission test, so a filter that
			// removed instances on load grounds would be adding one.
			singleInstanceFilters: []singleInstanceFilter{
				&schedulabilityFilter{},
				&stalenessFilter{instanceStalenessSeconds: p.InstanceStalenessSeconds},
			},
			selectors: &vllmCacheSelector{policy: policy},
		},
	}
	klog.Infof("vLLM router cache_aware policy created: cacheThreshold %.2f, "+
		"balanceAbs %d, balanceRel %.2f, evictionSecs %d, maxTreeSize %d, "+
		"localaccount=%v (tree over token ids; load = waiting + running + "+
		"dispatched since the last poll)",
		policy.cfg.cacheThreshold, policy.cfg.balanceAbs, policy.cfg.balanceRel,
		policy.cfg.evictionSecs, policy.cfg.maxTreeSize,
		p.EnableInstanceStatusLocalAccount)
	if !p.EnableInstanceStatusLocalAccount {
		// Not fatal, because a scheduler that will not start is reported by the
		// drivers as "reported no policy", which is what a slow rollout also
		// looks like. Loud instead, and paired with the inflight counter above so
		// that a run made in this state can be identified after the fact.
		klog.Errorf("vLLM router cache_aware: --enable-instance-status-local-account " +
			"is false, so the dispatches made since the last poll are not counted " +
			"and every decision between two polls will read the same queue depth. " +
			"This is the defect described in deviation 2 of vllmcache.go and in " +
			"ms_dev/notes/vllm-router-baseline.md section 3.1. DO NOT MEASURE THIS ARM.")
	}
	return policy
}
