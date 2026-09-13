package config

import (
	"strings"
	"time"

	"github.com/spf13/pflag"
	"k8s.io/klog/v2"

	"llumnix/pkg/consts"
)

type DiscoveryConfig struct {
	// instead of relying on the active registration of inference instances, the
	// backend services are actively discovered through the scheduler, which can
	// only be used in the scenario where BackendService are set
	LLMBackendDiscovery string
	SchedulerDiscovery  string

	RedisDiscoveryConfig
	EtcdDiscoveryConfig
	EndpointDiscoveryConfig
}

func (c *DiscoveryConfig) AddDiscoveryConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.LLMBackendDiscovery, "llm-backend-discovery", "redis", "use redis/etcd/endpoints to discovery backend services.")
	flags.StringVar(&c.SchedulerDiscovery, "scheduler-discovery", "endpoints", "use endpoints to discovery scheduler services.")

	c.RedisDiscoveryConfig.AddRedisDiscoveryConfigFlags(flags)
	c.EtcdDiscoveryConfig.AddEtcdDiscoveryConfigFlags(flags)
	c.EndpointDiscoveryConfig.AddEndpointDiscoveryConfigFlags(flags)
}

type EtcdDiscoveryConfig struct {
	DiscoveryEtcdEndpoints          string
	DiscoveryEtcdUsername           string
	DiscoveryEtcdPassword           string
	DiscoveryEtcdDialTimeout        float64
	DiscoveryEtcdLeaseTTL           int
	DiscoveryEtcdRefreshIntervalSec int
}

func (c *EtcdDiscoveryConfig) AddEtcdDiscoveryConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.DiscoveryEtcdEndpoints, "discovery-etcd-endpoints", "etcd:2379", "etcd discovery endpoints, comma-separated (e.g. etcd-0:2379,etcd-1:2379)")
	flags.StringVar(&c.DiscoveryEtcdUsername, "discovery-etcd-username", "", "etcd discovery username")
	flags.StringVar(&c.DiscoveryEtcdPassword, "discovery-etcd-password", "", "etcd discovery password")
	flags.Float64Var(&c.DiscoveryEtcdDialTimeout, "discovery-etcd-dial-timeout", 5.0, "etcd discovery dial timeout in seconds")
	flags.IntVar(&c.DiscoveryEtcdLeaseTTL, "discovery-etcd-lease-ttl", 60, "etcd discovery lease TTL in seconds")
	flags.IntVar(&c.DiscoveryEtcdRefreshIntervalSec, "discovery-etcd-refresh-interval-sec", 3600, "etcd discovery periodic refresh interval in seconds (safety net for missed Watch events)")
}

type EndpointDiscoveryConfig struct {
	LLMBackendEndpoints string
	SchedulerEndpoints  string
}

func (c *EndpointDiscoveryConfig) AddEndpointDiscoveryConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.LLMBackendEndpoints, "llm-backend-endpoints", "", "backend endpoints, example: 0.0.0.0:8090,0.0.0.0:8091")
	flags.StringVar(&c.SchedulerEndpoints, "scheduler-endpoints", "", "scheduler endpoints, example: 0.0.0.0:8090,0.0.0.0:8091")
}

type RedisDiscoveryConfig struct {
	DiscoveryRedisHost          string
	DiscoveryRedisPort          int
	DiscoveryRedisUsername      string
	DiscoveryRedisPassword      string
	DiscoveryRedisSocketTimeout float64
	DiscoveryRedisRetryTimes    int

	DiscoveryRedisRefreshIntervalMs int
	DiscoveryRedisStatusTTLMs       int
}

func (c *RedisDiscoveryConfig) AddRedisDiscoveryConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.DiscoveryRedisHost, "discovery-redis-host", "redis", "Redis discovery host")
	flags.IntVar(&c.DiscoveryRedisPort, "discovery-redis-port", 6379, "Redis discovery port")
	flags.StringVar(&c.DiscoveryRedisUsername, "discovery-redis-username", "", "Redis discovery username")
	flags.StringVar(&c.DiscoveryRedisPassword, "discovery-redis-password", "", "Redis discovery password")
	flags.Float64Var(&c.DiscoveryRedisSocketTimeout, "discovery-redis-socket-timeout", 1.0, "Redis discovery socket timeout")
	flags.IntVar(&c.DiscoveryRedisRetryTimes, "discovery-redis-retry-times", 1, "Redis discovery retry times")
	flags.IntVar(&c.DiscoveryRedisStatusTTLMs, "discovery-redis-status-ttl-ms", 60000, "Redis discovery status TTL milliseconds")
	flags.IntVar(&c.DiscoveryRedisRefreshIntervalMs, "discovery-redis-refresh-interval-ms", 1000, "Redis discovery refresh interval milliseconds")
}

type ProcessorConfig struct {
	// Tokenizer related configuration
	// builtin tokenizer name
	TokenizerName string
	// self defined tokenizer path, will overwrite the builtin tokenizer name when not empty
	TokenizerPath    string
	ChatTemplatePath string
	// Override model max length; 0 means use the value from tokenizer_config.json.
	// The engine derives max_model_len from config.json, while the gateway reads model_max_length
	// from tokenizer_config.json. These two values can differ, causing the gateway to generate a max_tokens
	// that exceeds the engine's actual limit. This flag allows explicitly aligning the two.
	MaxModelLen uint64

	ToolCallParser  string
	ReasoningParser string
}

func (c *ProcessorConfig) AddProcessorConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.TokenizerName, "tokenizer-name", "", "builtin tokenizer name")
	flags.StringVar(&c.TokenizerPath, "tokenizer-path", "", "builtin tokenizer path")
	flags.StringVar(&c.ChatTemplatePath, "chat-template", "", "chat template path")
	flags.Uint64Var(&c.MaxModelLen, "max-model-len", 0, "override the model_max_length from tokenizer_config.json; 0 means use the value from tokenizer_config.json")
	flags.StringVar(&c.ToolCallParser, "tool-call-parser", "", "tool call parser type")
	flags.StringVar(&c.ReasoningParser, "reasoning-parser", "", "reasoning parser type")
}

type RouteConfig struct {
	RoutePolicy    string
	RouteConfigRaw string

	RetryMaxCount int

	FallbackRetryQueueEnabled bool
	FallbackRetryQueueSize    int
	FallbackRetryWorkerSize   int
	FallbackRetryMaxCount     int
	FallbackRetryInitDelayMs  int
	FallbackRetryMaxDelayMs   int
}

func (c *RouteConfig) AddRouteConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.RoutePolicy, "route-policy", "", "route policy, support weight and prefix")
	flags.StringVar(&c.RouteConfigRaw, "route-config", "", "route config, include api key, base url, weight/prefix and fallback priority")
	flags.IntVar(&c.RetryMaxCount, "retry-max-count", 0, "max retry count for internal routing on retryable errors")
	flags.BoolVar(&c.FallbackRetryQueueEnabled, "fallback-retry-queue-enabled", false, "enable a retry queue that automatically retries fallback requests receiving 429 (Too Many Requests) with exponential backoff")
	flags.IntVar(&c.FallbackRetryQueueSize, "fallback-retry-queue-size", 100, "max number of 429-retry tasks that can be queued; new tasks are dropped when full")
	flags.IntVar(&c.FallbackRetryWorkerSize, "fallback-retry-worker-size", 10, "number of concurrent goroutines processing 429-retry tasks")
	flags.IntVar(&c.FallbackRetryMaxCount, "fallback-retry-max-count", 3, "max number of 429 retries per request before giving up")
	flags.IntVar(&c.FallbackRetryInitDelayMs, "fallback-retry-init-delay-ms", 500, "when a fallback endpoint returns 429, wait this many ms before the first retry; subsequent retries double this delay (exponential backoff)")
	flags.IntVar(&c.FallbackRetryMaxDelayMs, "fallback-retry-max-delay-ms", 5000, "upper bound (ms) for the exponential backoff delay between 429 retries on fallback endpoints")
}

type PDDisaggConfig struct {
	// The configuration of pd disaggregation
	// LLM Gateway currently supports a variety of separate implementations of the prefill
	// and decode phases, which can be distinguished by this configuration
	PDDisaggProtocol string
	// separate scheduling for p and d or not
	SeparatePDScheduling bool
}

func (c *PDDisaggConfig) AddPDDisaggConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.PDDisaggProtocol, "pd-disagg-protocol", "", "pd disaggregation protocol, this configuration only takes effect under the pd-disagg policy, now support vllm-mooncake, vllm-kvt, sglang-mooncake")
	flags.BoolVar(&c.SeparatePDScheduling, "separate-pd-scheduling", false, "Specify whether to separate pd scheduling")
}

type BatchServiceConfig struct {
	BatchOSSPath           string
	BatchOSSEndpoint       string
	BatchParallel          int
	BatchLinesPerShard     int
	BatchRequestTimeout    time.Duration
	BatchRequestRetryTimes int

	BatchServiceRedisAddrs      string
	BatchServiceRedisUsername   string
	BatchServiceRedisPassword   string
	BatchServiceRedisRetryTimes int
}

