package policy

import (
	"time"

	"golang.org/x/exp/maps"
	"k8s.io/klog/v2"

	"llumnix/cmd/scheduler/app/options"
	"llumnix/pkg/cms"
	"llumnix/pkg/consts"
	"llumnix/pkg/lrs"
	"llumnix/pkg/metrics"
	"llumnix/pkg/scheduler/hasher"
	"llumnix/pkg/scheduler/kvs"
	"llumnix/pkg/types"
)

type InstanceViewInterface interface {
	GetInstance() *types.LLMInstance
	GetInstanceId() string
	GetInferType() consts.InferType
}

type clusterView struct {
	groupedInstanceViews map[consts.InferType]map[string]InstanceViewInterface
}

type instanceViewScheduling struct {
	cmsView *cms.InstanceView
	lrsView *lrs.InstanceView
	InstanceViewInterface
	schedulingCtx
}

type schedulingCtx struct {
	metrics map[string]instanceSchedulingMetric
	// Set by the vLLM router cache_aware baseline in calculateMetrics, for the
	// same reason as fluidserveRequest below: a selector receives instance views
	// and no request.
	vllmCacheRequest *vllmCacheRequest
	// needsFailover indicates whether the instance needs failover by failover filter.
	needsFailover                     bool
	prefixHitTokens                   int
	prefixHitRatio                    float32
	prefixMissTokens                  int
	numComputedPrefillTokensPredicted int32

	// Per-request SLO budgets in milliseconds, copied onto every instance view
	// at the top of Schedule(). Filters and selectors receive only an instance
	// view -- their signatures take no request -- so this is how a per-request
	// value reaches them without changing those interfaces. It is race-free
	// because toClusterViewScheduling() allocates fresh views per request.
	// Zero means unspecified; an SLO-aware policy then falls back to its global
	// --ttft-slo / --tpot-slo.
	requestTtftSloMs int
	requestTpotSloMs int

	// FluidServe stashes its whole per-request decision context here for the
	// same reason: its selector needs the request, the instance's projected
	// state, and every other instance's projected state at once, and the
	// selector signature carries only instance views. Both pointers are written
	// once per request by the policy's calculateMetrics hook, before any filter
	// or selector runs.
	fluidserveRequest *fluidserveRequest
	fluidserveFlux    *instanceFlux

	// PolyServe, for the same reason: three of the four rungs of its placement
	// ladder need a fact about the whole tier rather than about one instance,
	// so the ladder runs in the selector and the request has to reach it.
	polyserveRequest *polyserveRequest
}

type clusterViewScheduling struct {
	groupedInstanceViews map[consts.InferType]map[string]*instanceViewScheduling
	instanceViews        map[string]*instanceViewScheduling
	clusterSchedulingCtx clusterSchedulingCtx
}

type clusterSchedulingCtx struct {
}

type ClusterViewClientInterface interface {
	RLock()
	RUnlock()
	Lock()
	Unlock()
}

type SchedulingPolicy interface {
	// Name scheduling policy name
	Name() string

	// Schedule attempts to acquire an instance for processing a new request.
	Schedule(*types.SchedulingRequest) error

	// ReleaseRequestLocalAccount cleans up CMS local accounts for a released request.
	// Called when the gateway releases scheduling resources (e.g., forwarding failure during retry)
	// to immediately remove stale local accounts.
	ReleaseRequestLocalAccount(requestId string)
}

func NewSchedulingPolicy(
	policy string,
	config *options.SchedulerConfig,
	lrsClient *lrs.LocalRealtimeStateClient) SchedulingPolicy {
	if len(policy) == 0 {
		panic("create scheduling policy exception, policy is empty.")
	}

	klog.Infof("create scheduler with policy: %v", policy)

	return NewDispatchPolicy(config, policy, lrsClient)
}

type DispatchPolicy struct {
	c                 *options.SchedulerConfig
	schedulingPolicy  string
	cmsClient         *cms.CMSReadClient
	lrsClient         *lrs.LocalRealtimeStateClient
	clusterViewClient ClusterViewClientInterface
	kvsClient         kvs.KVSClientInterface
	tokenHasher       *hasher.TokenHasher
	policyInternal    dispatchPolicyInternal
	clusterView       clusterView
}

