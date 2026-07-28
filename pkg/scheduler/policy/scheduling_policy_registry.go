package policy

import (
	"fmt"
	"time"

	"k8s.io/klog/v2"

	"llumnix/cmd/scheduler/app/options"
	"llumnix/pkg/consts"
	"llumnix/pkg/types"
)

func newDispatchPolicyInternal(c *options.SchedulerConfig) dispatchPolicyInternal {
	switch c.SchedulingPolicy {
	case consts.SchedulingPolicyLoadBalance:
		if c.EnableFullModeScheduling {
			return newLoadBalanceDispatchFullMode(c)
		} else {
			return newLoadBalanceDispatchLiteMode(c)
		}
	case consts.SchedulingPolicyFlood:
		return newFloodDispatchPolicyFullMode(c)
	case consts.SchedulingPolicySlo:
		return newSloDispatchFullMode(c)
	case consts.SchedulingPolicyPolyserve:
		return newPolyserveDispatchFullMode(c)
	case consts.SchedulingPolicyFluidserve:
		return newFluidserveDispatchFullMode(c)
	default:
		panic(fmt.Sprintf("unsupported scheduling policy: %s", c.SchedulingPolicy))
	}
}

type loadBalanceDispatchPolicy struct {
	baseDispatchPolicy
}

func newLoadBalanceDispatchFullMode(p *options.SchedulerConfig) *loadBalanceDispatchPolicy {
	policy := &loadBalanceDispatchPolicy{
		baseDispatchPolicy: baseDispatchPolicy{
			consts.InferTypePrefill: {
				metrics: map[string]func() instanceSchedulingMetric{
					p.DispatchPrefillLoadMetric: getSchedulingMetric(p, p.DispatchPrefillLoadMetric),
				},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
					&metricBasedFilter{
						metricName: p.DispatchPrefillLoadMetric,
						threshold:  p.DispatchPrefillLoadThreshold,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{p.DispatchPrefillLoadMetric},
				},
			},
			consts.InferTypeDecode: {
				metrics: map[string]func() instanceSchedulingMetric{
					p.DispatchDecodeLoadMetric: getSchedulingMetric(p, p.DispatchDecodeLoadMetric),
				},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
					&metricBasedFilter{
						metricName: p.DispatchDecodeLoadMetric,
						threshold:  p.DispatchDecodeLoadThreshold,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{p.DispatchDecodeLoadMetric},
				},
			},
			consts.InferTypeNeutral: {
				metrics: map[string]func() instanceSchedulingMetric{
					p.DispatchNeutralLoadMetric: getSchedulingMetric(p, p.DispatchNeutralLoadMetric),
				},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
					&metricBasedFilter{
						metricName: p.DispatchNeutralLoadMetric,
						threshold:  p.DispatchNeutralLoadThreshold,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{p.DispatchNeutralLoadMetric},
				},
			},
		},
	}

	// TODO(sunbiao.sun): Extract this part into a separate function
	// Placed the cache locality metric as the first metric to be used in the metric-based selector
	if p.EnableCacheAwareScheduling {
		prefillInferTypeMetrics := policy.baseDispatchPolicy[consts.InferTypePrefill].metrics
		prefillInferTypeMetrics[p.DispatchPrefillCacheLocalityMetric] = getSchedulingMetric(p, p.DispatchPrefillCacheLocalityMetric)
		prefillInstanceSelector := policy.baseDispatchPolicy[consts.InferTypePrefill].selectors.(*metricBasedSelector)
		prefillInstanceSelector.metricNames = append([]string{p.DispatchPrefillCacheLocalityMetric}, prefillInstanceSelector.metricNames...)

		normalInferTypeMetrics := policy.baseDispatchPolicy[consts.InferTypeNeutral].metrics
		normalInferTypeMetrics[p.DispatchPrefillCacheLocalityMetric] = getSchedulingMetric(p, p.DispatchPrefillCacheLocalityMetric)
		neutralInstanceSelector := policy.baseDispatchPolicy[consts.InferTypeNeutral].selectors.(*metricBasedSelector)
		neutralInstanceSelector.metricNames = append([]string{p.DispatchPrefillCacheLocalityMetric}, neutralInstanceSelector.metricNames...)
	}

	// Hard KV-occupancy admission filter (neutral only). Unlike the load
	// threshold filter above, this one is NOT skipped on fallback: when every
	// instance's hot KV usage ratio is >= the threshold, scheduling fails with
	// no-available-endpoint and the request is rejected upstream (429/503)
	// instead of being force-dispatched to the least-loaded instance.
	if p.AdmissionKvUsageThreshold > 0 {
		neutral := policy.baseDispatchPolicy[consts.InferTypeNeutral]
		neutral.metrics[consts.SchedulingMetricKVCacheUsageRatio] =
			getSchedulingMetric(p, consts.SchedulingMetricKVCacheUsageRatio)
		neutral.singleInstanceFilters = append(neutral.singleInstanceFilters,
			&metricBasedFilter{
				metricName:          consts.SchedulingMetricKVCacheUsageRatio,
				threshold:           p.AdmissionKvUsageThreshold,
				notSkipWhenFallback: true,
			})
		klog.Infof("KV-usage admission filter enabled for neutral dispatch: threshold=%.3f",
			p.AdmissionKvUsageThreshold)
	}

	return policy
}