func (c *BatchServiceConfig) AddBatchServiceConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.BatchOSSPath, "batch-oss-path", "", "OSS path for batch API")
	flags.StringVar(&c.BatchOSSEndpoint, "batch-oss-endpoint", "", "OSS endpoint (default is current region vpc endpoint)")
	flags.StringVar(&c.BatchServiceRedisAddrs, "batch-redis-addrs", "redis.roles:10000", "Redis addresses for batch API")
	flags.StringVar(&c.BatchServiceRedisUsername, "batch-redis-username", "default", "Redis username")
	flags.StringVar(&c.BatchServiceRedisPassword, "batch-redis-password", "default", "Redis password")
	flags.IntVar(&c.BatchServiceRedisRetryTimes, "batch-redis-retry-times", 3, "Redis retry times")

	flags.IntVar(&c.BatchParallel, "batch-parallel", 8, "The parallel of shard process")
	flags.IntVar(&c.BatchLinesPerShard, "batch-lines-per-shard", 1000, "The number of lines per shard file")
	flags.DurationVar(&c.BatchRequestTimeout, "batch-request-timeout", 3*time.Minute, "HTTP request timeout duration")
	flags.IntVar(&c.BatchRequestRetryTimes, "batch-request-retry-times", 3, "HTTP retry times")
}

type SchedulingBaseConfig struct {
	// lite-mode scheduling: When not enabling full mode scheduling (lite-mode scheduling), llumnix does not
	// intrusively modify inference engine, and only support basic load balance scheduling.
	// In lite mode scheduling, LLM gateway collect update realtime request token states and report these data to
	// LLM scheduler periodically. LLM scheduler perform load balance scheduling based on these local realtime states,
	// supporting num-requests and num-tokens scheduling metric.
	// full-mode scheduling: When enable full mode scheduling, llumnix intrusively modifies inference engine to
	// collect accurate load information from inference engine and support kv cache migration, and therefore can
	// support advanced scheduling feature like rescheduling, adaptive pd, etc.
	EnableFullModeScheduling bool

	SchedulingPolicy string
}

func (c *SchedulingBaseConfig) AddSchedulingBaseConfigFlags(flags *pflag.FlagSet) {
	flags.BoolVar(&c.EnableFullModeScheduling, "enable-full-mode-scheduling", consts.DefaultEnableFullModeScheduling, "Enable full mode scheduling")
	flags.StringVar(&c.SchedulingPolicy, "scheduling-policy", "load-balance", "scheduling policy, now support round-robin, load-balance, flood")
}

type LiteModeSchedulingConfig struct {
	// request token state report (report to llm scheduler) interval (seconds)
	RequestStateReportInterval int
}

func (c *LiteModeSchedulingConfig) AddLiteModeSchedulingConfigFlags(flags *pflag.FlagSet) {
	flags.IntVar(&c.RequestStateReportInterval, "requests-report-duration", 0, "Specify requests reporter duration")
}

type FullModeSchedulingConfig struct {
	// cms
	CmsRedisHost              string
	CmsRedisPort              string
	CmsRedisUsername          string
	CmsRedisPassword          string
	CmsRedisSocketTimeout     float64
	CmsRedisRetryTimes        int
	CmsPullStatusIntervalMs   int32
	CmsPullMetadataIntervalMs int32

	// Kvs
	EnableCacheAwareScheduling             bool
	CacheAwareSchedulingMinTokens          int
	KvsBackend                             string
	KvsMetadataServiceConfigPath           string
	KvsChunkSize                           int
	KvsEnableSaveUnfullChunk               bool
	KvsIrisMetaPrefix                      string
	KvsVLLMBlockPrefix                     string
	KvsRetryTimes                          int
	KvsRetryIntervalMs                     int
	KvsMetadataServiceDownDurationS        int
	KvsMetadataServiceRedisClusterHosts    string
	KvsMetadataServiceRedisClusterPassword string
	KvsMetadataServiceHttpServerHost       string
	KvsMetadataServiceHttpServerPort       string
	KvsHashAlgo                            string

	// schedule
	DispatchTopK                        int
	DispatchNeutralLoadMetric           string
	DispatchNeutralLoadThreshold        float32
	DispatchPrefillLoadMetric           string
	DispatchPrefillLoadThreshold        float32
	DispatchDecodeLoadMetric            string
	DispatchDecodeLoadThreshold         float32
	DispatchPrefillCacheLocalityMetric  string
	AdmissionKvUsageThreshold           float32
	EnableInstanceStatusLocalAccount    bool
	RequestLocalAccountStalenessSeconds int32
	AllowConcurrentScheduling           bool
	EnablePredictorEnhancedScheduling   bool
	MaxNumBatchedTokens                 int
	NumPredictorWarmupSamples           int

	// Slo
	TtftProfilingDataPath       string
	TpotProfilingDataPath       string
	TtftSlo                     float32
	TpotSlo                     float32
	TtftSloDispatchThreshold    float32
	TpotSloDispatchThreshold    float32
	TpotMigrateOutCeilThreshold float32

	// PolyServe
	PolyserveTierDecodeTokens string
	PolyserveDecodeTokens     int
	// The five switches below each restore one mechanism of the paper that the
	// first port left out or inverted. Every one of them defaults to the port's
	// previous behaviour, so a run that does not name them decides exactly as
	// every PolyServe run before 2026-08-27 did.
	PolyserveAdmissionBinds         bool
	PolyserveSteadyStateIgnoresPref bool
	PolyserveLazyPromotion          bool
	PolyservePartition              string
	PolyservePreferLoaded           bool
	PolyserveKvAdmission            bool

	// FluidServe
	FluidserveProfilePath            string
	FluidserveClassBudgets           string
	FluidserveHorizonSteps           int
	FluidserveChargeHorizon          int
	FluidserveZSafety                float64
	FluidserveTtftSafetyMs           int
	FluidserveEnablePend             bool
	FluidserveEnableShed             bool
	FluidserveShedSignal             string
	FluidserveOracleLength           bool
	FluidserveEnableAffinity         bool
	FluidserveAffinityWeight         float64
	FluidserveAffinityMetric         string
	FluidservePerInstanceCorrection  bool
	FluidserveMemoryUsesPaceCap      bool
	FluidservePerInstanceDelay       bool
	FluidserveDeadlineUsesDelay      bool
	FluidserveShedIgnoresFirstToken  bool
	FluidservePrefillInterleaveAware bool
	FluidserveClassPin               string
	FluidserveEnableFlux             bool
	FluidserveClassHarm              bool
	FluidserveForceMargin            bool
	FluidserveOwnBudgetGate          bool
	FluidserveIncumbentBalance       bool
	FluidserveOracleFlux             bool
	FluidserveDeadlineFeasible       bool
	FluidserveMemLevelTest           bool
	FluidserveMemorySafety           float64
	FluidservePrefillFullIteration   bool
	FluidservePrefillResidentQueue   bool
	FluidserveArrivalHorizons        int
	FluidservePrefixAware            bool
	FluidservePrefixCalibration      bool
	FluidservePrefixBlockTokens      int
	FluidservePrefixCapacity         int
	FluidserveKvSlopeProjection      bool
	FluidserveGateSlack              float64

	// EXP-107
	FluidserveClassInstanceCap           bool
	FluidserveClassInstanceCapWindowMult float64
	FluidserveCapCostsCapacity           bool
	FluidserveEnableForce                bool

	// The vLLM router cache_aware baseline. Defaults are that package's, not the
	// SGLang original's -- see the note on VllmCacheThreshold below.
	VllmCacheThreshold    float64
	VllmCacheBalanceAbs   int
	VllmCacheBalanceRel   float64
	VllmCacheEvictionSecs int
	VllmCacheMaxTreeSize  int

	// Adaptive PD
	EnableAdaptivePD             bool
	TpotMigrateOutFloorThreshold float32

	// filter
	FailoverDomain           string
	InstanceStalenessSeconds int64

	// rescheduling
	EnableRescheduling               bool
	ReschedulingPolicies             string
	ReschedulingIntervalMs           int32
	ReschedulingDecodeLoadMetric     string
	ReschedulingDecodeLoadThreshold  float32
	ReschedulingPrefillLoadMetric    string
	ReschedulingNeutralLoadMetric    string
	ReschedulingNeutralLoadThreshold float32
	ReschedulingReqSelectOrder       string
	ReschedulingReqSelectRule        string
	ReschedulingReqSelectValue       float32
	ReschedulingLoadBalanceThreshold float32
	ReschedulingLoadBalanceScope     string

	// llumlet
	LlumletGrpcConnectionPoolSize int
	LlumletGrpcTimeoutSeconds     int
}