func NewDispatchPolicy(
	c *options.SchedulerConfig, schedulingPolicy string, lrsClient *lrs.LocalRealtimeStateClient) *DispatchPolicy {

	verifyConfig(c)

	var cmsClient *cms.CMSReadClient
	var clusterViewClient ClusterViewClientInterface
	if c.EnableFullModeScheduling {
		client, err := cms.CreateOrGetClient(
			c.CmsRedisHost,
			c.CmsRedisPort,
			c.CmsRedisUsername,
			c.CmsRedisPassword,
			c.CmsRedisSocketTimeout,
			c.CmsRedisRetryTimes,
			c.CmsPullStatusIntervalMs,
			c.CmsPullMetadataIntervalMs,
			c.AllowConcurrentScheduling,
			c.EnableInstanceStatusLocalAccount,
			c.EnableCacheAwareScheduling,
			c.RequestLocalAccountStalenessSeconds,
			c.EnablePredictorEnhancedScheduling,
			c.NumPredictorWarmupSamples,
			c.EnableAdaptivePD)
		if err != nil {
			panic(err)
		}
		cmsClient = client
		cmsClient.SetinstanceSchedulingMetricsRecorder(recordCMSInstanceSchedulingMetrics)
		clusterViewClient = cmsClient
	} else {
		clusterViewClient = lrsClient
	}

	var kvsClient kvs.KVSClientInterface
	var tokenHasher *hasher.TokenHasher
	if c.EnableCacheAwareScheduling {
		client, err := kvs.CreateOrGetClient(
			c.KvsBackend,
			c.KvsMetadataServiceConfigPath,
			c.KvsRetryTimes,
			c.KvsRetryIntervalMs,
			c.KvsMetadataServiceDownDurationS,
			c.KvsMetadataServiceRedisClusterHosts,
			c.KvsMetadataServiceRedisClusterPassword,
			c.KvsMetadataServiceHttpServerHost,
			c.KvsMetadataServiceHttpServerPort)
		if err != nil {
			panic(err)
		}
		kvsClient = client

		// Override hash algo for v6d backend: v6d always uses xxhash.
		hashAlgo := c.KvsHashAlgo
		if c.KvsBackend == consts.KvsBackendV6d {
			klog.Infof("Overriding KvsHashAlgo from %q to %q for v6d backend", c.KvsHashAlgo, consts.KvsHashAlgoXxhash)
			hashAlgo = consts.KvsHashAlgoXxhash
		}
		th, err := hasher.NewTokenHasher(hashAlgo)
		if err != nil {
			panic(err)
		}
		tokenHasher = th
	}

	return &DispatchPolicy{
		c:                 c,
		schedulingPolicy:  schedulingPolicy,
		cmsClient:         cmsClient,
		lrsClient:         lrsClient,
		clusterViewClient: clusterViewClient,
		kvsClient:         kvsClient,
		tokenHasher:       tokenHasher,
		policyInternal:    newDispatchPolicyInternal(c),
		clusterView: clusterView{
			groupedInstanceViews: nil,
		},
	}
}

func (p *DispatchPolicy) Name() string {
	return p.schedulingPolicy
}

func (p *DispatchPolicy) ReleaseRequestLocalAccount(requestId string) {
	if !p.c.EnableInstanceStatusLocalAccount || p.cmsClient == nil {
		return
	}
	p.cmsClient.RemoveRequestLocalAccount(requestId)
}

func Uint32ToInt64(arr []uint32) []int64 {
	result := make([]int64, len(arr))
	for i, v := range arr {
		result[i] = int64(v)
	}
	return result
}

