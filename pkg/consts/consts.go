package consts

import (
	"time"
)

const (
	MetricRecordDuration = 5 * time.Second
)

// InferType represents the inference type of an instance or request.
type InferType string

const (
	InferTypeNeutral InferType = "neutral"
	InferTypePrefill InferType = "prefill"
	InferTypeDecode  InferType = "decode"
	InferTypeAll     InferType = "all" // Only for filtering purposes, will not appear in llm worker
)

func (m InferType) String() string {
	return string(m)
}

type SchedulingStage string

const (
	SchedulingStagePrefill SchedulingStage = "prefill"
	SchedulingStageDecode  SchedulingStage = "decode"
)

const (
	RoutePolicyWeight = "weight"
	RoutePolicyPrefix = "prefix"
	RouteInternalURL  = "local"
)

// llm scheduling policy with use a remote concertized scheduler
const (
	SchedulingPolicyRoundRobin  = "round-robin"
	SchedulingPolicyLoadBalance = "load-balance"
	SchedulingPolicyFlood       = "flood"
	SchedulingPolicySlo         = "slo"
	// SchedulingPolicyPolyserve routes by per-request SLO tier: the request may
	// only land on a server assigned to its tier, must pass an admission test
	// derived from PolyServe (arXiv:2507.17769) sections 4.5 to 4.7, and among
	// survivors goes to the least loaded rather than the most loaded.
	SchedulingPolicyPolyserve = "polyserve"

	// SchedulingPolicyFluidserve decides routing and admission from a single
	// quantity: how much more KV an instance can take over a planning horizon
	// while still meeting the latency budgets of the requests already on it.
	// When no instance has enough, the request is held at the gateway instead of
	// being committed to an engine, which keeps the placement decision open at
	// no cost. See ms_dev/notes/fluidserve-implementation.md.
	SchedulingPolicyFluidserve = "fluidserve"

	// SchedulingPolicyVllmCache is the vLLM router's default policy, cache_aware,
	// ported as a baseline. It is the PyPI `vllm-router` package's default and
	// NOT vllm-project/production-stack's, whose Helm chart deploys roundrobin.
	// It routes by longest prefix match among per-instance approximate trees,
	// falls back to the emptiest tree when the match is weak, and switches to
	// shortest queue when the instances are far enough out of balance. It has no
	// admission test: it always returns an instance.
	SchedulingPolicyVllmCache = "vllm-cache"
)

const (
	KvsBackendV6d      = "v6d"
	KvsBackendMooncake = "mooncake"
)

const (
	SchedulingMetricKVCacheUsageRatioProjected     = "kv_cache_usage_ratio_projected"
	SchedulingMetricKVCacheUsageRatio              = "kv_cache_usage_ratio"
	SchedulingMetricDecodeBatchSize                = "decode_batch_size"
	SchedulingMetricNumWaitingRequests             = "num_waiting_requests"
	SchedulingMetricAllPrefillsTokensNum           = "all_prefills_tokens_num"
	SchedulingMetricKVCacheHitLen                  = "kv_cache_hit_len"
	SchedulingMetricCacheAwareAllPrefillsTokensNum = "cache_aware_all_prefills_tokens_num"
	SchedulingMetricNumRequests                    = "num_requests"
	SchedulingMetricAllDecodesTokensNum            = "all_decodes_tokens_num"
	SchedulingMetricNumTokens                      = "num_tokens"

	SchedulingMetricPredictedTtft = "predicted_ttft"
	SchedulingMetricPredictedTpot = "predicted_tpot"

	// PolyServe splits the iteration-time estimate in two, because the near
	// term and the steady state fail for different reasons and the paper checks
	// them separately.
	//
	// IterNow is the very next iteration, at the KV the instance holds right
	// now. A co-scheduled prefill chunk dominates it (a full 8192-token chunk
	// measures 634 ms against 17-113 ms for a decode step), which is what
	// section 4.7 guards against for co-location.
	//
	// IterMax is the steady state, at the largest KV the batch will reach as
	// its requests grow to their expected output length -- section 4.5 admits
	// on that maximum rather than on a snapshot.
	SchedulingMetricPolyserveIterNow = "polyserve_iter_now"
	SchedulingMetricPolyserveIterMax = "polyserve_iter_max"
)