func (c *FullModeSchedulingConfig) AddFullModeSchedulingConfigFlags(flags *pflag.FlagSet) {
	flags.StringVar(&c.CmsRedisHost, "cms-redis-host", consts.DefaultCmsRedisHost, "Llumnix CMS redis host")
	flags.StringVar(&c.CmsRedisPort, "cms-redis-port", consts.DefaultCmsRedisPort, "Llumnix CMS redis port")
	flags.StringVar(&c.CmsRedisUsername, "cms-redis-username", consts.DefaultCmsRedisUsername, "Llumnix CMS redis username")
	flags.StringVar(&c.CmsRedisPassword, "cms-redis-password", consts.DefaultCmsRedisPassword, "Llumnix CMS redis password")
	flags.Float64Var(&c.CmsRedisSocketTimeout, "cms-redis-timeout", consts.DefaultCmsRedisSocketTimeout, "Llumnix CMS redis socket timeout")
	flags.IntVar(&c.CmsRedisRetryTimes, "cms-redis-retry-times", consts.DefaultCmsRedisRetryTimes, "Llumnix CMS redis retry times")
	flags.Int32Var(&c.CmsPullStatusIntervalMs, "cms-pull-status-interval-ms", consts.DefaultCmsPullStatusIntervalMs, "Llumnix CMS pull status interval in milliseconds")
	flags.Int32Var(&c.CmsPullMetadataIntervalMs, "cms-pull-metadata-interval-ms", consts.DefaultCmsPullMetadataIntervalMs, "Llumnix CMS pull metadata interval in milliseconds")

	flags.BoolVar(&c.EnableCacheAwareScheduling, "enable-cache-aware-scheduling", consts.DefaultEnableCacheAwareScheduling, "Llumnix enable cache aware scheduling")
	flags.IntVar(&c.CacheAwareSchedulingMinTokens, "cache-aware-scheduling-min-tokens", consts.DefaultCacheAwareSchedulingMinTokens, "Llumnix cache aware scheduling min tokens")
	flags.StringVar(&c.KvsBackend, "kvs-backend", consts.DefaultKvsBackend, "Llumnix KVS backend")
	flags.StringVar(&c.KvsMetadataServiceConfigPath, "kvs-metadata-service-config-path", consts.DefaultKvsMetadataServiceConfigPath, "Llumnix KVS MetadataService config path")
	flags.StringVar(&c.KvsHashAlgo, "kvs-hash-algo", consts.DefaultKvsHashAlgo, "Llumnix KVS hash algo")
	flags.IntVar(&c.KvsChunkSize, "kvs-chunk-size", consts.DefaultKvsChunkSize, "Llumnix KVS chunk size")
	flags.BoolVar(&c.KvsEnableSaveUnfullChunk, "kvs-enable-save-unfull-chunk", consts.DefaultKvsEnableSaveUnfullChunk, "Llumnix KVS enable save unfull chunk")
	flags.StringVar(&c.KvsIrisMetaPrefix, "kvs-iris-meta-prefix", consts.DefaultKvsIrisMetaPrefix, "Llumnix KVS iris meta prefix")
	flags.StringVar(&c.KvsVLLMBlockPrefix, "kvs-vllm-block-prefix", consts.DefaultKvsVLLMBlockPrefix, "Llumnix KVS vllm block prefix")
	flags.IntVar(&c.KvsRetryIntervalMs, "kvs-retry-interval-ms", consts.DefaultKvsRetryIntervalMs, "Llumnix KVS retry interval in milliseconds")
	flags.IntVar(&c.KvsRetryTimes, "kvs-retry-times", consts.DefaultKvsRetryTimes, "Llumnix KVS retry times")
	flags.IntVar(&c.KvsMetadataServiceDownDurationS, "kvs-metadata-service-down-duration-s", consts.DefaultKvsMetadataServiceDownDurationS, "Llumnix KVS metadata service down duration in seconds")
	flags.StringVar(&c.KvsMetadataServiceRedisClusterHosts, "kvs-metadata-service-redis-cluster-hosts", consts.DefaultKvsMetadataServiceRedisClusterHosts, "Llumnix KVS metadata service redis cluster hosts")
	flags.StringVar(&c.KvsMetadataServiceRedisClusterPassword, "kvs-metadata-service-redis-cluster-password", consts.DefaultKvsMetadataServiceRedisClusterPassword, "Llumnix KVS metadata service redis cluster password")
	flags.StringVar(&c.KvsMetadataServiceHttpServerHost, "kvs-metadata-service-http-server-host", consts.DefaultKvsMetadataServiceHttpServerHost, "Llumnix KVS metadata service http server host")
	flags.StringVar(&c.KvsMetadataServiceHttpServerPort, "kvs-metadata-service-http-server-port", consts.DefaultKvsMetadataServiceHttpServerPort, "Llumnix KVS metadata service http server port")

	flags.IntVar(&c.DispatchTopK, "dispatch-top-k", consts.DefaultDispatchTopK, "Llumnix dispatch top K")
	flags.StringVar(&c.DispatchNeutralLoadMetric, "dispatch-neutral-load-metric", consts.DefaultDispatchNeutralLoadMetric, "Llumnix dispatch neutral load metric")
	flags.Float32Var(&c.DispatchNeutralLoadThreshold, "dispatch-neutral-load-threshold", consts.DefaultDispatchNeutralLoadThreshold, "Llumnix dispatch neutral load threshold")
	flags.StringVar(&c.DispatchPrefillLoadMetric, "dispatch-prefill-load-metric", consts.DefaultDispatchPrefillLoadMetric, "Llumnix dispatch prefill load metric")
	flags.Float32Var(&c.DispatchPrefillLoadThreshold, "dispatch-prefill-load-threshold", consts.DefaultDispatchPrefillLoadThreshold, "Llumnix dispatch prefill load threshold")
	flags.StringVar(&c.DispatchDecodeLoadMetric, "dispatch-decode-load-metric", consts.DefaultDispatchDecodeLoadMetric, "Llumnix dispatch decode load metric")
	flags.Float32Var(&c.DispatchDecodeLoadThreshold, "dispatch-decode-load-threshold", consts.DefaultDispatchDecodeLoadThreshold, "Llumnix dispatch decode load threshold")
	flags.StringVar(&c.DispatchPrefillCacheLocalityMetric, "dispatch-prefill-cache-locality-metric", consts.DefaultDispatchPrefillCacheLocalityMetric, "Llumnix dispatch prefill cache locality metric")
	flags.Float32Var(&c.AdmissionKvUsageThreshold, "admission-kv-usage-threshold", 0, "if > 0, add a hard per-instance admission filter on neutral dispatch: an instance is ineligible while its hot KV cache usage ratio (kv_cache_usage_ratio, 0-1) is >= this threshold, and the filter is NOT skipped on fallback, so when all instances exceed it the scheduler returns 429 (no available endpoint). 0 disables (stock behavior)")
	flags.BoolVar(&c.EnableInstanceStatusLocalAccount, "enable-instance-status-local-account", consts.DefaultEnableInstanceStatusLocalAccount, "Llumnix enable instance status local account")
	flags.Int32Var(&c.RequestLocalAccountStalenessSeconds, "request-local-account-staleness-seconds", consts.DefaultRequestLocalAccountStalenessSeconds, "Llumnix request local account staleness seconds")
	flags.BoolVar(&c.AllowConcurrentScheduling, "allow-concurrent-scheduling", consts.DefaultAllowConcurrentScheduling, "Llumnix allow concurrent scheduling")
	flags.BoolVar(&c.EnablePredictorEnhancedScheduling, "enable-predictor-enhanced-scheduling", consts.DefaultEnablePredictorEnhancedScheduling, "Llumnix enable predictor enhanced scheduling")
	flags.IntVar(&c.MaxNumBatchedTokens, "max-num-batched-tokens", consts.DefaultMaxNumBatchedTokens, "Llumnix max num batched tokens")
	flags.IntVar(&c.NumPredictorWarmupSamples, "num-predictor-warmup-samples", consts.DefaultNumPredictorWarmupSamples, "Llumnix num predictor warmup samples")

	flags.StringVar(&c.TtftProfilingDataPath, "ttft-profiling-data-path", "", "Llumnix ttft profiling data path")
	flags.Float32Var(&c.TtftSlo, "ttft-slo", consts.DefaultTtftSlo, "Llumnix ttft slo")
	flags.Float32Var(&c.TtftSloDispatchThreshold, "ttft-slo-dispatch-threshold", consts.DefaultTtftSloDispatchThreshold, "Llumnix ttft slo dispatch threshold")
	flags.StringVar(&c.TpotProfilingDataPath, "tpot-profiling-data-path", "", "Llumnix tpot profiling data path")
	flags.Float32Var(&c.TpotSlo, "tpot-slo", consts.DefaultTpotSlo, "Llumnix tpot slo")
	flags.Float32Var(&c.TpotSloDispatchThreshold, "tpot-slo-dispatch-threshold", consts.DefaultTpotSloDispatchThreshold, "Llumnix tpot slo dispatch threshold")
	flags.Float32Var(&c.TpotMigrateOutCeilThreshold, "tpot-migrate-out-ceil-threshold", consts.DefaultTpotMigrateOutCeilThreshold, "Llumnix tpot migrate out ceil threshold")

	flags.StringVar(&c.PolyserveTierDecodeTokens, "polyserve-tier-decode-tokens", "",
		"PolyServe expected output length per SLO tier, as \"tpotSloMs:tokens,...\" "+
			"(e.g. \"25:728,50:386,100:275\"). Section 4.5 admits on the largest KV a batch "+
			"will reach, which needs an output length; the paper does not predict it either "+
			"and uses the average decode length, so these are measured per-class means. "+
			"Tiers not listed fall back to --polyserve-decode-tokens.")
	flags.IntVar(&c.PolyserveDecodeTokens, "polyserve-decode-tokens", consts.DefaultPolyserveDecodeTokens,
		"PolyServe expected output length for tiers absent from --polyserve-tier-decode-tokens")

	flags.BoolVar(&c.PolyserveAdmissionBinds, "polyserve-admission-binds", false,
		"Let the section 4.5/4.6 admission test decide. With this off, Llumnix's second "+
			"scheduling pass drops the admission filter when no server passes it, so the "+
			"request is placed on the least loaded server of its tier and admission changes "+
			"no outcome at all -- which is why this arm rejected 0.0% of requests. With it "+
			"on, a request no server admits is held at the gateway (the paper's pending "+
			"queue, section 4.6) and is refused once its own TTFT budget can no longer be "+
			"met however long it waits.")
	flags.BoolVar(&c.PolyserveSteadyStateIgnoresPref, "polyserve-steady-state-ignores-prefill", false,
		"Keep the queued prefill chunk out of the steady-state iteration estimate, as "+
			"section 4.5 does (\"PolyServe only considers batch size and KV cache size\"). "+
			"The near-term estimate keeps it, because the wait time section 4.6 asks about "+
			"IS that chunk on a co-located server. With this off both estimates carry it, "+
			"and since a full 8,192-token chunk costs 520-634 ms against tier budgets of "+
			"25-100 ms, no server passes admission whenever any prefill is queued.")
	flags.BoolVar(&c.PolyserveLazyPromotion, "polyserve-lazy-promotion", false,
		"Section 4.4: when every server of a request's own tier refuses it, offer the "+
			"request to servers of a TIGHTER tier before growing the tier. The guest is "+
			"judged against the HOST tier's per-token budget rather than its own, which is "+
			"what makes the paper's claim that a looser guest is harmless true rather than "+
			"assumed.")
	flags.StringVar(&c.PolyservePartition, "polyserve-partition", "demand",
		"How servers are divided between tiers. \"demand\" recomputes the split every 10 s "+
			"from arrival rate times a profiled per-request cost, which is this port's own "+
			"invention -- the paper computes no demand at all. \"elastic\" follows section "+
			"4.3: servers sit in an idle pool, a tier takes one when its requests start "+
			"pending, and gives one back when the last server of that tier runs empty.")
	flags.BoolVar(&c.PolyservePreferLoaded, "polyserve-prefer-loaded", false,
		"Among the servers that pass admission, pick the most loaded rather than the "+
			"least loaded (section 4.3). This is not a preference: it is what leaves the "+
			"last server of a tier empty, and an empty last server is the only signal the "+
			"paper uses to return a server to the idle pool. With least-loaded selection "+
			"every server stays partly full and --polyserve-partition=elastic can never "+
			"release one.")
	flags.BoolVar(&c.PolyserveKvAdmission, "polyserve-kv-admission", false,
		"Refuse a server whose KV cache cannot hold the batch section 4.5 simulates "+
			"forward to. The port has no memory predicate at all, so the fleet is driven "+
			"past its KV capacity and vLLM recovers by preempting and recomputing. The "+
			"comparison is against the instance's reported total GPU token capacity, so "+
			"this introduces no threshold to choose.")

	flags.StringVar(&c.FluidserveProfilePath, "fluidserve-profile-path", "",
		"FluidServe offline profile (decode step law + per-class output-length "+
			"survival), produced by ms_dev/scripts/gen_fluidserve_profile.py")
	flags.StringVar(&c.FluidserveClassBudgets, "fluidserve-class-budgets", "",
		"How each SLO tier's latency budget is defined, as \"tier:mode[:budgetMs],...\" "+
			"(e.g. \"25:e2e:30000,50:decode,100:decode\"). e2e means the whole request "+
			"must finish within the given wall-clock budget measured from arrival; decode "+
			"means its mean time between output tokens must stay within the tier key. "+
			"Both are cumulative, which is how the requests are actually scored, so a "+
			"request that has been faster than its budget carries the credit forward.")
	flags.IntVar(&c.FluidserveHorizonSteps, "fluidserve-horizon-steps",
		consts.DefaultFluidserveHorizonSteps,
		"Planning horizon in engine iterations. Counted in iterations rather than "+
			"seconds because decode growth is then exactly one token per request per "+
			"iteration, and because iteration time is itself a function of the occupancy "+
			"being controlled.")
	flags.IntVar(&c.FluidserveChargeHorizon, "fluidserve-charge-horizon", 0,
		"Iterations of future decode an ARRIVING request is charged for, when that "+
			"has to differ from --fluidserve-horizon-steps. Zero, the default, means "+
			"follow the planning horizon, which reproduces every measurement taken "+
			"before 2026-09-14.\n"+
			"Why it exists. The planning horizon enters the policy in three places and "+
			"they are not the same mechanism. It is the window over which departures "+
			"are credited (expectedOutflow), it is the number of iterations the "+
			"arriving request is charged for (costOf), and it is the denominator of "+
			"the prefill fraction in the pace estimate, meanStepMs, which returns "+
			"corr*((k-sp)*dec + sp*(pre+dec-c0))/k with sp clamped to k. Because the "+
			"pace is evaluated with the arriving request's own prompt already in "+
			"pendingPrefill, sp is at least one at every decision, so a horizon of 1 "+
			"makes that expression the cost of a whole prefill iteration for every "+
			"candidate and the gate refuses nearly everything. EXP-133's last "+
			"cumulative step moved the horizon to 1 intending to remove only the "+
			"charge, and what it actually measured was mostly that saturation: "+
			"attainment 40.0 with 59.9% refused. Setting this to 1 and leaving the "+
			"planning horizon at its deployed value removes the charge and nothing "+
			"else, which is the ablation that step was supposed to be.")
	flags.Float64Var(&c.FluidserveZSafety, "fluidserve-z-safety",
		consts.DefaultFluidserveZSafety,
		"Standard deviations subtracted from the expected KV release. Larger values "+
			"admit less and leave more headroom.")
	flags.IntVar(&c.FluidserveTtftSafetyMs, "fluidserve-ttft-safety-ms",
		consts.DefaultFluidserveTtftSafetyMs,
		"Margin subtracted from the time-to-first-token budget when deciding how long "+
			"a request may keep waiting")
	flags.BoolVar(&c.FluidserveEnablePend, "fluidserve-enable-pend", true,
		"Hold a request at the gateway when no instance can take it within budget. "+
			"Disabling it forces an immediate placement, which is the ablation that "+
			"isolates what deferring the binding is worth.")
	flags.BoolVar(&c.FluidserveEnableShed, "fluidserve-enable-shed", true,
		"Reject a request once no instance can serve it within its own budget and "+
			"it can no longer afford to wait. Disabling it places such requests "+
			"anyway, which is the ablation that isolates what informed rejection is "+
			"worth: the same requests are lost either way, and the question is "+
			"whether the capacity they would have consumed saves the ones around "+
			"them.")
	// EXP-79. The pre-registered "independent combination" row of the ablation
	// matrix in fluidserve-design.md section 6.3, which that document calls the
	// paper's core experiment: routing by the flux model AND shedding by the flux
	// model, but with the two not sharing anything.
	//
	// Today they share the placement. The shed test asks whether the instance the
	// request is ABOUT TO BE PLACED ON would still miss its budget, so the refusal
	// is a statement about a specific destination. `fleet` asks the same question
	// of the fleet as a whole -- the request is priced against the average of what
	// the instances would give it, with no reference to which one was chosen -- so
	// both halves still use the flux model and neither knows the other's answer.
	//
	//   coupled        the shipped behaviour: shed when the chosen placement misses
	//   fleet[:scale]  shed when the request would miss against the mean of the
	//                  candidates. scale multiplies the budget the test compares
	//                  against, above 1 refusing less and below 1 refusing more, so
	//                  the arm's rejection rate can be matched to the control's.
	//                  Matching it is what closes the objection that the score came
	//                  from choosing a convenient amount to refuse.
	flags.StringVar(&c.FluidserveShedSignal, "fluidserve-shed-signal", "coupled",
		"Which quantity the shed test reads: `coupled` (the placement about to be "+
			"made, shipped) or `fleet[:scale]` (the mean over candidates, which is the "+
			"independent-combination ablation). Ignored when --fluidserve-enable-shed=false.")

	// EXP-64. Read the client's per-request output-length hint instead of the
	// class's length distribution, for the arriving request AND for the requests
	// already resident on each instance -- the feasibility test is about what the
	// incumbents still have to produce, so using the hint for only the arrival
	// would measure a third thing.
	//
	// Default false, so the control arm is the same policy every experiment since
	// EXP-27 measured, and a request with no parseable hint falls back to the
	// class distribution rather than to zero.
	flags.BoolVar(&c.FluidserveOracleLength, "fluidserve-oracle-length", false,
		"Use the per-request output-length hint from the OpenAI user field "+
			"(len:<tokens>) in place of the class length distribution. EXP-64: this "+
			"bounds what a finer-grained length predictor could be worth.")
	flags.BoolVar(&c.FluidserveOracleFlux, "fluidserve-oracle-flux", false,
		"Let the per-request length hint drive the FLEET projection too, not "+
			"only the arriving request. --fluidserve-oracle-length replaces three "+
			"quantities -- what an arrival is charged, whether it can still meet "+
			"its deadline, and how many tokens a resident still owes -- but the "+
			"two flux terms keep their old sources: outflow asks the class "+
			"survival curve how likely each resident is to finish inside the "+
			"horizon, and inflow charges every decoding request the whole horizon "+
			"whether or not it will still be there. With a hint both become facts: "+
			"a resident finishes inside the horizon exactly when its remaining "+
			"count fits, and it generates min(remaining, horizon) rather than the "+
			"horizon. The first also zeroes that request's term in the variance "+
			"the z-safety margin comes from, because perfect information needs no "+
			"margin. Kept separate from --fluidserve-oracle-length so EXP-64's "+
			"narrower meaning stays measurable; residents without a hint keep the "+
			"class curve and the flat charge.")

	flags.BoolVar(&c.FluidserveEnableAffinity, "fluidserve-enable-affinity", true,
		"Among the instances that can take a request, prefer the one already "+
			"holding the most of its class. Disabling it routes purely by free "+
			"space, which is the ablation that isolates where the class separation "+
			"comes from.")
	flags.Float64Var(&c.FluidserveAffinityWeight, "fluidserve-affinity-weight", 1.0,
		"How strongly the class preference counts against free space when ordering "+
			"the instances that can take a request. At 1.0 the instance holding the "+
			"most of the request's class wins and free space only breaks ties, which "+
			"is the behaviour every experiment before EXP-58 measured. At 0.0 the "+
			"preference contributes nothing and the ordering is by free space alone, "+
			"which is what --fluidserve-enable-affinity=false produces. Values in "+
			"between trade the two off continuously, which is what makes the degree "+
			"of class separation an axis that can be swept rather than a switch. "+
			"Ignored when --fluidserve-enable-affinity=false.")
	flags.BoolVar(&c.FluidservePerInstanceCorrection,
		"fluidserve-per-instance-correction", true,
		"Keep the measured-against-predicted correction of the iteration-time "+
			"model per instance instead of as one scalar for the fleet. The "+
			"correction multiplies every term of the predicted mean, and the "+
			"prediction is what three of the four feasibility conditions read. "+
			"Measured on the mix-shift hour trace, the engines holding chat run at "+
			"1.07-1.15 times their prediction while the engines dedicated to deep "+
			"research run at 0.76-0.96, so a single coefficient averages two errors "+
			"of opposite sign to a fleet mean of 0.975-0.997 and is wrong by -24% to "+
			"+15% on every individual instance. Off by default. The cost of splitting "+
			"it is fourfold fewer samples per coefficient and a tighter feedback loop, "+
			"since a per-instance factor feeds back into that instance's own placement "+
			"rather than being diluted across four. ON by default since v0.3, on "+
			"EXP-98: two repeats of the mix-shift hour moved the per-request "+
			"admitted score from 68.9 to 75.8 with this alone.")
	flags.BoolVar(&c.FluidserveMemoryUsesPaceCap,
		"fluidserve-memory-uses-pace-cap", true,
		"Test the memory condition against min(capKv, capMem) instead of capMem "+
			"alone. capMem is the physical pool and reduces to 0.95 x logical "+
			"occupancy over physical utilisation, so it equals the current occupancy "+
			"at 95% utilisation and refuses nothing below that -- by which point "+
			"preemption is imminent. capKv, the occupancy at which the instance still "+
			"meets the pace promised to it, is already computed and already includes "+
			"the queued prefill, but reaches only the sort's free-space term, whose "+
			"weight is zero at an affinity weight of 1.0. Off by default. An instance "+
			"with no live request has an infinite allowance and therefore an infinite "+
			"capKv, so the minimum falls back to capMem there in either mode. ON by "+
			"default since v0.3, as the other half of EXP-98's pair: alone it is "+
			"worse than nothing, 67.8 against 68.9, because a wrong correction "+
			"makes a wrong ceiling, and together with the per-instance correction "+
			"it reads 76.2.")
	flags.BoolVar(&c.FluidservePerInstanceDelay,
		"fluidserve-per-instance-delay", false,
		"Keep the queueing delay between a dispatch and its first token per "+
			"instance instead of as one scalar for the fleet. That delay is the "+
			"residual between what the decision predicted the prefill would cost "+
			"and what the request realised, and it is added to the first-token "+
			"budget on the holding path to decide whether a request can wait. "+
			"Measured on the mix-shift hour trace, its p90 over the four instances "+
			"is 1,265 / 1,520 / 2,564 / 20,022 ms, so a fleet scalar describes none "+
			"of them and understates by an order of magnitude exactly the instance "+
			"that is backlogged. Off by default. An instance with fewer than "+
			"fsMinPlacementDelaySamples samples of its own falls back to the fleet "+
			"value rather than to the fixed margin, so a newly restarted instance "+
			"is not treated as though it had no queue.")
	flags.BoolVar(&c.FluidserveDeadlineUsesDelay,
		"fluidserve-deadline-uses-delay", false,
		"Add the measured queueing delay to the first-token deadline test. The "+
			"test asks whether waited + prefillMs exceeds the time-to-first-token "+
			"budget, and prefillMs is the work rather than the wait: it prices "+
			"this prompt's own prefill given the queue, computed as though the "+
			"engine did nothing else, while the backlogged instance spends 45.3% "+
			"of its steps on prefill and interleaves decode with the rest. The "+
			"same bound is already added on the holding path in canWait, so "+
			"without this flag the two paths ask the same question with different "+
			"quantities. Making the test a feasibility condition WITHOUT this "+
			"refused 60,440 and 61,402 candidates over two repeats, 4.4% and 4.6% "+
			"of all refusals, and changed admitted attainment by 0.1 points, "+
			"because what it refused was already refused by another condition. "+
			"Whether adding the delay is the right direction is NOT settled: on "+
			"the 9-minute flat-rate trace the estimate summed to 1,640 ms per "+
			"placement against 334 ms realised, so it over-predicts by 4.9 times "+
			"and adding to it would refuse more rather than better. Off by "+
			"default. The test is read by the shed path and, when "+
			"--fluidserve-deadline-feasible is set, by the feasibility "+
			"conjunction.")
	flags.BoolVar(&c.FluidserveShedIgnoresFirstToken,
		"fluidserve-shed-ignores-first-token", false,
		"Take the first-token branch out of the shed test, leaving it to judge on "+
			"per-token pace alone. The shed test asks whether the placement that "+
			"would actually be made still meets the request's own budget, and for a "+
			"class judged on a per-token budget that is the first-token branch OR "+
			"the pace branch. For deep research the pace branch never fires -- over "+
			"both EXP-100 repeats 0.0% of its completed requests exceeded its 100 ms "+
			"per-token budget -- so its 30.0% and 29.6% rejection runs entirely "+
			"through the first-token estimate, which on the 9-minute flat-rate trace "+
			"over-predicts the post-dispatch time by 4.9 times. This does NOT turn "+
			"shedding off: chat and the agent class keep being judged on the branch "+
			"that fires for them, which is what separates this from the shed-off "+
			"ablation. Off by default.")
	flags.BoolVar(&c.FluidservePrefillInterleaveAware,
		"fluidserve-prefill-interleave-aware", true,
		"Stretch the queue term of the first-token estimate by the engine's "+
			"measured prefill duty cycle, the share of its time it actually "+
			"spends on prefill. The estimate prices prefill STEP time and nothing "+
			"else, so it answers how much engine prefill time the work costs "+
			"rather than when the request's first token appears, and between "+
			"those sits the decode the engine interleaves. The per-token half of "+
			"this policy already models that mixing, so the two halves were "+
			"describing the same engine differently. Measured over the 15,219 "+
			"deep research placements of one mix-shift hour, realised over "+
			"predicted is 0.59 at the median and 1.99 at p90 and the ratio of the "+
			"two p90s is 2.06, against a measured duty of 0.43 to 0.46 on an "+
			"instance carrying a prefill queue over 10,000 tokens, whose "+
			"reciprocal is 2.2 to 2.3: the shortfall is in the tail, which is "+
			"where the misses are. Only the observed queue is stretched -- the "+
			"duty-derived arriving term already carries the duty as a factor, so "+
			"dividing it would return the horizon -- and the request's own prefill "+
			"is not stretched, because chat already over-predicts twofold. ON by default "+
			"since v0.3, on EXP-103: two repeats of the mix-shift hour take deep "+
			"research's admitted attainment from 83.7 to 96.5 and 94.7 and its "+
			"offered attainment from 60.7 to 68.1 and 66.0, at a rejection rate "+
			"and a token goodput inside the four control repeats' own range.")
	flags.StringVar(&c.FluidserveAffinityMetric, "fluidserve-affinity-metric", "count",
		"What \"most of this class\" means when the class preference orders the "+
			"feasible instances. `share` is the fraction of THAT INSTANCE's requests "+
			"belonging to the class and is the shipped behaviour; because it is a "+
			"ratio it saturates at 1.0, so every instance the class already "+
			"dominates scores identically, the comparator falls through to the "+
			"free-space tie-break, and that prefers the EMPTIER instance -- which "+
			"spreads the class over the instances it has taken instead of filling "+
			"one, the opposite of what sortCandidates documents. Reconstructed on "+
			"the mix-shift trace, the top share was exactly tied on 45.7% of chat "+
			"placements in the 93%-chat segment against 1.2% when the same instants "+
			"are ranked by count. `count` divides by the LARGEST count among the "+
			"candidates instead, so the score no longer saturates while staying in "+
			"0..1 and therefore commensurable with free space; at weight 1 it orders "+
			"by how many of the class an instance holds, so the fullest keeps "+
			"winning until the feasibility test stops it.")
	// On by default since v0.2. EXP-69 measured the control and the treatment
	// with this as the only difference, two repeats at each of three rates, and
	// every metric moved the same way: offered attainment 64.2 -> 77.6, 49.3 ->
	// 59.4 and 38.0 -> 48.3 at 35, 45 and 55 req/s, token goodput +13.9 to
	// +21.7%, rejection 25.3 -> 16.0, 43.2 -> 35.4 and 53.7 -> 46.3%, and the
	// admitted denominator up as well. The gains are far outside the repeat
	// spread, which is 0.1 to 4.9 points. The cost is measured rather than
	// estimated: hashing a prompt is 5.2 to 50.7 microseconds and a lookup is
	// 0.42 to 4.5, so a request evaluating four instances pays about 70 in the
	// worst case.
	//
	// What it does is charge prefill per instance, which makes the feasibility
	// test stop refusing instances that could have taken the request. It is NOT
	// prefix-aware routing: the engine's own cache hit rate is slightly LOWER
	// with this on at 45 and 55 req/s, and prefix enters neither term of the
	// candidate sort score. See EXP-69 section 3.2.
	//
	// Turning it off reproduces every arm measured before EXP-67. The driver
	// arms named `fluidserve` set it off explicitly for that reason, so that a
	// results directory carrying that arm name means the same configuration
	// whenever it was produced.
	flags.BoolVar(&c.FluidservePrefixAware, "fluidserve-prefix-aware", true,
		"Charge an arriving prompt only for the blocks the instance under "+
			"consideration is not already believed to hold, instead of for a "+
			"fleet-wide fraction of it. The scheduler keeps its own index of which "+
			"instance each block of prompt was last dispatched to; it does not query "+
			"the engines, so it cannot see eviction, and the error that causes is "+
			"measured by the calibration below. On by default since v0.2; set it "+
			"false to reproduce every arm measured before EXP-67. See "+
			"ms_dev/notes/fluidserve-prefix.md.")
	flags.BoolVar(&c.FluidservePrefixCalibration, "fluidserve-prefix-calibration", true,
		"Multiply the prefix-aware charge by the measured ratio between what the "+
			"engines actually computed and what this scheduler predicted they would. "+
			"With it on, an index that claims more cache hits than the engines have "+
			"is corrected within about a minute; with it off the charge is taken at "+
			"face value, which is the ablation that shows what the correction is "+
			"worth. Ignored when --fluidserve-prefix-aware=false.")
	flags.IntVar(&c.FluidservePrefixBlockTokens, "fluidserve-prefix-block-tokens", 16,
		"Tokens per block in the prefix index. Matching the engine's own KV block "+
			"size makes a claimed hit correspond to something the engine can actually "+
			"reuse; a larger value costs less to hash and loses up to one block of "+
			"precision at the end of the matched run, in the direction of charging "+
			"more. Ignored when --fluidserve-prefix-aware=false.")
	flags.IntVar(&c.FluidservePrefixCapacity, "fluidserve-prefix-capacity", 500000,
		"Blocks the prefix index keeps before evicting the least recently used. "+
			"The working set is set by the amount of DISTINCT prompt content in "+
			"flight, not by the request rate: one eight-minute condition at 70 req/s "+
			"carried 33,660 requests and 56.7M prompt tokens but only 4.73M distinct "+
			"tokens, which is 0.30M blocks at the default block size. Ignored when "+
			"--fluidserve-prefix-aware=false.")
	flags.StringVar(&c.FluidserveClassPin, "fluidserve-class-pin", "",
		"Restrict each class to a fixed set of instances, as "+
			"\"50:0;100:1,2;25:3\": the class whose per-token budget tier is 50 may "+
			"only be placed on the first instance in the sorted list of instance "+
			"ids, the tier-100 class on the second and third, and so on. Empty, the "+
			"default, means no restriction. Nothing else changes: the same capacity "+
			"model, budgets, four-way ladder and ordering run over whatever survives "+
			"the filter, and a request whose instances cannot take it is held or "+
			"rejected rather than placed elsewhere. This exists so that pinning "+
			"classes to servers can be measured as an ablation of this policy "+
			"instead of only as a comparison against a different system.")
	flags.BoolVar(&c.FluidserveEnableFlux, "fluidserve-enable-flux", true,
		"Project occupancy over the horizon. Disabling it judges instances on their "+
			"current occupancy alone, which is the level-based baseline.")
	flags.BoolVar(&c.FluidserveForceMargin, "fluidserve-force-margin", false,
		"Apply the same allowance margin to the forced-placement test that routing "+
			"already applies. Routing requires the predicted pace to be within 90% "+
			"of the budget; without this flag the test that decides between "+
			"rejecting and forcing compares against the budget itself, so a request "+
			"predicted to land between the two is refused a routed placement and "+
			"then given a forced one. Measured at 60 req/s, 62% of admitted chat "+
			"then missed, at a predicted pace accurate to 0.1 ms. Off by default "+
			"until EXP-42 judges it.")
	flags.BoolVar(&c.FluidserveOwnBudgetGate, "fluidserve-own-budget-gate", false,
		"Judge an arriving request's pace against its OWN class budget rather "+
			"than against the tightest nominal budget on the instance. The "+
			"instance minimum is redundant -- the requests already there are "+
			"protected on the next line by their REMAINING budgets -- and it "+
			"becomes chat's 50 ms on every instance within seconds, so a deep "+
			"research request with a 100 ms budget cannot route onto a fleet "+
			"running at 55.6 ms. Off by default until EXP-46 judges it.")
	flags.BoolVar(&c.FluidserveIncumbentBalance, "fluidserve-incumbent-balance", true,
		"Protect the requests already on an instance by what each of them has "+
			"LEFT per token, not only by what its class was promised. This is "+
			"the second of the two pace conditions: the first compares the pace "+
			"after admitting against the tightest NOMINAL budget on the "+
			"instance, this one compares it against the tightest REMAINING "+
			"budget, which carries how fast those requests have actually been "+
			"served so far. Turning it off leaves the nominal condition and so "+
			"reduces the predicate to the form the baselines use, which compare "+
			"a predicted pace against a constant budget carried on the request. "+
			"The two are not the same quantity: measured over the hour-long "+
			"trace the nominal and the remaining minimum disagree on 55-59%% of "+
			"instance-seconds, by 5.9-8.1 ms at the 90th percentile against a "+
			"50 ms chat budget, and on 3.9-6.0%% of them they give opposite "+
			"answers; on a fixed-rate condition the same disagreement is 38-45%% "+
			"and 2.2-3.8 ms, so a static benchmark understates this axis. On by "+
			"default, which is the shipped behaviour.")
	flags.BoolVar(&c.FluidserveMemLevelTest, "fluidserve-mem-level-test", false,
		"Compare kvLogical + cost against capMem instead of proj + cost, making "+
			"the memory predicate a level test. The projection prices departures "+
			"and not arrivals: measured over one horizon an instance moves by "+
			"generated 46,087 + admitted 158,216 - released 204,988 = -686, while "+
			"the model predicts 49,321 - 153,968 = -104,647, so it forecasts a "+
			"drain the arrivals then fill. That optimism is 5.3% where the safety "+
			"factor is 5%, and the two cancel. Off by default. EXP-114.")
	flags.Float64Var(&c.FluidserveMemorySafety, "fluidserve-memory-safety", 0,
		"Fraction of the physical KV pool capMem admits up to. Zero keeps the "+
			"compiled 0.95. The engine's measured preemption onset on the "+
			"eight-instance fleet is 0.973-0.998, so 0.95 is 2.3-4.8 occupancy "+
			"points conservative; lowering this trades admissions for distance "+
			"from that onset. EXP-114.")
	flags.BoolVar(&c.FluidservePrefillFullIteration, "fluidserve-prefill-full-iteration", false,
		"Price the observed prefill queue's drain at the full cost of a "+
			"chunk-carrying iteration, t_pre + t_dec - c0, which is the "+
			"composition the capacity model already uses, instead of dividing the "+
			"prefill-only cost by the instance's unconditional prefill duty and "+
			"taking the larger of that and the planning horizon's arriving work. "+
			"Measured over six one-hour runs on two fleet shapes, one millisecond "+
			"of prefill-only queue cost is worth 0.35-1.03 ms of wall clock where "+
			"1/duty asserts 3.0-3.6, and the floor under the divisor makes the "+
			"estimate jump 5.4x-7.9x across a continuous input while the measured "+
			"first token moves 11-27%. Off by default. EXP-117.")
	flags.BoolVar(&c.FluidservePrefillResidentQueue, "fluidserve-prefill-resident-queue", false,
		"Let a prompt believed wholly resident in the prefix index still carry the "+
			"instance-level queue term in the first-token estimate. A prefix hit "+
			"removes this request's own prefill compute; it does not move the "+
			"request forward in the engine's queue, and the estimate currently "+
			"returns exactly zero for 1.50-1.95% of placements regardless of what "+
			"is queued. Off by default. EXP-117.")
	flags.IntVar(&c.FluidserveArrivalHorizons, "fluidserve-arrival-horizons", 0,
		"Average the prompt mass this instance received over the last N horizons "+
			"and add it to the KV projection. Zero disables the term, which is the "+
			"deployed behaviour. The projection has no term for prompts routed in "+
			"DURING the horizon: measured per instance per horizon, the realised "+
			"change is generation +41,508 plus prompts routed in +133,565 minus "+
			"released -173,317 = -971, while the model says +41,508 - 129,219 = "+
			"-87,735, so the omitted term is 1.5 times the drain that is modelled. "+
			"N is a window and not a gain -- the term is the MEAN per horizon, so N "+
			"changes how much noise is averaged away and not how much is charged. "+
			"The per-instance correlation between one horizon's arrival mass and "+
			"the next is +0.76 at N=1, +0.83 at N=10 and falls again at N=20. "+
			"EXP-124.")
	flags.BoolVar(&c.FluidserveDeadlineFeasible, "fluidserve-deadline-feasible", false,
		"Make the first-token deadline part of the feasibility test, not only of "+
			"the refusal test. Feasibility is otherwise four conditions about "+
			"per-token pace and KV, none of which asks when this request would see "+
			"its own first token; a pace inside the gate is compatible with a long "+
			"prefill queue ahead of it. Measured, held deepresearch requests were "+
			"routed onto an instance delivering 66-70 ms per token that was carrying "+
			"76,813-87,845 tokens of queued prefill, and took 12.6-13.2 s to a first "+
			"token against a 10 s budget; the test that would have refused them, "+
			"waited + prefillMs > ttftSlo, is already computed and sits on a path the "+
			"decision does not take once the candidate is feasible. Only the "+
			"time-to-first-token form is applied: the end-to-end form that swe is "+
			"judged on also carries the whole decode, and folding it in would change "+
			"two classes at once. Off by default, which reproduces every measurement "+
			"taken before EXP-87.")
	flags.BoolVar(&c.FluidserveKvSlopeProjection, "fluidserve-kv-slope-projection",
		false,
		"Project an instance's KV occupancy from the rate that occupancy is "+
			"observed to be moving at, instead of from a modelled balance of the "+
			"resident set's growth against what completions are expected to "+
			"release. The modelled balance has three terms where the quantity has "+
			"four: it omits the requests the scheduler places during the horizon "+
			"itself, which at saturation is about 48 placements and 83,000 tokens "+
			"per engine. Scored against what the engines actually held one horizon "+
			"later, over 14,676 paired samples of the hour-long trace, the modelled "+
			"balance under-predicts 88.5% of the time by a mean of 101,633 tokens "+
			"while the observed slope is unbiased and has a third of the absolute "+
			"error. Off by default until EXP-49 judges it.")
	flags.Float64Var(&c.FluidserveGateSlack, "fluidserve-gate-slack", 1.0,
		"How far past the tightest per-token budget promised to anything live on "+
			"an instance that instance may be driven, in order to serve a class "+
			"whose own budget is looser. 1 holds the instance to that promise, "+
			"which is the shipped behaviour; a value at or above the largest ratio "+
			"between two class budgets removes the instance minimum entirely and "+
			"is the same policy as --fluidserve-own-budget-gate. The two ends fail "+
			"in opposite directions and both are measured: at 1 a fleet delivering "+
			"45 ms refuses a request promised 100, and 5 of 14 runs at 45 req/s "+
			"end with every gate at 45.0 ms and 15%% of decisions routing; at the "+
			"far end the hour-long trace loses 4.4 points because the capacity "+
			"freed for the loose-budget classes comes out of chat, which is 76.9%% "+
			"of arrivals. Requests already on the instance are protected on the "+
			"next condition regardless, by what each has left rather than by what "+
			"its class was promised.")
	// vLLM router cache_aware baseline. Every default here is the one PyPI
	// `vllm-router` 0.1.15 ships, because that package is what the arm is meant
	// to represent.
	flags.Float64Var(&c.VllmCacheThreshold, "vllm-cache-threshold", 0.3,
		"Prefix match rate above which the vLLM router cache_aware baseline routes "+
			"to the best-matching instance; at or below it routes to the instance "+
			"with the smallest tree. NOTE 0.3 is vllm-router's default and 0.7 is "+
			"the SGLang model gateway's, which it forked -- the two give different "+
			"routers, so the value is stated rather than inherited.")
	flags.IntVar(&c.VllmCacheBalanceAbs, "vllm-cache-balance-abs", 64,
		"Absolute queue-depth difference above which the cache_aware baseline "+
			"treats the fleet as imbalanced and routes to the shortest queue.")
	flags.Float64Var(&c.VllmCacheBalanceRel, "vllm-cache-balance-rel", 1.5,
		"Relative queue-depth ratio above which the cache_aware baseline treats "+
			"the fleet as imbalanced. Both this and the absolute test must hold.")
	flags.IntVar(&c.VllmCacheEvictionSecs, "vllm-cache-eviction-secs", 120,
		"Interval between LRU eviction passes over the per-instance prefix trees.")
	flags.IntVar(&c.VllmCacheMaxTreeSize, "vllm-cache-max-tree-size", 1<<26,
		"Maximum nodes per per-instance prefix tree before LRU eviction runs.")

	flags.BoolVar(&c.FluidserveClassHarm, "fluidserve-class-harm", true,
		"When no instance can take a request at its promised pace, charge each "+
			"candidate for the share of it that belongs to OTHER classes. Disabling "+
			"it leaves the damage estimate as the sum over incumbents that can still "+
			"meet their budgets, which reads an instance whose class has already "+
			"begun to miss as costing nothing and therefore keeps sending it more. "+
			"It is a separate switch from --fluidserve-enable-affinity so that the "+
			"feasible-set ordering and this can be told apart in an ablation.")

	// EXP-107: the class-instance cap and the force-branch switch. Defaults
	// reproduce the shipped behaviour exactly -- cap off, force on -- so every
	// arm that does not set them keeps the meaning of every run measured
	// before the flags existed. Moving either default is a version event, not
	// an edit (the v0.3 procedure).
	flags.BoolVar(&c.FluidserveClassInstanceCap, "fluidserve-class-instance-cap", false,
		"Cap, per SLO class, the number of instances whose pace gate that class "+
			"sets, at a demand-derived limit (arrival rate over the per-instance "+
			"service rate at the class's pace). At the limit, the class may only be "+
			"placed on its most-loaded gate holders, so surplus gate holders drain "+
			"within one residence time and their gates release to the next class.")
	flags.BoolVar(&c.FluidserveCapCostsCapacity, "fluidserve-cap-costs-capacity", false,
		"Make the class-instance cap keep a candidate it would otherwise remove "+
			"when placing this class there would not actually cost that instance "+
			"any admissible capacity. Admission takes min(capKv, capMem); a "+
			"tighter gate lowers capKv only, so while capMem is the smaller "+
			"ceiling the cap is refusing a placement to protect capacity that was "+
			"never at risk. Measured at 185 req/s on eight Llama-3.1-8B instances "+
			"that is 100% of loaded scrapes for deepresearch, 98.9% for swe and "+
			"77.0% for chat, while on four 70B instances the pace ceiling refused "+
			"38 placements per one the physical pool refused. Separate from "+
			"--fluidserve-class-instance-cap so that flag keeps naming the "+
			"mechanism every earlier run was measured with.")
	flags.Float64Var(&c.FluidserveClassInstanceCapWindowMult,
		"fluidserve-class-instance-cap-window-mult", 3.0,
		"Smoothing horizon of the cap's demand estimate, in residence times of "+
			"each class (clamped to 15-120 s). It defines how long a demand shift "+
			"must persist before it moves the class's instance limit: bursts "+
			"shorter than the window are absorbed by admission instead of by "+
			"re-gating instances.")
	flags.BoolVar(&c.FluidserveEnableForce, "fluidserve-enable-force", true,
		"Keep the last-resort branch that places a request every instance refused, "+
			"provided the request itself is still predicted to meet its own budget. "+
			"With false, that case is rejected immediately instead (shed reason "+
			"no_feasible): the placement was known to push running requests past "+
			"their budgets, and measured on the mix-shift hour such placements met "+
			"their own budgets only 56-82% of the time. Requires "+
			"--fluidserve-enable-shed=true when false.")

	flags.BoolVar(&c.EnableAdaptivePD, "enable-adaptive-pd", consts.DefaultEnableAdaptivePD, "Llumnix enable adaptive pd")
	flags.Float32Var(&c.TpotMigrateOutFloorThreshold, "tpot-migrate-out-floor-threshold", consts.DefaultTpotMigrateOutFloorThreshold, "Llumnix tpot migrate out floor threshold")

	flags.StringVar(&c.FailoverDomain, "failover-domain", consts.DefaultFailoverDomain, "Llumnix failover domain")
	flags.Int64Var(&c.InstanceStalenessSeconds, "instance-staleness-seconds", consts.DefaultInstanceStalenessSeconds, "Llumnix instance staleness seconds")

	flags.BoolVar(&c.EnableRescheduling, "enable-rescheduling", consts.DefaultEnableRescheduling, "Llumnix enable rescheduling")
	flags.StringVar(&c.ReschedulingPolicies, "rescheduling-policies", consts.DefaultReschedulingPolicies, "Llumnix rescheduling policies, comma separated")
	flags.Int32Var(&c.ReschedulingIntervalMs, "rescheduling-interval-ms", consts.DefaultReschedulingIntervalMs, "Llumnix rescheduling interval milliseconds")
	flags.StringVar(&c.ReschedulingDecodeLoadMetric, "rescheduling-decode-load-metric", consts.DefaultReschedulingDecodeLoadMetric, "Llumnix rescheduling decode load metric")
	flags.Float32Var(&c.ReschedulingDecodeLoadThreshold, "rescheduling-decode-load-threshold", consts.DefaultReschedulingDecodeLoadThreshold, "Llumnix rescheduling decode load threshold")
	flags.StringVar(&c.ReschedulingPrefillLoadMetric, "rescheduling-prefill-load-metric", consts.DefaultReschedulingPrefillLoadMetric, "Llumnix rescheduling prefill load metric")
	flags.StringVar(&c.ReschedulingNeutralLoadMetric, "rescheduling-neutral-load-metric", consts.DefaultReschedulingNeutralLoadMetric, "Llumnix rescheduling neutral load metric")
	flags.Float32Var(&c.ReschedulingNeutralLoadThreshold, "rescheduling-neutral-load-threshold", consts.DefaultReschedulingNeutralLoadThreshold, "Llumnix rescheduling neutral load threshold")
	flags.StringVar(&c.ReschedulingReqSelectOrder, "rescheduling-req-select-order", consts.DefaultReschedulingReqSelectOrder, "Llumnix rescheduling req selection order")
	flags.StringVar(&c.ReschedulingReqSelectRule, "rescheduling-req-select-rule", consts.DefaultReschedulingReqSelectRule, "Llumnix rescheduling req selection rule")
	flags.Float32Var(&c.ReschedulingReqSelectValue, "rescheduling-req-select-value", consts.DefaultReschedulingReqSelectValue, "Llumnix rescheduling req selection value")
	flags.Float32Var(&c.ReschedulingLoadBalanceThreshold, "rescheduling-load-balance-threshold", consts.DefaultReschedulingLoadBalanceThreshold, "Llumnix rescheduling load balance threshold")
	flags.StringVar(&c.ReschedulingLoadBalanceScope, "rescheduling-load-balance-scope", consts.DefaultReschedulingLoadBalanceScope, "Llumnix rescheduling load balance scope")

	flags.IntVar(&c.LlumletGrpcConnectionPoolSize, "llumlet-grpc-connection-pool-size", consts.DefaultLlumletGrpcConnectionPoolSize, "Llumnix llumlet grpc connection pool size")
	flags.IntVar(&c.LlumletGrpcTimeoutSeconds, "llumlet-grpc-timeout-seconds", consts.DefaultLlumletGrpcTimeoutSeconds, "Llumnix llumlet grpc timeout seconds")
}