// The SLO policy attempts to route requests to the instance with the lowest latency.
type sloDispatchPolicy struct {
	baseDispatchPolicy
}

func newSloDispatchFullMode(p *options.SchedulerConfig) *sloDispatchPolicy {
	// init latency predictor, fast fail
	GetLatencyPredictor(p.TtftProfilingDataPath, p.TpotProfilingDataPath)

	// TODO(KuilongCui): add a configurable argument to allow users to choose whether to reject
	// requests that fail to meet SLO targets for SloDispatchPolicy and Adaptive PD.
	policy := &sloDispatchPolicy{
		baseDispatchPolicy: baseDispatchPolicy{
			// Co-located (neutral) instances, which is what this fleet is. The
			// upstream policy defines only Prefill and Decode because it assumes a
			// disaggregated deployment where an instance does one or the other, and
			// baseDispatchPolicy is a map of pointers, so a neutral request against
			// the policy as shipped is a nil dereference rather than a fallback.
			//
			// The RULE is unchanged: keep only the instances predicted to meet the
			// budget, then take the best among them. What has to be combined is that
			// a neutral instance serves both phases, so both predictions constrain
			// it at once and both filters apply, where upstream each branch applies
			// one. Ordering is by predicted TPOT with predicted TTFT as the
			// tie-break, because the decode budget is the one that binds for the
			// whole of a request's life while the prefill budget binds once.
			consts.InferTypeNeutral: {
				metrics: map[string]func() instanceSchedulingMetric{
					consts.SchedulingMetricPredictedTtft: getSchedulingMetric(p, consts.SchedulingMetricPredictedTtft),
					consts.SchedulingMetricPredictedTpot: getSchedulingMetric(p, consts.SchedulingMetricPredictedTpot),
				},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
					&metricBasedFilter{
						metricName:          consts.SchedulingMetricPredictedTtft,
						threshold:           p.TtftSlo * p.TtftSloDispatchThreshold,
						notSkipWhenFallback: true,
					},
					&metricBasedFilter{
						metricName:          consts.SchedulingMetricPredictedTpot,
						threshold:           p.TpotSlo * p.TpotSloDispatchThreshold,
						notSkipWhenFallback: true,
					},
				},
				selectors: &metricBasedSelector{
					topK: p.DispatchTopK,
					metricNames: []string{
						consts.SchedulingMetricPredictedTpot,
						consts.SchedulingMetricPredictedTtft,
					},
				},
			},
			consts.InferTypePrefill: {
				metrics: map[string]func() instanceSchedulingMetric{
					consts.SchedulingMetricPredictedTtft: getSchedulingMetric(p, consts.SchedulingMetricPredictedTtft),
				},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
					&metricBasedFilter{
						metricName:          consts.SchedulingMetricPredictedTtft,
						threshold:           p.TtftSlo * p.TtftSloDispatchThreshold,
						notSkipWhenFallback: true,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{consts.SchedulingMetricPredictedTtft},
				},
			},
			consts.InferTypeDecode: {
				metrics: map[string]func() instanceSchedulingMetric{
					consts.SchedulingMetricPredictedTpot: getSchedulingMetric(p, consts.SchedulingMetricPredictedTpot),
				},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
					&metricBasedFilter{
						metricName:          consts.SchedulingMetricPredictedTpot,
						threshold:           p.TpotSlo * p.TpotSloDispatchThreshold,
						notSkipWhenFallback: true,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{consts.SchedulingMetricPredictedTpot},
				},
			},
		},
	}

	if p.EnableAdaptivePD {
		configureAdaptivePDForSlo(policy, p)
	}

	return policy
}

