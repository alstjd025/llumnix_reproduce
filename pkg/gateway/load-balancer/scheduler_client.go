package balancer

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"k8s.io/klog/v2"

	"llumnix/cmd/gateway/app/options"
	"llumnix/pkg/consts"
	"llumnix/pkg/keepalive"
	"llumnix/pkg/metrics"
	"llumnix/pkg/resolver"
	"llumnix/pkg/types"
)

const (
	scheduleRequestTimeout = 2 * time.Second
	releaseTryCount        = 3
)

type SchedulerClient struct {
	config *options.GatewayConfig

	schClient *http.Client

	kac *keepalive.KeepAliveClient
}

func NewSchedulerClient(config *options.GatewayConfig) *SchedulerClient {
	schResolver := resolver.CreateSchedulerResolver(&config.DiscoveryConfig)
	kac := keepalive.NewKeepAliveClient("scheduler", config.ServiceToken, schResolver)

	cb := &SchedulerClient{
		config:    config,
		schClient: &http.Client{Timeout: scheduleRequestTimeout},
		kac:       kac,
	}

	kac.StartAndAwaitReady()

	klog.Infof("create llm scheduler balancer successfully.")
	return cb
}

func (cb *SchedulerClient) createSchedulingRequest(req *types.RequestContext) *types.SchedulingRequest {
	localEndpoint := cb.kac.GetLocalEndpoint()
	// create scheduler request
	schRequest := &types.SchedulingRequest{
		Id:              req.Id,
		Model:           req.LLMRequest.Model,
		GatewayId:       localEndpoint.String(),
		SchedulingMode:  req.SchedulingCtx.SchedulingMode,
		SchedulingStage: req.SchedulingCtx.SchedulingStage,
	}

	if tokenIds, ok := req.LLMRequest.GetPromptTokens(); ok {
		schRequest.PromptNumTokens = len(tokenIds)
		if cb.config.ForwardTokens {
			schRequest.PromptTokenIds = tokenIds
		}
	} else {
		klog.Warningf("scheduling request %s prompt string and token ids are empty or not support: %v", req.Id, req.LLMRequest.CompletionRequest.Prompt)
	}

	// Carry the client's per-request SLO through to the scheduler. The OpenAI
	// API gives us one integer channel, so the client packs both budgets into
	// "priority"; the same number still works as a vLLM priority downstream.
	// Policies that do not consume the SLO simply ignore these fields.
	if cr := req.LLMRequest.CompletionRequest; cr != nil && cr.Priority != nil {
		if ttftMs, tpotMs, ok := types.DecodePackedSlo(*cr.Priority); ok {
			schRequest.TtftSloMs = ttftMs
			schRequest.TpotSloMs = tpotMs
		} else {
			klog.V(4).Infof("request %s: priority %d is not a packed SLO, leaving budgets unset",
				req.Id, *cr.Priority)
		}
	}
	// record the borrow gateway
	req.SchedulingCtx.GatewayId = localEndpoint.String()
	return schRequest
}

func (cb *SchedulerClient) handleResponse(body []byte) (types.SchedulingResult, error) {
	var schReq types.SchedulingRequest
	err := json.Unmarshal(body, &schReq)
	if err != nil {
		err = consts.ErrorEndpointNotFound
		klog.Warningf("do scheduling: content format error: %s", string(body))
		return nil, err
	}
	if len(schReq.SchedulingResult) == 0 {
		klog.Warningf("get next endpoint: no available endpoint")
		return nil, consts.ErrorNoAvailableEndpoint
	}

	klog.V(3).Infof("scheduling result: %v", schReq.SchedulingResult.String())
	return schReq.SchedulingResult, nil
}

func (cb *SchedulerClient) doSchedule(req *types.RequestContext) (types.SchedulingResult, error) {
	// check scheduler ready or not
	if !cb.kac.IsRemoteReady() {
		return nil, consts.ErrorSchedulerNotReady
	}

	// create scheduling request to scheduler
	schReq := cb.createSchedulingRequest(req)
	data, err := json.Marshal(&schReq)
	if err != nil {
		klog.Errorf("failed to marshal scheduling request: %v", err)
		return nil, err
	}

	// send request to scheduler
	url := fmt.Sprintf("http://%s/schedule", cb.kac.GetRemoteEndpoint().String())
	httpReq, _ := http.NewRequest("POST", url, bytes.NewBuffer(data))
	if len(cb.config.ServiceToken) > 0 {
		httpReq.Header.Add("Authorization", cb.config.ServiceToken)
	}
	resp, err := cb.schClient.Do(httpReq)
	if err != nil {
		klog.Warningf("[%s] do request scheduling fail: %v\n", req.Id, err)
		return nil, consts.ErrorMayNetworkBroken
	}

	defer resp.Body.Close()

	var body []byte
	body, err = io.ReadAll(resp.Body)
	if err != nil {
		klog.Warningf("[%s] do request scheduling: could not read body: %v", req.Id, err)
		return nil, consts.ErrorMayNetworkBroken
	}

	switch resp.StatusCode {
	case http.StatusOK:
		return cb.handleResponse(body)
	case http.StatusTooManyRequests:
		// The scheduler uses one status for two answers. "no available endpoint"
		// means not yet, and the caller may keep holding the request; "admission
		// rejected" means the scheduler has decided it will not be served, and
		// holding it any longer only delays the client's error.
		if bytes.Contains(body, []byte(consts.ErrorAdmissionRejected.Error())) {
			return nil, consts.ErrorAdmissionRejected
		}
		return nil, consts.ErrorNoAvailableEndpoint
	case http.StatusNotFound:
		return nil, consts.ErrorEndpointNotFound
	default:
		klog.Warningf("[%s] do request scheduling: %d, %s", req.Id, resp.StatusCode, string(body))
		return nil, consts.ErrorEndpointNotFound
	}
}