// safeSplitArgs safely splits a string by comma, handling quotes and square brackets.
// Example: "a=1,b=[1,2,3],c='hello,world'" -> ["a=1", "b=[1,2,3]", "c='hello,world'"]
func safeSplitArgs(input string) []string {
	if input == "" {
		return nil
	}

	var result []string
	var current strings.Builder
	var inQuote rune     // 0 if not in quote, otherwise the quote char (' or ")
	var bracketDepth int // Track nesting depth of square brackets

	for i, ch := range input {
		// Handle escape sequences
		if i > 0 && input[i-1] == '\\' {
			current.WriteRune(ch)
			continue
		}

		// Handle quotes
		if (ch == '\'' || ch == '"') && bracketDepth == 0 {
			if inQuote == 0 {
				inQuote = ch
			} else if inQuote == ch {
				inQuote = 0
			}
			current.WriteRune(ch)
			continue
		}

		// Skip processing if inside quotes
		if inQuote != 0 {
			current.WriteRune(ch)
			continue
		}

		// Handle square brackets
		if ch == '[' {
			bracketDepth++
			current.WriteRune(ch)
			continue
		}

		if ch == ']' {
			if bracketDepth > 0 {
				bracketDepth--
			}
			current.WriteRune(ch)
			continue
		}

		// Handle comma separator (only when not in quotes or brackets)
		if ch == ',' && bracketDepth == 0 {
			if current.Len() > 0 {
				result = append(result, strings.TrimSpace(current.String()))
				current.Reset()
			}
			continue
		}

		current.WriteRune(ch)
	}

	// Add the last segment
	if current.Len() > 0 {
		result = append(result, strings.TrimSpace(current.String()))
	}

	// Remove surrounding quotes from each segment
	for i, segment := range result {
		// Split by first '=' to check if it's a key=value pair
		parts := strings.SplitN(segment, "=", 2)
		if len(parts) == 2 {
			value := parts[1]
			// Remove quotes (not brackets)
			if len(value) >= 2 && value[0] == '\'' && value[len(value)-1] == '\'' {
				result[i] = parts[0] + "=" + value[1:len(value)-1]
			} else if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
				result[i] = parts[0] + "=" + value[1:len(value)-1]
			}
		}
	}

	return result
}