func (p *DispatchPolicy) Schedule(request *types.SchedulingRequest) error {
	tStart := time.Now()
	defer func() {
		elapsed := time.Since(tStart).Milliseconds()
		klog.V(3).Infof("Llumnix Schedule took %dms, request id: %v, promptTokenIds len: %v",
			elapsed, request.Id, request.PromptNumTokens)
	}()

	if p.c.EnableFullModeScheduling {
		if !p.cmsClient.IsAlive() {
			klog.V(2).Info("CMS client is not alive, return ErrorCmsNotAvailable")
			return consts.ErrorCmsNotAvailable
		}
	}

	klog.V(4).Infof("Schedule request received, promptTokenIds length: %d", request.PromptNumTokens)

	startTime := time.Now()

	if p.c.AllowConcurrentScheduling {
		p.clusterViewClient.RLock()
	} else {
		p.clusterViewClient.Lock()
	}
	defer func() {
		if p.c.AllowConcurrentScheduling {
			p.clusterViewClient.RUnlock()
		} else {
			p.clusterViewClient.Unlock()
		}
		elapsed := time.Since(startTime)
		metrics.Histogram("request_full_mode_schedule_duration_milliseconds", metrics.Labels{}).ObserveInt(elapsed.Milliseconds())
		// MICROSECONDS, UNDER A DIFFERENT NAME. The millisecond histogram above
		// records `elapsed.Milliseconds()`, which FLOORS: every decision faster
		// than 1 ms lands in the same bucket as a decision that took no time at
		// all. Its sum/count is therefore not a mean -- on the EXP-139 runs it
		// reads 0.009 to 0.015, which says only that 0.9% to 1.5% of decisions
		// crossed a millisecond, and leaves the actual mean unknown anywhere
		// between 0 and 1 ms.
		//
		// The name carries the unit because the old one is in every run already
		// on disk. Reusing it would put values a thousand-fold apart in one
		// column, which is the "two quantities under one name" failure this
		// repository keeps recording.
		metrics.Histogram("request_full_mode_schedule_duration_microseconds", metrics.Labels{}).ObserveInt(elapsed.Microseconds())
	}()

	if p.c.EnableFullModeScheduling {
		p.clusterView.groupedInstanceViews = toInstanceViewInterfaceMap(p.cmsClient.GetGroupedInstanceViews())
	} else {
		p.clusterView.groupedInstanceViews = toInstanceViewInterfaceMap(p.lrsClient.GetGroupedInstanceViews())
	}
	clusterViewScheduling := toClusterViewScheduling(p.clusterView)
	clusterViewScheduling.setRequestSlo(request)
	klog.V(4).Infof("Retrieved cluster instances, count: %d", len(clusterViewScheduling.instanceViews))

	// The policy's own decision, timed apart from everything around it. The
	// histogram above spans the lock, the CMS fetch and the view conversion,
	// which are Llumnix's cost and are paid by every policy alike; only this
	// call is the cost of the routing and admission rules under test. Reporting
	// the outer number as "our overhead" would charge us for infrastructure we
	// share with the baselines.
	policyStart := time.Now()
	selectedInstances := p.schedule(request, clusterViewScheduling)
	metrics.Histogram("request_policy_decide_duration_microseconds", metrics.Labels{}).
		ObserveInt(time.Since(policyStart).Microseconds())
	if len(selectedInstances) == 0 {
		// An empty result means one of two different things, and the gateway has
		// to be told which. A policy that holds a request answers "no endpoint"
		// on every retry while it waits, and the gateway keeps holding. A policy
		// that has decided the request will not be served says so through this
		// interface, and the gateway returns it to the client at once instead of
		// holding a request nobody intends to place.
		if rej, ok := p.policyInternal.(admissionRejecter); ok &&
			rej.admissionRejected(request.Id) {
			klog.V(3).Infof("request %s rejected by admission control", request.Id)
			return consts.ErrorAdmissionRejected
		}
		klog.Warningf("No instances selected, return ErrorNoAvailableEndpoint")
		return consts.ErrorNoAvailableEndpoint
	}

	var schResults types.SchedulingResult
	for _, instance := range selectedInstances[0] {
		if instance == nil {
			continue
		}
		targetInstance := *instance.GetInstance()

		// For adaptive pd, all instances can serve as both Prefill and Decode,
		// with the specific role being determined at runtime.
		if p.c.EnableAdaptivePD {
			targetInstance.InferType = consts.InferTypePrefill
		}

		schResults = append(schResults, targetInstance)
		klog.V(4).Infof("Added token from instance: %s", instance.GetInstanceId())
		logSelectedInstance(instance, request.Id, consts.InferTypePrefill, p.c.EnableFullModeScheduling)
		if p.c.EnableFullModeScheduling {
			recordSelectedInstanceSchedulingMetrics(instance, consts.InferTypePrefill)
			if request.SchedulingMode == types.SchedulingModeNeutral {
				recordSelectedInstanceSchedulingMetrics(instance, consts.InferTypeDecode)
			}
		}
	}

	if len(selectedInstances) > 1 {
		for _, instance := range selectedInstances[1] {
			if instance == nil {
				continue
			}
			targetInstance := *instance.GetInstance()
			if p.c.EnableAdaptivePD {
				targetInstance.InferType = consts.InferTypeDecode
			}

			schResults = append(schResults, targetInstance)
			klog.V(4).Infof("Added token2 from instance: %s", instance.GetInstanceId())
			logSelectedInstance(instance, request.Id, consts.InferTypeDecode, p.c.EnableFullModeScheduling)
			if p.c.EnableFullModeScheduling {
				recordSelectedInstanceSchedulingMetrics(instance, consts.InferTypeDecode)
			}
		}
	}
	request.SchedulingResult = schResults
	return nil
}