func configureAdaptivePDForSlo(policy *sloDispatchPolicy, p *options.SchedulerConfig) {
	// On instances without decode requests, the instance with the lowest predicted TTFT will be selected for Prefill.
	policy.baseDispatchPolicy[consts.InferTypePrefill].metrics[consts.SchedulingMetricDecodeBatchSize] =
		getSchedulingMetric(p, consts.SchedulingMetricDecodeBatchSize)
	policy.baseDispatchPolicy[consts.InferTypePrefill].metrics[consts.SchedulingMetricPredictedTpot] =
		getSchedulingMetric(p, consts.SchedulingMetricPredictedTpot)
	policy.baseDispatchPolicy[consts.InferTypePrefill].singleInstanceFilters = []singleInstanceFilter{
		&schedulabilityFilter{},
		&stalenessFilter{
			instanceStalenessSeconds: p.InstanceStalenessSeconds,
		},
		&instanceAttributeFilter{
			attrKey:       consts.AttrKeyReservedInferType,
			rejectedValue: consts.InferTypeDecode,
		},
		&metricBasedFilter{
			metricName: consts.SchedulingMetricPredictedTtft,
			threshold:  p.TtftSlo * p.TtftSloDispatchThreshold,
		},
		&metricBasedFilter{
			metricName:          consts.SchedulingMetricDecodeBatchSize,
			threshold:           0.1,
			notSkipWhenFallback: true,
		}}
	policy.baseDispatchPolicy[consts.InferTypePrefill].selectors = &sloPrefillApdSelector{}

	// Select the instance with the highest Predicted TPOT that does not exceed the TPOT SLO as decode
	// (bin-packing for decode). If no available instance is found, attempt to convert the P instance with
	// the lowest Predicted TTFT to D. Finally, if no available instance that meets the TPOT SLO can be
	// found, perform load balancing across all D instances.
	policy.baseDispatchPolicy[consts.InferTypeDecode].metrics[consts.SchedulingMetricPredictedTtft] =
		getSchedulingMetric(p, consts.SchedulingMetricPredictedTtft)
	policy.baseDispatchPolicy[consts.InferTypeDecode].metrics[consts.SchedulingMetricDecodeBatchSize] =
		getSchedulingMetric(p, consts.SchedulingMetricDecodeBatchSize)
	policy.baseDispatchPolicy[consts.InferTypeDecode].singleInstanceFilters = []singleInstanceFilter{
		&schedulabilityFilter{},
		&stalenessFilter{
			instanceStalenessSeconds: p.InstanceStalenessSeconds,
		},
		&instanceAttributeFilter{
			attrKey:       consts.AttrKeyReservedInferType,
			rejectedValue: consts.InferTypePrefill,
		},
		&metricBasedFilter{
			metricName: consts.SchedulingMetricPredictedTtft,
			threshold:  p.TpotSlo * p.TpotSloDispatchThreshold,
		},
		&metricBasedFilter{
			metricName: consts.SchedulingMetricPredictedTpot,
			threshold:  p.TpotSlo * p.TpotSloDispatchThreshold,
		},
	}
	policy.baseDispatchPolicy[consts.InferTypeDecode].selectors = &sloDecodeApdSelector{}
}

