// Package handler connects the EPP scheduling cycle to the in-process TTFT model.
package handler

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"time"

	"sigs.k8s.io/controller-runtime/pkg/log"

	"github.com/llm-d/llm-d-router/eppopt/model"
	"github.com/llm-d/llm-d-router/pkg/common/routing"
	fwkrequest "github.com/llm-d/llm-d-router/pkg/epp/framework/common/request"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/plugin"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/requesthandling"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/scheduling"
	attrprefix "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/datalayer/attribute/prefix"
	tokenproducer "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/requesthandling/dataproducer/tokenizer"
)

const (
	Type             = "epp-opt-profile-handler"
	candidateProfile = "epp-opt-candidates"
	baselineProfile  = "default"
	prefillProfile   = "prefill"
	vendorLabel      = "llm-d.ai/accelerator-vendor"
)

var (
	_ scheduling.ProfileHandler  = (*Handler)(nil)
	_ requesthandling.PreRequest = (*Handler)(nil)
)

type config struct {
	E2ECalibrationFile   string        `json:"e2eCalibrationFile"`
	TTFTSLOSeconds       float64       `json:"ttftSLOSeconds"`
	CalibrationFile      string        `json:"calibrationFile"`
	MetricsMaxAgeSeconds int           `json:"metricsMaxAgeSeconds"`
	Options              model.Options `json:"decision"`
}

type Handler struct {
	name   string
	model  *model.Model
	e2e    *model.E2EModel
	config config
}

func Factory(name string, params *json.Decoder, _ plugin.Handle) (plugin.Plugin, error) {
	cfg := config{MetricsMaxAgeSeconds: 10}
	if params == nil {
		return nil, errors.New("EPP-Opt needs a calibrationFile")
	}
	params.DisallowUnknownFields()
	if err := params.Decode(&cfg); err != nil {
		return nil, err
	}
	if cfg.E2ECalibrationFile != "" {
		if !(cfg.TTFTSLOSeconds > 0) || cfg.MetricsMaxAgeSeconds <= 0 {
			return nil, errors.New("E2E mode requires a positive TTFT SLO and metrics age")
		}
		fitted, err := model.LoadE2E(cfg.E2ECalibrationFile)
		if err != nil {
			return nil, fmt.Errorf("load E2E calibration: %w", err)
		}
		return &Handler{name: name, e2e: fitted, config: cfg}, nil
	}
	if cfg.CalibrationFile == "" || cfg.MetricsMaxAgeSeconds <= 0 {
		return nil, errors.New("EPP-Opt requires calibrationFile and a positive metricsMaxAgeSeconds")
	}
	if err := cfg.Options.Validate(); err != nil {
		return nil, err
	}
	fitted, err := model.Load(cfg.CalibrationFile)
	if err != nil {
		return nil, fmt.Errorf("load EPP-Opt profile: %w", err)
	}
	return &Handler{name: name, model: fitted, config: cfg}, nil
}

func (h *Handler) TypedName() plugin.TypedName { return plugin.TypedName{Type: Type, Name: h.name} }

func (h *Handler) Consumes() plugin.DataDependencies {
	return plugin.DataDependencies{Required: map[plugin.DataKey]any{
		attrprefix.PrefixCacheMatchInfoDataKey: attrprefix.PrefixCacheMatchInfo{},
		tokenproducer.TokenizedPromptDataKey:   fwkrequest.TokenizedRequest{},
	}}
}

func (h *Handler) Pick(_ context.Context, _ *scheduling.InferenceRequest, profiles map[string]scheduling.SchedulerProfile, results map[string]*scheduling.ProfileRunResult) map[string]scheduling.SchedulerProfile {
	pending := make(map[string]scheduling.SchedulerProfile, 2)
	if _, ran := results[candidateProfile]; !ran && profiles[candidateProfile] != nil {
		pending[candidateProfile] = profiles[candidateProfile]
	}
	if h.e2e != nil {
		if _, ran := results[baselineProfile]; !ran && profiles[baselineProfile] != nil {
			pending[baselineProfile] = profiles[baselineProfile]
		}
	}
	if len(pending) != 0 {
		return pending
	}
	return nil
}