func (p *DispatchPolicy) schedule(
	request *types.SchedulingRequest,
	clusterView clusterViewScheduling) (selectedInstances [][]*instanceViewScheduling) {
	requestId := request.Id
	promptTokenIds := Uint32ToInt64(request.PromptTokenIds)
	selectedInstances = [][]*instanceViewScheduling{}
	if p.c.EnableCacheAwareScheduling && p.tokenHasher != nil && request.PromptNumTokens >= p.c.CacheAwareSchedulingMinTokens {
		// NOTE(sunbiao.sun): Calculating instance prompt cache hit len has ms-level latency, but it does not r/w
		// the raw instance view, so we unlock and re-lock here to improve scheduling throughput
		// when not allowing concurrent scheduling.
		if !p.c.AllowConcurrentScheduling {
			p.cmsClient.Unlock()
		}
		prefixHashes, err := p.tokenHasher.HashTokens(
			promptTokenIds, p.c.KvsChunkSize, p.c.KvsEnableSaveUnfullChunk,
			p.c.KvsIrisMetaPrefix, p.c.KvsVLLMBlockPrefix)
		if err != nil {
			klog.Warningf("HashTokens failed: %v", err)
		} else {
			// Write prefixHitTokens in scheduling ctx of instance view.
			getInstancesPrefixCacheHitLen(
				p.kvsClient, p.cmsClient, prefixHashes, p.c.KvsChunkSize,
				request.PromptNumTokens, clusterView.instanceViews)
		}
		if !p.c.AllowConcurrentScheduling {
			p.cmsClient.Lock()
		}
	}

	numTokens := int32(0)
	if p.c.EnableInstanceStatusLocalAccount {
		numTokens = int32(request.PromptNumTokens)
	}

	if p.c.EnablePredictorEnhancedScheduling {
		predictNumComputedPrefillTokens(
			clusterView.groupedInstanceViews[consts.InferTypePrefill], p.cmsClient.TTFTPredictor, int32(p.c.MaxNumBatchedTokens))
	}
	for inferType, instanceViews := range clusterView.groupedInstanceViews {
		klog.V(3).Infof(
			"Calculating metrics for infer type: %s, instance count: %d", inferType, len(instanceViews))
		p.policyInternal.calculateMetrics(inferType, request, instanceViews)
	}

	if request.SchedulingMode == types.SchedulingModeNeutral {
		if _, exists := clusterView.groupedInstanceViews[consts.InferTypeNeutral]; exists {
			neutral := p.executeSchedule(consts.InferTypeNeutral, clusterView)
			if neutral != nil {
				klog.V(4).Infof("Neutral instance selected: %s", neutral.GetInstanceId())
				selectedInstances = append(selectedInstances, []*instanceViewScheduling{neutral})
				if p.c.EnableInstanceStatusLocalAccount {
					p.cmsClient.AddRequestLocalAccount(
						neutral.cmsView, consts.InferTypeNeutral, numTokens,
						int32(neutral.schedulingCtx.prefixHitTokens), requestId)
				}
				if p.c.EnableCacheAwareScheduling {
					metrics.Histogram("request_prefix_cache_hit_percent", metrics.Labels{}).ObserveInt(int64(neutral.schedulingCtx.prefixHitRatio * 100))
				}
			} else {
				klog.V(4).Info("No neutral instance selected")
			}
		}
		return
	}

	if len(clusterView.groupedInstanceViews) == 1 {
		klog.V(4).Infof(
			"PD mode requires both Prefill and Decode instance groups, but only %d group found: %v. Returning empty selection.",
			len(clusterView.groupedInstanceViews), maps.Keys(clusterView.groupedInstanceViews))
		return
	}

	var prefill *instanceViewScheduling
	needPrefill := request.SchedulingMode == types.SchedulingModePDBatch ||
		(request.SchedulingMode == types.SchedulingModePDStaged && request.SchedulingStage == consts.SchedulingStagePrefill)
	if needPrefill {
		prefill = p.executeSchedule(consts.InferTypePrefill, clusterView)
		if prefill == nil {
			klog.V(4).Info("No prefill instance selected, return")
		} else {
			if p.c.EnableInstanceStatusLocalAccount {
				p.cmsClient.AddRequestLocalAccount(
					prefill.cmsView, consts.InferTypePrefill, numTokens,
					int32(prefill.schedulingCtx.prefixHitTokens), requestId)
			}
			if p.c.EnableCacheAwareScheduling {
				metrics.Histogram("request_prefix_cache_hit_percent", metrics.Labels{}).ObserveInt(int64(prefill.schedulingCtx.prefixHitRatio * 100))
			}
		}
	}

	var decode *instanceViewScheduling
	needDecode := request.SchedulingMode == types.SchedulingModePDBatch ||
		(request.SchedulingMode == types.SchedulingModePDStaged && request.SchedulingStage == consts.SchedulingStageDecode)
	if needDecode {
		decode = p.executeSchedule(consts.InferTypeDecode, clusterView)
		if decode == nil {
			if p.c.EnableInstanceStatusLocalAccount && prefill != nil {
				p.cmsClient.RevertRequestPrefillLocalAccount(
					prefill.cmsView, numTokens, int32(prefill.schedulingCtx.prefixHitTokens), requestId)
			}
			klog.V(4).Info("No decode instance selected, return")
		} else {
			if p.c.EnableInstanceStatusLocalAccount {
				p.cmsClient.AddRequestLocalAccount(
					decode.cmsView, consts.InferTypeDecode, numTokens, int32(decode.schedulingCtx.prefixHitTokens),
					requestId)
			}
		}
	}

	selectedInstances = append(selectedInstances,
		[]*instanceViewScheduling{prefill}, []*instanceViewScheduling{decode})
	return
}