// The flood policy attempts to always route requests to the same instance whenever possible.
type floodDispatchPolicy struct {
	baseDispatchPolicy
}

func newFloodDispatchPolicyFullMode(p *options.SchedulerConfig) *floodDispatchPolicy {
	policy := &floodDispatchPolicy{
		baseDispatchPolicy: baseDispatchPolicy{
			consts.InferTypePrefill: {
				metrics: map[string]func() instanceSchedulingMetric{},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
				},
				selectors: &fixedPreferenceSelector{},
			},
			consts.InferTypeDecode: {
				metrics: map[string]func() instanceSchedulingMetric{},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
				},
				selectors: &fixedPreferenceSelector{},
			},
			consts.InferTypeNeutral: {
				metrics: map[string]func() instanceSchedulingMetric{},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
				},
				selectors: &fixedPreferenceSelector{},
			},
		},
	}

	if p.EnableAdaptivePD {
		klog.Warning("AdaptivePD is ignored for flood dispatch policy.")
	}

	if p.EnableCacheAwareScheduling {
		klog.Warning("CacheAwareScheduling is ignored for flood dispatch policy.")
	}

	return policy
}

func newLoadBalanceDispatchLiteMode(p *options.SchedulerConfig) *loadBalanceDispatchPolicy {
	policy := &loadBalanceDispatchPolicy{
		baseDispatchPolicy: baseDispatchPolicy{
			consts.InferTypePrefill: {
				metrics: map[string]func() instanceSchedulingMetric{
					p.DispatchPrefillLoadMetric: getSchedulingMetric(p, p.DispatchPrefillLoadMetric),
				},
				globalFilters: []globalFilter{},
				singleInstanceFilters: []singleInstanceFilter{
					&metricBasedFilter{
						metricName: p.DispatchPrefillLoadMetric,
						threshold:  p.DispatchPrefillLoadThreshold,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{p.DispatchPrefillLoadMetric},
				},
			},
			consts.InferTypeDecode: {
				metrics: map[string]func() instanceSchedulingMetric{
					p.DispatchDecodeLoadMetric: getSchedulingMetric(p, p.DispatchDecodeLoadMetric),
				},
				globalFilters: []globalFilter{},
				singleInstanceFilters: []singleInstanceFilter{
					&metricBasedFilter{
						metricName: p.DispatchDecodeLoadMetric,
						threshold:  p.DispatchDecodeLoadThreshold,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{p.DispatchDecodeLoadMetric},
				},
			},
			consts.InferTypeNeutral: {
				metrics: map[string]func() instanceSchedulingMetric{
					p.DispatchNeutralLoadMetric: getSchedulingMetric(p, p.DispatchNeutralLoadMetric),
				},
				globalFilters: []globalFilter{},
				singleInstanceFilters: []singleInstanceFilter{
					&metricBasedFilter{
						metricName: p.DispatchNeutralLoadMetric,
						threshold:  p.DispatchNeutralLoadThreshold,
					},
				},
				selectors: &metricBasedSelector{
					topK:        p.DispatchTopK,
					metricNames: []string{p.DispatchNeutralLoadMetric},
				},
			},
		},
	}

	return policy
}

// polyserveDispatchPolicy routes by per-request SLO tier, following PolyServe
// (arXiv:2507.17769). See polyserve.go for how each of the paper's mechanisms
// maps onto Llumnix.
type polyserveDispatchPolicy struct {
	baseDispatchPolicy
	tierPartition *tierPartition
	repartitioner *tierRepartitioner
}

// calculateMetrics is the scheduling path's only per-request hook that sees both
// the request and the live instance set, so the repartitioner rides along here
// instead of running on its own goroutine -- that way the allocation is always
// computed against the same instances the request is about to be scheduled on.
func (p *polyserveDispatchPolicy) calculateMetrics(
	inferType consts.InferType,
	request *types.SchedulingRequest,
	instanceViews map[string]*instanceViewScheduling) {

	// The caller iterates every infer type present in the cluster, but this
	// policy only defines Neutral; baseDispatchPolicy is a map of pointers, so
	// indexing an undefined infer type would panic rather than no-op.
	if p.baseDispatchPolicy[inferType] == nil {
		return
	}
	if inferType == consts.InferTypeNeutral {
		p.repartitioner.observe(request)
		p.repartitioner.maybeRepartition(time.Now(), instanceViews)
	}
	p.baseDispatchPolicy.calculateMetrics(inferType, request, instanceViews)
}

// newPolyserveDispatchFullMode builds the policy for co-located (neutral)
// instances. Unlike newSloDispatchFullMode, which only defines Prefill and
// Decode, this defines InferTypeNeutral, because PolyServe here runs on a
// co-located fleet -- and because baseDispatchPolicy is a map of pointers, a
// missing infer type is a nil dereference rather than a graceful fallback.
func newPolyserveDispatchFullMode(p *options.SchedulerConfig) *polyserveDispatchPolicy {
	// Fail fast if the profiling tables are unusable: every admission decision
	// depends on them, so starting without them would silently route blind.
	GetLatencyPredictor(p.TtftProfilingDataPath, p.TpotProfilingDataPath)

	partition := newTierPartition()
	decodeTokens, err := parseTierDecodeTokens(p.PolyserveTierDecodeTokens, p.PolyserveDecodeTokens)
	if err != nil {
		panic(fmt.Sprintf("invalid --polyserve-tier-decode-tokens: %v", err))
	}

	policy := &polyserveDispatchPolicy{
		tierPartition: partition,
		repartitioner: newTierRepartitioner(
			decodeTokens,
			GetLatencyPredictor(p.TtftProfilingDataPath, p.TpotProfilingDataPath),
			partition),
		baseDispatchPolicy: baseDispatchPolicy{
			consts.InferTypeNeutral: {
				metrics: map[string]func() instanceSchedulingMetric{
					consts.SchedulingMetricPredictedTtft: getSchedulingMetric(p, consts.SchedulingMetricPredictedTtft),
					consts.SchedulingMetricPolyserveIterNow: getSchedulingMetric(
						p, consts.SchedulingMetricPolyserveIterNow),
					consts.SchedulingMetricPolyserveIterMax: getSchedulingMetric(
						p, consts.SchedulingMetricPolyserveIterMax),
				},
				globalFilters: []globalFilter{
					&failoverFilter{
						failoverDomain: p.FailoverDomain,
					},
				},
				singleInstanceFilters: []singleInstanceFilter{
					&schedulabilityFilter{},
					&stalenessFilter{
						instanceStalenessSeconds: p.InstanceStalenessSeconds,
					},
					// Tier isolation holds even on the fallback pass; admission
					// relaxes so an overloaded tier degrades to "least loaded
					// server in my tier" instead of being rejected.
					&tierAffinityFilter{partition: partition},
					&polyserveAdmissionFilter{
						globalTtftSloMs:   p.TtftSlo,
						globalTpotSloMs:   p.TpotSlo,
						ttftSloMultiplier: p.TtftSloDispatchThreshold,
						tpotSloMultiplier: p.TpotSloDispatchThreshold,
					},
				},
				selectors: &leastBindingLatencySelector{
					globalTtftSloMs: p.TtftSlo,
					globalTpotSloMs: p.TpotSlo,
				},
			},
		},
	}

	klog.Infof("PolyServe dispatch policy created: ttftSlo=%.0fms tpotSlo=%.0fms "+
		"(dispatch thresholds %.2f/%.2f), tier decode tokens %q (default %d)",
		p.TtftSlo, p.TpotSlo, p.TtftSloDispatchThreshold, p.TpotSloDispatchThreshold,
		p.PolyserveTierDecodeTokens, p.PolyserveDecodeTokens)

	return policy
}