// ParseLlumnixExtraArgs parses llumnix-extra-args and overrides corresponding flag values.
// Supports two formats for array values:
//   - Quoted strings: key='value1,value2' -> parsed as: key=value1,value2 (quotes removed)
//   - Bracket arrays: key=[value1,value2] -> parsed as: key=[value1,value2] (brackets kept)
//
// Example: "dispatch-top-k=5,policies=[p1,p2],timeout='10,20'" ->
//
//	"dispatch-top-k=5", "policies=[p1,p2]", "timeout=10,20"
func ParseLlumnixExtraArgs(flags *pflag.FlagSet, extraArgs string) {
	if extraArgs == "" {
		return
	}

	klog.Infof("Parsing extra-args: %s", extraArgs)

	// Use safeSplitArgs to safely split by comma, handling quotes, brackets and arrays
	args := safeSplitArgs(extraArgs)

	for _, arg := range args {
		if arg == "" {
			continue
		}

		// Split by first '=' to get key-value pair
		parts := strings.SplitN(arg, "=", 2)
		if len(parts) != 2 {
			klog.Warningf("Invalid extra arg format (expected key=value): %s", arg)
			continue
		}

		key := strings.TrimSpace(parts[0])
		value := strings.TrimSpace(parts[1])

		// Try to set the flag value
		flag := flags.Lookup(key)
		if flag == nil {
			klog.Warningf("Flag not found, skipping: %s", key)
			continue
		}

		if err := flags.Set(key, value); err != nil {
			klog.Warningf("Failed to set flag %s=%s: %v", key, value, err)
			continue
		}

		klog.Infof("Successfully set flag from extra-args: %s=%s", key, value)
	}
}