const (
	MigrationReqSelectRuleNumReq = "NUM_REQ"
	MigrationReqSelectRuleToken  = "TOKEN"
	MigrationReqSelectRuleRatio  = "RATIO"

	MigrationReqSelectOrderLCR   = "LCR"   // last running
	MigrationReqSelectOrderFCR   = "FCR"   // first running
	MigrationReqSelectOrderLR    = "LR"    // longest running
	MigrationReqSelectOrderSR    = "SR"    // shortest running
	MigrationReqSelectOrderFCW   = "FCW"   // first waiting
	MigrationReqSelectOrderFCWSR = "FCWSR" // first waiting and shortest running
)

const (
	ReschedulingPolicyNeutralLoad     = "neutral_load"
	ReschedulingPolicyDecodeLoad      = "decode_load"
	ReschedulingPolicyPrefillFailover = "prefill_failover"
	ReschedulingPolicyDecodeFailover  = "decode_failover"
	ReschedulingPolicyNeutralFailover = "neutral_failover"

	ReschedulingPolicyBinPackingMitigation    = "binpacking_mitigation"
	ReschedulingPolicyBinPackingConsolidation = "binpacking_consolidation"
)

const (
	ReschedulingLoadBalanceScopeCluster = "cluster"
	ReschedulingLoadBalanceScopeUnit    = "unit"
)

// different pd-disagg protocol
const (
	PDDisaggProtocolVllmKvt        = "vllm-kvt"
	PDDisaggProtocolSGlangMooncake = "sglang-mooncake"
	PDDisaggProtocolVllmMooncake   = "vllm-mooncake"
)

// forwarder type constants, mapping to config PDDisaggProtocol values
const (
	ForwarderTypeNeutral        = "neutral"
	ForwarderTypeVllmKvt        = PDDisaggProtocolVllmKvt
	ForwarderTypeSglangMooncake = PDDisaggProtocolSGlangMooncake
	ForwarderTypeVllmMooncake   = PDDisaggProtocolVllmMooncake
)

// gateway support different discovery mode
const (
	DiscoveryEndpoints = "endpoints"
	DiscoveryRedis     = "redis"
	DiscoveryEtcd      = "etcd"
)

const (
	// FailoverDomainInstanceUnit failover instances sharing the unit with the unschedulable instances
	FailoverDomainInstanceUnit = "instance-unit"
	// FailoverDomainNodeUnit failover instances sharing units with instances on nodes requiring failover
	FailoverDomainNodeUnit = "node-unit"
	// FailoverDomainNode failover instances sharing the same node with the unschedulable instances
	FailoverDomainNode = "node"
	// FailoverDomainInstance When the failover domain is instance, it is equivalent to failover filter not being enabled.
	FailoverDomainInstance = "instance"
)

const (
	KvsHashAlgoSha256Hex  = "sha256_hex"
	KvsHashAlgoSha256CBOR = "sha256_cbor"
	KvsHashAlgoXxhash     = "xxhash"
)