func (h *Handler) ProcessResults(ctx context.Context, req *scheduling.InferenceRequest, results map[string]*scheduling.ProfileRunResult) (*scheduling.SchedulingResult, error) {
	if req == nil {
		return nil, errors.New("EPP-Opt request is nil")
	}
	result := results[candidateProfile]
	original := result
	if h.e2e != nil {
		original = results[baselineProfile]
	}
	if original == nil || len(original.TargetEndpoints) == 0 {
		return nil, errors.New("EPP-Opt has no eligible original endpoint")
	}
	if h.e2e != nil && original.TargetEndpoints[0].GetMetadata().Labels[vendorLabel] != "amd" {
		return selected(original.TargetEndpoints[0], nil), nil
	}
	if h.e2e != nil && (result == nil || len(result.TargetEndpoints) == 0) {
		return selected(original.TargetEndpoints[0], nil), nil
	}
	if result == nil || len(result.TargetEndpoints) == 0 {
		return nil, errors.New("EPP-Opt has no eligible endpoints")
	}
	var nvidia, amd scheduling.Endpoint
	var nvidiaCount, amdCount int
	for _, ep := range result.ScoredCandidates {
		switch ep.GetMetadata().Labels[vendorLabel] {
		case "nvidia":
			nvidia, nvidiaCount = ep.Endpoint, nvidiaCount+1
		case "amd":
			amd, amdCount = ep.Endpoint, amdCount+1
		}
	}
	fallback := func(reason string) (*scheduling.SchedulingResult, error) {
		log.FromContext(ctx).V(1).Info("EPP-Opt fallback selection", "requestID", req.RequestID, "reason", reason)
		return selected(original.TargetEndpoints[0], nil), nil
	}
	if nvidiaCount != 1 || amdCount != 1 {
		return fallback("calibration-requires-single-amd-and-nvidia")
	}
	if req.Body == nil || req.Body.TokenizedRequest == nil {
		return fallback("tokenization-unavailable")
	}
	if !h.fresh(nvidia) || !h.fresh(amd) {
		return fallback("load-snapshot-stale")
	}
	input := req.Body.TokenizedRequest.TokenCount()
	nvidiaCached, lok := prefixHits(nvidia, input)
	amdCached, rok := prefixHits(amd, input)
	if !lok || !rok {
		return fallback("prefix-snapshot-unavailable")
	}
	nm, am := nvidia.GetMetrics(), amd.GetMetrics()
	workload := model.Workload{
		InputTokens: input, NvidiaRunning: nm.RunningRequestsSize, NvidiaWaiting: nm.WaitingQueueSize,
		AMDRunning: am.RunningRequestsSize, AMDWaiting: am.WaitingQueueSize,
		NvidiaCached: nvidiaCached, AMDCached: amdCached, BurstSize: 1,
	}
	if h.e2e != nil {
		if req.Body.MaxOutputTokens == nil {
			return fallback("output-budget-unavailable")
		}
		amdEstimate, pdEstimate, err := h.e2e.Estimate(workload, *req.Body.MaxOutputTokens)
		if err != nil {
			return fallback(err.Error())
		}
		route := "amd"
		if pdEstimate.E2E < amdEstimate.E2E && pdEstimate.TTFT <= h.config.TTFTSLOSeconds {
			route = model.Remote
		}
		log.FromContext(ctx).V(1).Info("EPP-Opt E2E decision", "requestID", req.RequestID,
			"route", route, "originalVendor", "amd", "outputTokens", *req.Body.MaxOutputTokens,
			"amd", amdEstimate, "pd", pdEstimate, "ttftSLOSeconds", h.config.TTFTSLOSeconds, "workload", workload)
		if route == model.Remote {
			return selected(nvidia, original.TargetEndpoints[0]), nil
		}
		return selected(original.TargetEndpoints[0], nil), nil
	}
	decision, err := h.model.Decide(workload, h.config.Options)
	if err != nil {
		return fallback(err.Error())
	}
	log.FromContext(ctx).V(1).Info("EPP-Opt decision", "requestID", req.RequestID, "route", decision.Route,
		"reason", decision.Reason, "estimation", decision.Estimation, "nvidiaTTFTMs", decision.LocalTTFT,
		"pdTTFTMs", decision.RemoteTTFT, "kvMs", decision.KVMS, "workload", workload)
	if decision.Route == model.Remote {
		return selected(nvidia, amd), nil
	}
	return selected(nvidia, nil), nil
}

func (h *Handler) fresh(ep scheduling.Endpoint) bool {
	m := ep.GetMetrics()
	return m.Updated() && time.Since(m.UpdateTime) <= time.Duration(h.config.MetricsMaxAgeSeconds)*time.Second
}

func prefixHits(ep scheduling.Endpoint, input int) (int, bool) {
	raw, _ := ep.Get(attrprefix.PrefixCacheMatchInfoDataKey)
	info, ok := raw.(*attrprefix.PrefixCacheMatchInfo)
	if !ok || info == nil {
		return 0, false
	}
	return min(input, info.CachedBlockCount()*info.BlockSizeTokens()), true
}

func selected(decode, prefill scheduling.Endpoint) *scheduling.SchedulingResult {
	profiles := map[string]*scheduling.ProfileRunResult{
		candidateProfile: {TargetEndpoints: []scheduling.Endpoint{decode}},
	}
	if prefill != nil {
		profiles[prefillProfile] = &scheduling.ProfileRunResult{TargetEndpoints: []scheduling.Endpoint{prefill}}
	}
	return &scheduling.SchedulingResult{PrimaryProfileName: candidateProfile, ProfileResults: profiles}
}

func (h *Handler) PreRequest(_ context.Context, req *scheduling.InferenceRequest, result *scheduling.SchedulingResult) error {
	routing.DeleteRoutingHeader(req.Headers, routing.PrefillEndpointHeader)
	if prefill := result.ProfileResults[prefillProfile]; prefill != nil {
		meta := prefill.TargetEndpoints[0].GetMetadata()
		routing.SetRoutingHeader(req.Headers, routing.PrefillEndpointHeader, net.JoinHostPort(meta.Address, meta.Port))
	}
	return nil
}
