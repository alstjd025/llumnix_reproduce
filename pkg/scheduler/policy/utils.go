package policy

import (
	"fmt"
	"math"
	"strings"

	"k8s.io/apimachinery/pkg/util/sets"
	"k8s.io/klog/v2"

	"llumnix/cmd/scheduler/app/options"
	"llumnix/pkg/cms"
	"llumnix/pkg/consts"
	"llumnix/pkg/lrs"
	"llumnix/pkg/types"
)

func getKeySliceFromMap[M ~map[K]V, K comparable, V any](m M) []K {
	keys := make([]K, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

func verifyConfig(c *options.SchedulerConfig) {
	verifySchedulingPolicy(c)
	verifySchedulingFeature(c)
	verifyDispatchLoadMetric(c)
}

func verifySchedulingPolicy(c *options.SchedulerConfig) {
	liteModeSchedulingPolicySet := sets.New[string](consts.SchedulingPolicyLoadBalance)
	fullModeSchedulingPolicySet := sets.New[string](
		consts.SchedulingPolicyLoadBalance,
		consts.SchedulingPolicyFlood,
		consts.SchedulingPolicySlo,
		// PolyServe needs the CMS instance status that only full mode pulls:
		// its admission test reads queue depth, decode batch and KV occupancy.
		consts.SchedulingPolicyPolyserve,
		// FluidServe reads the same CMS instance status, plus the engine's step
		// counter and last iteration duration, none of which lite mode pulls.
		consts.SchedulingPolicyFluidserve,
		// The vLLM router cache_aware baseline reads the same CMS instance status
		// for its queue depth.
		consts.SchedulingPolicyVllmCache)

	policy := c.SchedulingPolicy
	if !c.EnableFullModeScheduling {
		if !liteModeSchedulingPolicySet.Has(policy) {
			panic(fmt.Sprintf("The scheduling policy %s is not supported when not enable full-mode scheduling.", policy))
		}
	} else {
		if !fullModeSchedulingPolicySet.Has(policy) {
			panic(fmt.Sprintf("The scheduling policy %s is not supported when enabling full-mode scheduling.", policy))
		}
	}
}

func verifySchedulingFeature(c *options.SchedulerConfig) {
	if !c.EnableFullModeScheduling {
		if c.EnableCacheAwareScheduling {
			c.EnableCacheAwareScheduling = false
			klog.Warningf("The scheduling feature cache-aware scheduling is not supported when not enable full-mode scheduling, forcefully disable it here.")
		}
		if c.EnablePredictorEnhancedScheduling {
			c.EnablePredictorEnhancedScheduling = false
			klog.Warningf("The scheduling feature predictor-enhanced scheduling is not supported when not enable full-mode scheduling, forcefully disable it here.")
		}
		if c.EnableAdaptivePD {
			c.EnableAdaptivePD = false
			klog.Warningf("The scheduling feature adaptive-pd is not supported when not enable full-mode scheduling, forcefully disable it here.")
		}
		if c.EnableRescheduling {
			c.EnableRescheduling = false
			klog.Warningf("The scheduling feature rescheduling is not supported when not enable full-mode scheduling, forcefully disable it here.")
		}
		if c.EnableInstanceStatusLocalAccount {
			c.EnableInstanceStatusLocalAccount = false
			klog.Warningf("The scheduling feature instance-status-local-account is not supported when not enable full-mode scheduling, forcefully disable it here.")
		}
	}
}

func verifyDispatchLoadMetric(c *options.SchedulerConfig) {
	liteModeSchedulingMetricSet := sets.New[string](
		consts.SchedulingMetricNumRequests, consts.SchedulingMetricNumTokens)
	fullModeSchedulingMetricSet := sets.New[string](
		consts.SchedulingMetricKVCacheUsageRatioProjected, consts.SchedulingMetricDecodeBatchSize,
		consts.SchedulingMetricNumWaitingRequests, consts.SchedulingMetricAllPrefillsTokensNum,
		consts.SchedulingMetricKVCacheHitLen, consts.SchedulingMetricCacheAwareAllPrefillsTokensNum,
		consts.SchedulingMetricNumRequests, consts.SchedulingMetricAllDecodesTokensNum)

	if !c.EnableFullModeScheduling {
		if !liteModeSchedulingMetricSet.Has(c.DispatchNeutralLoadMetric) {
			klog.Warningf("The neutral dispatch load metric %s is not supported when not enable full-mode scheduling, "+
				"forcefully set metric to %s here.", c.DispatchNeutralLoadMetric, consts.SchedulingMetricNumTokens)
			c.DispatchNeutralLoadMetric = consts.SchedulingMetricNumTokens
		}
		if !liteModeSchedulingMetricSet.Has(c.DispatchPrefillLoadMetric) {
			klog.Warningf("The prefill dispatch load metric %s is not supported when not enable full-mode scheduling, "+
				"forcefully set metric to %s here", c.DispatchPrefillLoadMetric, consts.SchedulingMetricNumTokens)
			c.DispatchPrefillLoadMetric = consts.SchedulingMetricNumTokens
		}
		if !liteModeSchedulingMetricSet.Has(c.DispatchDecodeLoadMetric) {
			klog.Warningf("The decode dispatch load metric %s is not supported when not enable full-mode scheduling, "+
				"forcefully set metric to %s here", c.DispatchDecodeLoadMetric, consts.SchedulingMetricNumTokens)
			c.DispatchDecodeLoadMetric = consts.SchedulingMetricNumTokens
		}
	} else {
		if !fullModeSchedulingMetricSet.Has(c.DispatchNeutralLoadMetric) {
			klog.Warningf("The neutral dispatch load metric %s is not supported when enable full-mode scheduling, "+
				"forcefully set metric to %s here", c.DispatchNeutralLoadMetric, consts.SchedulingMetricKVCacheUsageRatioProjected)
			c.DispatchNeutralLoadMetric = consts.SchedulingMetricKVCacheUsageRatioProjected
		}
		if !fullModeSchedulingMetricSet.Has(c.DispatchPrefillLoadMetric) {
			klog.Warningf("The prefill dispatch load metric %s is not supported when enable full-mode scheduling, "+
				"forcefully set metric to %s here", c.DispatchPrefillLoadMetric, consts.SchedulingMetricAllPrefillsTokensNum)
			c.DispatchPrefillLoadMetric = consts.SchedulingMetricAllPrefillsTokensNum
		}
		if !fullModeSchedulingMetricSet.Has(c.DispatchDecodeLoadMetric) {
			klog.Warningf("The decode dispatch load metric %s is not supported when enable full-mode scheduling, "+
				"forcefully set metric to %s here", c.DispatchDecodeLoadMetric, consts.SchedulingMetricKVCacheUsageRatioProjected)
			c.DispatchDecodeLoadMetric = consts.SchedulingMetricKVCacheUsageRatioProjected
		}
	}
}

func toInstanceViewInterfaceMap[T InstanceViewInterface](
	raw map[consts.InferType]map[string]T) map[consts.InferType]map[string]InstanceViewInterface {
	if raw == nil {
		return nil
	}

	res := make(map[consts.InferType]map[string]InstanceViewInterface, len(raw))
	for mode, views := range raw {
		inner := make(map[string]InstanceViewInterface, len(views))
		for id, v := range views {
			inner[id] = v
		}
		res[mode] = inner
	}
	return res
}

func toClusterViewScheduling(cv clusterView) clusterViewScheduling {
	var groupedInstanceViews map[consts.InferType]map[string]*instanceViewScheduling
	var instanceViews map[string]*instanceViewScheduling
	groupedInstanceViews = make(map[consts.InferType]map[string]*instanceViewScheduling, len(cv.groupedInstanceViews))
	instanceViews = make(map[string]*instanceViewScheduling)
	for inferType, views := range cv.groupedInstanceViews {
		if groupedInstanceViews[inferType] == nil {
			groupedInstanceViews[inferType] = make(map[string]*instanceViewScheduling, len(views))
		}
		for instanceID, view := range views {
			groupedInstanceViews[inferType][instanceID] = &instanceViewScheduling{
				InstanceViewInterface: view,
				schedulingCtx: schedulingCtx{
					metrics:          map[string]instanceSchedulingMetric{},
					needsFailover:    false,
					prefixHitTokens:  0,
					prefixHitRatio:   0.0,
					prefixMissTokens: 0,
				},
			}
			switch v := view.(type) {
			case *cms.InstanceView:
				groupedInstanceViews[inferType][instanceID].cmsView = v
				groupedInstanceViews[inferType][instanceID].lrsView = nil
			case *lrs.InstanceView:
				groupedInstanceViews[inferType][instanceID].lrsView = v
				groupedInstanceViews[inferType][instanceID].cmsView = nil
			default:
				if v == nil {
					klog.Errorf("Instance view is nil for instance %s", instanceID)
				} else {
					klog.Errorf("Unexpected instance view type of instance %s: %T", instanceID, v)
				}
			}
			instanceViews[instanceID] = groupedInstanceViews[inferType][instanceID]
		}
	}
	result := clusterViewScheduling{
		groupedInstanceViews: groupedInstanceViews,
		instanceViews:        instanceViews,
		clusterSchedulingCtx: clusterSchedulingCtx{},
	}
	return result
}

// setRequestSlo stamps the request's SLO budgets onto every instance view, so
// that filters and selectors -- which are handed an instance view and no
// request -- can judge against the budget of the request actually being
// scheduled instead of a global config value. instanceViews holds the same
// pointers as groupedInstanceViews, so one pass covers both.
func (cv *clusterViewScheduling) setRequestSlo(request *types.SchedulingRequest) {
	if request == nil {
		return
	}
	for _, view := range cv.instanceViews {
		view.schedulingCtx.requestTtftSloMs = request.TtftSloMs
		view.schedulingCtx.requestTpotSloMs = request.TpotSloMs
	}
}

func getRemainingInstanceIds(
	instanceViews map[string]*instanceViewScheduling,
	filteredOutInstanceIds sets.Set[string]) sets.Set[string] {

	allInstanceIds := sets.New[string]()
	for id := range instanceViews {
		allInstanceIds.Insert(id)
	}
	return allInstanceIds.Difference(filteredOutInstanceIds)
}

func logSelectedInstance(
	instance *instanceViewScheduling, requestId string, requestInferType consts.InferType, enableFullModeScheduling bool) {
	var loadMetric instanceSchedulingMetric
	if enableFullModeScheduling {
		loadMetric = &kvCacheUsageRatioProjected{
			baseMetric: baseMetric{
				name: consts.SchedulingMetricKVCacheUsageRatioProjected,
			},
		}
	} else {
		loadMetric = &numTokens{
			baseMetric: baseMetric{
				name: consts.SchedulingMetricNumTokens,
			},
		}
	}
	loadMetric.Calculate(nil, instance)
	klog.V(3).Infof("[Schedule] dispatch request %s to %s instance %s for %s, %s: %.4f",
		requestId, instance.GetInferType(), instance.GetInstanceId(), requestInferType,
		loadMetric.GetName(), loadMetric.GetValue())
	logPredictionForRequest(instance, requestId)
}

// predictionMetrics are the scheduling metrics that are a POLICY'S OWN FORECAST
// of what a placement will cost, as opposed to a reading of the instance's
// current state. Llumnix SLO computes the first two and admits on them;
// PolyServe computes the last two, iter_now being the per-token time it expects
// at the batch as it stands and iter_max the same at the largest KV the batch
// reaches. A policy that does not compute one simply has no entry.
var predictionMetrics = []string{
	consts.SchedulingMetricPredictedTtft,
	consts.SchedulingMetricPredictedTpot,
	consts.SchedulingMetricPolyserveIterNow,
	consts.SchedulingMetricPolyserveIterMax,
}

// logPredictionForRequest records, per request, the value the policy predicted
// for the instance it chose.
//
// WHY THIS LINE EXISTS. Every policy here that can express a latency budget
// decides by forecasting one and comparing it against that budget, and the
// forecast is the thing under test: how far is it from what the request then
// experiences? recordSelectedInstanceSchedulingMetrics already observes the same
// two values into histograms, but a histogram cannot be joined to a request, and
// the comparison has to be per request because the error is class dependent --
// FluidServe's own first-token estimate was measured 1.68x high for chat and
// 0.74x low for deepresearch over the same eight minutes.
//
// It must be joined against the CLIENT's first_token_latency, not against
// anything the scheduler reports. The scheduler cannot observe when a request
// produced its first token: reconcile infers it from the engine's global step
// counter, which advances on every engine step and not on that request's, so it
// reported a mean of 314 ms with nothing over 10 s while the client recorded
// 22.8% of deepresearch over 10 s in the same run. Comparing a forecast against
// that would compare a model with a model.
//
// FORMAT. The prefix matches what llumnix_metrics.py already greps at the
// source, so the line reaches server_metrics/scheduler_dispatch.log with no
// change to the collector. It deliberately does NOT match
// build_request_engine_map.py's pattern, which requires "to <x> instance
// <digits>" after the uuid: the engine attribution join must keep reading the
// line above, not this one. At Infof rather than V(3) so that a run made with a
// lower verbosity still carries it -- the line above is V(3) and only survives
// because this scheduler happens to run at -v 4.
func logPredictionForRequest(instance *instanceViewScheduling, requestId string) {
	var b strings.Builder
	for _, name := range predictionMetrics {
		m, ok := instance.schedulingCtx.metrics[name]
		if !ok {
			continue
		}
		v := m.GetValue()
		if math.IsInf(float64(v), 0) || math.IsNaN(float64(v)) {
			// An infinite forecast is the policy saying "not feasible here", and
			// it is information: it must reach the log as a sentinel rather than
			// as a formatted +Inf that the offline parser reads as a number.
			fmt.Fprintf(&b, " %s=inf", name)
			continue
		}
		fmt.Fprintf(&b, " %s=%.3f", name, v)
	}
	if b.Len() == 0 {
		return
	}
	klog.Infof("[Schedule] dispatch request %s prediction%s inst=%s",
		requestId, b.String(), instance.GetInstanceId())
}