// Execute the filters in order. If no suitable instance is found, set fallback=true
// for all filters and execute them again. Then, apply the selector across all
// available instances to choose the most suitable instance.
func (p *DispatchPolicy) executeSchedule(
	inferType consts.InferType,
	clusterView clusterViewScheduling,
) *instanceViewScheduling {
	var availableInstanceViews map[string]*instanceViewScheduling

	var targetInstanceViews map[string]*instanceViewScheduling
	if !p.c.EnableAdaptivePD {
		targetInstanceViews = clusterView.groupedInstanceViews[inferType]
	} else {
		targetInstanceViews = clusterView.instanceViews
	}

	fallback := false
	availableInstanceViews = p.policyInternal.filter(
		inferType, targetInstanceViews, fallback)

	if len(availableInstanceViews) == 0 {
		klog.V(4).Info("No instances found without fallback, executing scheduling steps with fallback=true")
		fallback = true
		availableInstanceViews = p.policyInternal.filter(
			inferType, targetInstanceViews, fallback)
	}

	if len(availableInstanceViews) == 0 {
		klog.V(4).Info("No available instances after all steps, return nil")
		return nil
	}

	klog.V(4).Infof("Available instances count: %d, fallback: %v, instance IDs: %v",
		len(availableInstanceViews), fallback, maps.Keys(availableInstanceViews))

	selected := p.policyInternal.selectInstance(inferType, availableInstanceViews, fallback)
	if selected == nil {
		klog.V(4).Info("No instance selected by policy internal, return nil")
		return nil
	}
	klog.V(4).Infof("Instance selected by policy internal: %s", selected.GetInstanceId())

	return selected
}

// admissionRejecter is implemented by policies that can decide a request will
// not be served at all. Reporting is one-shot: the flag is cleared as it is
// read, so it describes the scheduling call that just ran and nothing else.
type admissionRejecter interface {
	admissionRejected(requestID string) bool
}

type dispatchPolicyInternal interface {
	calculateMetrics(
		inferType consts.InferType,
		request *types.SchedulingRequest,
		instanceViews map[string]*instanceViewScheduling)
	filter(
		inferType consts.InferType,
		instanceViews map[string]*instanceViewScheduling,
		fallback bool) map[string]*instanceViewScheduling
	selectInstance(
		inferType consts.InferType,
		instanceViews map[string]*instanceViewScheduling,
		fallback bool) *instanceViewScheduling
}

type baseDispatchPolicy map[consts.InferType]*inferTypeBaseDispatchPolicy

type inferTypeBaseDispatchPolicy struct {
	metrics               map[string]func() instanceSchedulingMetric // key: metricsName
	globalFilters         []globalFilter
	singleInstanceFilters []singleInstanceFilter
	selectors             dispatchSelector
}

func (p baseDispatchPolicy) calculateMetrics(
	inferType consts.InferType,
	request *types.SchedulingRequest,
	instanceViews map[string]*instanceViewScheduling) {

	calculateMetrics(request, instanceViews, p[inferType].metrics)
}

