package types

import (
	"fmt"
	"strconv"
	"strings"

	"llumnix/pkg/consts"
)

type SchedulingMode string

const (
	SchedulingModeNeutral  SchedulingMode = "neutral"
	SchedulingModePDBatch  SchedulingMode = "pd_batch"
	SchedulingModePDStaged SchedulingMode = "pd_staged"
)

type SchedulingResult []LLMInstance

func (sr SchedulingResult) String() string {
	var str string
	for _, instance := range sr {
		if len(str) > 0 {
			str += ","
		}
		str += instance.String()
	}
	return str
}

func (sr SchedulingResult) GetInstanceByInferType(inferType consts.InferType) *LLMInstance {
	for _, instance := range sr {
		if instance.InferType == inferType {
			return &instance
		}
	}
	return nil
}

type SchedulingRequest struct {
	// Scheduling information
	Id              string                 `json:"id"`
	Model           string                 `json:"model"`
	GatewayId       string                 `json:"gateway_id,omitempty"`
	SchedulingMode  SchedulingMode         `json:"scheduling_mode"`
	SchedulingStage consts.SchedulingStage `json:"scheduling_stage"`

	// LLM Prompt
	PromptNumTokens int      `json:"prompt_num_tokens,omitempty"`
	PromptTokenIds  []uint32 `json:"prompt_token_ids"`

	// Per-request SLO in milliseconds, decoded by the gateway from the client's
	// packed "priority" field (see DecodePackedSlo). Zero means unspecified, in
	// which case an SLO-aware policy falls back to its global --ttft-slo /
	// --tpot-slo. Only PolyServe reads these; every other policy ignores them,
	// so populating them is inert for the existing arms.
	TtftSloMs int `json:"ttft_slo_ms,omitempty"`
	TpotSloMs int `json:"tpot_slo_ms,omitempty"`

	// PredictedOutputTokens is how many output tokens this request is expected to
	// produce, when the client supplies it. EXP-64 carries a near-exact value
	// here to bound what a finer-grained length predictor could be worth; a real
	// deployment would carry a prediction. Zero means unsupplied, and every
	// policy that does not read it is unaffected.
	//
	// It rides in the OpenAI `user` field rather than `max_tokens`, and that
	// choice is the point: max_tokens would truncate generation and change the
	// workload, making the hint an upper bound instead of information, whereas
	// `user` is accepted and ignored by vLLM. The two arms of the experiment
	// therefore emit byte-identical requests and differ only in whether the
	// scheduler reads the field.
	PredictedOutputTokens int `json:"predicted_output_tokens,omitempty"`

	// scheduling result
	SchedulingResult SchedulingResult `json:"scheduling_result,omitempty"`
}

const (
	// sloPriorityScale is the packing radix shared with the client and with the
	// engine-side scheduler (patches/vllm-sched/slo_tier.py, deadline_sched.py).
	sloPriorityScale = 1000
	// maxPlausibleTtftSloMs bounds a decoded TTFT SLO at one hour. It exists to
	// reject the OTHER encoding that occupies the same field: the EDF arm sends
	// priority = arrival_ms + slo_ms, an absolute epoch deadline (~1.8e12), which
	// would otherwise decode to a nonsense multi-week TTFT. A best-effort tier
	// (30 days) is rejected by the same bound and correctly falls back to the
	// global SLO, i.e. "no special treatment".
	maxPlausibleTtftSloMs = 3600 * 1000
)

// DecodePackedSlo unpacks a per-request SLO from the single integer channel the
// OpenAI API gives us. The client packs ttft_slo_ms*1000 + tpot_slo_ms, so one
// "priority" value carries both budgets; vLLM reads the same number as a plain
// priority (lower first), which conveniently makes it an EDF order too.
//
// ok is false when the value cannot be a packed SLO, in which case the caller
// must leave both budgets unset rather than trust a garbage decode.
func DecodePackedSlo(priority int) (ttftSloMs int, tpotSloMs int, ok bool) {
	if priority <= 0 {
		return 0, 0, false
	}
	ttftSloMs = priority / sloPriorityScale
	tpotSloMs = priority % sloPriorityScale
	if ttftSloMs <= 0 || ttftSloMs > maxPlausibleTtftSloMs {
		return 0, 0, false
	}
	return ttftSloMs, tpotSloMs, true
}

func (req *SchedulingRequest) String() string {
	var str string

	// Add basic identification info
	if req.Id != "" {
		str += req.Id
	}
	if req.Model != "" {
		if len(str) > 0 {
			str += "|"
		}
		str += req.Model
	}
	if req.GatewayId != "" {
		if len(str) > 0 {
			str += "@"
		}
		str += req.GatewayId
	}

	// Add scheduling mode and scheduling stage
	if req.SchedulingMode != "" {
		if len(str) > 0 {
			str += " "
		}
		str += string(req.SchedulingMode)
	}
	if req.SchedulingStage != "" {
		if len(str) > 0 {
			str += "/"
		}
		str += string(req.SchedulingStage)
	}

	// Add prompt info (truncated if too long)
	if req.PromptNumTokens > 0 {
		if len(str) > 0 {
			str += " "
		}
		str += fmt.Sprintf("tokens:%d", req.PromptNumTokens)
	}

	// Add per-request SLO so a dispatch log line shows which budget was applied
	if req.TtftSloMs > 0 || req.TpotSloMs > 0 {
		if len(str) > 0 {
			str += " "
		}
		str += fmt.Sprintf("slo:%dms/%dms", req.TtftSloMs, req.TpotSloMs)
	}

	// Add scheduling result if present
	if req.SchedulingResult != nil {
		if len(str) > 0 {
			str += " "
		}
		str += fmt.Sprintf("result:[%s]", req.SchedulingResult.String())
	}

	return str
}

// ParseLengthHint reads the `len:<n>` encoding out of the OpenAI `user` field.
//
// Anything else returns false rather than a guess. A malformed hint that
// silently became zero would make the treatment arm identical to its control
// with nothing in any log to say so, which is the failure mode this repository
// has hit with a boolean flag, a preserved deployment setting and a verifier
// regex. The caller logs the rejection.
func ParseLengthHint(user string) (int, bool) {
	const prefix = "len:"
	user = strings.TrimSpace(user)
	if !strings.HasPrefix(user, prefix) {
		return 0, false
	}
	n, err := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(user, prefix)))
	if err != nil || n <= 0 {
		return 0, false
	}
	return n, true
}