func (cb *SchedulerClient) Get(req *types.RequestContext) (types.SchedulingResult, error) {
	tStart := time.Now()

	if !cb.kac.IsRemoteReady() {
		return nil, consts.ErrorSchedulerNotReady
	}

	waits := 0
	for {
		result, err := cb.doSchedule(req)
		if err == nil {
			if waits > 0 {
				metrics.Counter("gateway_scheduling_waited_total", metrics.Labels{}).Inc()
				metrics.Histogram("gateway_scheduling_wait_milliseconds",
					metrics.Labels{}).ObserveInt(time.Since(tStart).Milliseconds())
			}
			return result, nil
		}

		// don't return errors directly, as the scheduler may not have
		// fully loaded all backend service instances right after a restart.
		// if err == consts.ErrorEndpointNotFound {
		// 	return nil, err
		// }

		// The scheduler has decided this request will not be served. Returning
		// now is the whole point of the decision: the capacity it would have
		// used goes to the requests that can still meet their budgets, and the
		// client learns immediately rather than after the hold expires.
		if errors.Is(err, consts.ErrorAdmissionRejected) {
			metrics.Counter("gateway_scheduling_rejected_total", metrics.Labels{}).Inc()
			return nil, err
		}

		// all service endpoints are busy, wait a period for next try
		if errors.Is(err, consts.ErrorNoAvailableEndpoint) {
			if time.Since(tStart) > cb.config.WaitSchedulingTimeout {
				metrics.Counter("gateway_scheduling_gave_up_total", metrics.Labels{}).Inc()
				return nil, err
			} else {
				// V(3), not Info: with a scheduling policy that answers "not
				// yet" as a normal outcome rather than an error -- holding the
				// request here is how it defers a placement -- this line fires
				// once per held request per retry interval, which at a 100 ms
				// interval is thousands of lines a second and both slows the
				// gateway and rotates its log away. The counters above carry
				// the same information durably.
				klog.V(3).Infof("[%s] all service endpoints are busy, try next after %dms",
					req.Id, cb.config.WaitSchedulingRetryInterval.Milliseconds())
				waits++
				time.Sleep(cb.config.WaitSchedulingRetryInterval)
				continue
			}
		}

		// scheduler is not ready
		klog.Warningf("[%s] service backend endpoint get failed, error: %v", req.Id, err)
		return nil, consts.ErrorSchedulerNotReady
	}
}

func (cb *SchedulerClient) createReleaseRequest(req *types.RequestContext, instance *types.LLMInstance) *types.SchedulingRequest {
	localEndpoint := cb.kac.GetLocalEndpoint()
	return &types.SchedulingRequest{
		Id:               req.Id,
		Model:            req.LLMRequest.Model,
		GatewayId:        localEndpoint.String(),
		SchedulingMode:   req.SchedulingCtx.SchedulingMode,
		SchedulingStage:  req.SchedulingCtx.SchedulingStage,
		SchedulingResult: types.SchedulingResult{*instance},
	}
}

// Release synchronously sends a release request to the scheduler to free scheduling state
// (local account, LRS) for the given request and instance.
//
// Synchronous execution guarantees that the previous state is fully released before
// the retry path issues a new Schedule, preventing stale-entry races at the scheduler.
// Release does not blocks the response-producing critical path:
//   - Prefill stage: released in OnPostPrefill after the first token arrives, which could be overlapped by the second token generation.
//   - Retry path: released before issuing the next Schedule.
//   - Decode stage: released in OnPostRequest after the response completes.
//
// While in PD separated scheduling, released in OnPostPrefill will blocked the next decode schedule.
func (cb *SchedulerClient) Release(req *types.RequestContext, instance *types.LLMInstance) {
	if req == nil || instance == nil {
		return
	}
	// 1. The current gateway differs from the one at borrowing time, indicating the resources have been updated and no release is needed.
	// 2. If the scheduler is in a not-ready state, no release is required either, as subsequent new schedulers or connections will refresh these resources.
	localEndpoint := cb.kac.GetLocalEndpoint().String()
	ready := cb.kac.IsRemoteReady()
	if localEndpoint != req.SchedulingCtx.GatewayId || !ready {
		return
	}

	releaseRequest := cb.createReleaseRequest(req, instance)
	klog.V(3).Infof("release request: %v", releaseRequest.String())
	data, err := json.Marshal(releaseRequest)
	if err != nil {
		klog.Errorf("marshal release request error: %v", err)
		return
	}

	endpoint := cb.kac.GetRemoteEndpoint()
	url := fmt.Sprintf("http://%s/release", endpoint.String())
	httpReq, _ := http.NewRequest("POST", url, bytes.NewBuffer(data))
	if len(cb.config.ServiceToken) > 0 {
		httpReq.Header.Add("Authorization", cb.config.ServiceToken)
	}
	retry := releaseTryCount
RETRY:
	resp, err := cb.schClient.Do(httpReq)
	retry--
	if err != nil {
		// retry when some network error occurs
		if retry > 0 {
			time.Sleep(500 * time.Millisecond)
			goto RETRY
		}
		klog.Warningf("release state for request %s fail: %v\n", req.Id, err)
		return
	}

	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		klog.Warningf("release state for request %s: could not read body: %v", req.Id, err)
		return
	}
	if resp.StatusCode != http.StatusOK {
		klog.Errorf("release state for request %s: error: %d, %s", req.Id, resp.StatusCode, string(body))
		return
	}
}