func (p baseDispatchPolicy) filter(
	inferType consts.InferType,
	instanceViews map[string]*instanceViewScheduling,
	fallback bool) map[string]*instanceViewScheduling {

	availableInstanceViews := filter(
		instanceViews,
		p[inferType].singleInstanceFilters,
		p[inferType].globalFilters,
		fallback)

	klog.V(4).Infof("BaseDispatchPolicy filter completed, available instances count: %d, instance IDs: %v",
		len(availableInstanceViews), maps.Keys(availableInstanceViews))

	return availableInstanceViews
}

func (p baseDispatchPolicy) selectInstance(
	inferType consts.InferType,
	instanceViews map[string]*instanceViewScheduling,
	fallback bool) *instanceViewScheduling {

	selected := p[inferType].selectors.selectInstance(instanceViews, fallback)
	if selected != nil {
		klog.V(4).Infof("Instance selected: %s", selected.GetInstanceId())
	} else {
		klog.V(4).Info("No instance selected")
	}
	return selected
}

// recordCMSInstanceSchedulingMetrics is the callback registered on CMSReadClient.
// It wraps a cms.InstanceView into instanceViewScheduling and reuses Calculate methods
// to record computed scheduling metrics during CMS status refresh.
func recordCMSInstanceSchedulingMetrics(view *cms.InstanceView, labels metrics.Labels) {
	iv := &instanceViewScheduling{
		cmsView:               view,
		InstanceViewInterface: view,
	}
	metricsToRecord := []struct {
		gaugeName string
		metric    instanceSchedulingMetric
	}{
		{"instance_cms_kv_cache_usage_ratio_projected", &kvCacheUsageRatioProjected{
			baseMetric: baseMetric{name: consts.SchedulingMetricKVCacheUsageRatioProjected}}},
		{"instance_cms_decode_batch_size", &decodeBatchSize{
			baseMetric: baseMetric{name: consts.SchedulingMetricDecodeBatchSize}}},
		{"instance_cms_all_prefills_tokens_num", &allPrefillsTokensNum{
			baseMetric: baseMetric{name: consts.SchedulingMetricAllPrefillsTokensNum}}},
		{"instance_cms_all_decodes_tokens_num", &allDecodesTokensNum{
			baseMetric: baseMetric{name: consts.SchedulingMetricAllDecodesTokensNum}}},
	}
	for _, m := range metricsToRecord {
		m.metric.Calculate(nil, iv)
		metrics.Gauge(m.gaugeName, labels).Set(float64(m.metric.GetValue()))
	}
}

func recordSelectedInstanceSchedulingMetrics(instance *instanceViewScheduling, inferType consts.InferType) {
	if instance == nil || instance.cmsView == nil {
		return
	}
	instanceLabels := metrics.Labels{
		{Name: "infer_type", Value: string(inferType)},
	}

	// Reuse Calculate methods from metrics.go to compute values
	metricsToRecord := []struct {
		metricName string
		metric     instanceSchedulingMetric
	}{
		{"selected_instance_kv_cache_usage_ratio_projected", &kvCacheUsageRatioProjected{
			baseMetric: baseMetric{name: consts.SchedulingMetricKVCacheUsageRatioProjected}}},
		{"selected_instance_decode_batch_size", &decodeBatchSize{
			baseMetric: baseMetric{name: consts.SchedulingMetricDecodeBatchSize}}},
		{"selected_instance_all_prefills_tokens_num", &allPrefillsTokensNum{
			baseMetric: baseMetric{name: consts.SchedulingMetricAllPrefillsTokensNum}}},
		{"selected_instance_all_decodes_tokens_num", &allDecodesTokensNum{
			baseMetric: baseMetric{name: consts.SchedulingMetricAllDecodesTokensNum}}},
	}
	for _, m := range metricsToRecord {
		m.metric.Calculate(nil, instance)
		metrics.Histogram(m.metricName, instanceLabels).Observe(float64(m.metric.GetValue()))
	}

	// PredictedTtft and PredictedTpot: use pre-computed values from schedulingCtx if available
	if m, ok := instance.schedulingCtx.metrics[consts.SchedulingMetricPredictedTtft]; ok {
		metrics.Histogram("selected_instance_predicted_ttft", instanceLabels).Observe(float64(m.GetValue()))
	}
	if m, ok := instance.schedulingCtx.metrics[consts.SchedulingMetricPredictedTpot]; ok {
		metrics.Histogram("selected_instance_predicted_tpot", instanceLabels).Observe(float64(m.GetValue()))
	}
}