// default value
const (
	DefaultEnableFullModeScheduling = true

	// CMS defaults
	DefaultCmsRedisHost              = "redis.roles"
	DefaultCmsRedisPort              = "10000"
	DefaultCmsRedisUsername          = ""
	DefaultCmsRedisPassword          = ""
	DefaultCmsRedisSocketTimeout     = 1.0
	DefaultCmsRedisRetryTimes        = 1
	DefaultCmsPullStatusIntervalMs   = 50
	DefaultCmsPullMetadataIntervalMs = 10000

	// KvsMetaService defaults
	DefaultEnableCacheAwareScheduling             = false
	DefaultCacheAwareSchedulingMinTokens          = 1024
	DefaultKvsBackend                             = "mooncake"
	DefaultKvsMetadataServiceConfigPath           = ""
	DefaultKvsChunkSize                           = 256
	DefaultKvsEnableSaveUnfullChunk               = false
	DefaultKvsIrisMetaPrefix                      = "iris."
	DefaultKvsVLLMBlockPrefix                     = "block.hash.key."
	DefaultKvsRetryIntervalMs                     = 100
	DefaultKvsRetryTimes                          = 5
	DefaultKvsMetadataServiceDownDurationS        = 30
	DefaultKvsMetadataServiceRedisClusterHosts    = ""
	DefaultKvsMetadataServiceRedisClusterPassword = ""
	DefaultKvsMetadataServiceHttpServerHost       = "0.0.0.0"
	DefaultKvsMetadataServiceHttpServerPort       = "9003"
	DefaultKvsHashAlgo                            = KvsHashAlgoSha256CBOR

	// Scheduling defaults
	DefaultDispatchTopK                        = 1
	DefaultDispatchNeutralLoadMetric           = SchedulingMetricAllPrefillsTokensNum
	DefaultDispatchNeutralLoadThreshold        = 8192
	DefaultDispatchPrefillLoadMetric           = SchedulingMetricAllPrefillsTokensNum
	DefaultDispatchPrefillLoadThreshold        = 2048
	DefaultDispatchDecodeLoadMetric            = SchedulingMetricKVCacheUsageRatioProjected
	DefaultDispatchDecodeLoadThreshold         = 1.0
	DefaultDispatchPrefillCacheLocalityMetric  = SchedulingMetricCacheAwareAllPrefillsTokensNum
	DefaultEnableInstanceStatusLocalAccount    = true
	DefaultRequestLocalAccountStalenessSeconds = 10
	DefaultAllowConcurrentScheduling           = false
	DefaultEnablePredictorEnhancedScheduling   = false
	DefaultMaxNumBatchedTokens                 = 65536
	DefaultNumPredictorWarmupSamples           = 20

	DefaultTtftSlo                     = 6000
	DefaultTpotSlo                     = 50
	DefaultTtftSloDispatchThreshold    = 0.85
	DefaultTpotSloDispatchThreshold    = 0.85
	DefaultTpotMigrateOutCeilThreshold = 0.95

	// DefaultPolyserveDecodeTokens is the fallback expected output length when a
	// request's tier is not listed in --polyserve-tier-decode-tokens. 463 is the
	// request-weighted mean of the mix workload's measured per-class means
	// (chat 386, deepresearch 275, swe 728) at 1:1:1.
	DefaultPolyserveDecodeTokens = 463

	// FluidServe defaults. The horizon is long enough that a request admitted
	// now is judged against how the instance will look once its current batch
	// has grown, and short enough that the completion estimate over it is not
	// dominated by the spread of the output-length distribution.
	DefaultFluidserveHorizonSteps = 100
	// One-sided 95% bound on the KV expected to be released.
	DefaultFluidserveZSafety = 1.65
	// Margin left in front of a wait deadline, covering one status interval of
	// staleness in the instance state the deadline was computed from.
	DefaultFluidserveTtftSafetyMs = 300

	// Adaptive PD defaults
	DefaultEnableAdaptivePD             = false
	DefaultTpotMigrateOutFloorThreshold = 0.60

	// Filter defaults
	DefaultFailoverDomain           = FailoverDomainInstanceUnit
	DefaultInstanceStalenessSeconds = 60

	// Rescheduling defaults
	DefaultEnableRescheduling               = false
	DefaultReschedulingPolicies             = "decode_load,prefill_failover,decode_failover,neutral_failover"
	DefaultReschedulingIntervalMs           = 500
	DefaultReschedulingDecodeLoadMetric     = SchedulingMetricKVCacheUsageRatioProjected
	DefaultReschedulingDecodeLoadThreshold  = 1.0
	DefaultReschedulingPrefillLoadMetric    = SchedulingMetricKVCacheUsageRatioProjected
	DefaultReschedulingNeutralLoadMetric    = SchedulingMetricKVCacheUsageRatioProjected
	DefaultReschedulingNeutralLoadThreshold = 1.0
	DefaultReschedulingReqSelectOrder       = MigrationReqSelectOrderSR
	DefaultReschedulingReqSelectRule        = MigrationReqSelectRuleToken
	DefaultReschedulingReqSelectValue       = 1024
	DefaultReschedulingLoadBalanceThreshold = 0.7
	DefaultReschedulingLoadBalanceScope     = ReschedulingLoadBalanceScopeCluster

	// Llumlet defaults
	DefaultLlumletGrpcConnectionPoolSize = -1
	DefaultLlumletGrpcTimeoutSeconds     = -1
)

const AttrKeyReservedInferType = "ReservedInferType"
