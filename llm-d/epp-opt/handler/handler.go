// Package handler implements EPP-Opt's per-request route selection.
package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/url"
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
	"github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/scheduling/filter/bylabel"
)

const (
	Type             = "e2e-aware-pd-profile-handler"
	WholeFilterType  = "epp-opt-whole-request-filter"
	candidateProfile = "epp-opt-candidates"
	baselineProfile  = "default"
	prefillProfile   = "prefill"
	vendorLabel      = "llm-d.ai/accelerator-vendor"
)

var (
	_ scheduling.ProfileHandler             = (*Handler)(nil)
	_ requesthandling.PreRequest            = (*Handler)(nil)
	_ requesthandling.ResponseBodyProcessor = (*Handler)(nil)
)

type config struct {
	PredictorURL            string   `json:"predictorURL"`
	Model                   string   `json:"model"`
	Runtime                 string   `json:"runtime"`
	VerifiedPD              []string `json:"verifiedPD"`
	MetricsMaxAgeSeconds    int      `json:"metricsMaxAgeSeconds"`
	PredictionTimeoutMillis int      `json:"predictionTimeoutMillis"`
	CalibrationEnabled      bool     `json:"calibrationEnabled"`
}

type Handler struct {
	name     string
	config   config
	client   *http.Client
	verified map[string]bool
	feedback chan model.Observation
	stateKey plugin.DataKey
}

func Factory(name string, params *json.Decoder, handle plugin.Handle) (plugin.Plugin, error) {
	cfg := config{MetricsMaxAgeSeconds: 10, PredictionTimeoutMillis: 35}
	if params == nil {
		return nil, errors.New("EPP-Opt requires a predictor URL, model and runtime ID")
	}
	params.DisallowUnknownFields()
	if err := params.Decode(&cfg); err != nil {
		return nil, err
	}
	u, err := url.Parse(cfg.PredictorURL)
	if err != nil || u.Host == "" || u.Scheme != "http" || u.User != nil ||
		u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") ||
		cfg.Model == "" || cfg.Runtime == "" || cfg.MetricsMaxAgeSeconds <= 0 ||
		cfg.PredictionTimeoutMillis <= 0 || cfg.PredictionTimeoutMillis > 1000 {
		return nil, errors.New("invalid EPP-Opt prediction configuration")
	}
	cfg.PredictorURL = "http://" + u.Host
	h := &Handler{
		name: name, config: cfg, feedback: make(chan model.Observation, 1024),
		verified: make(map[string]bool), stateKey: plugin.NewDataKey("EPPOptObservation", name),
		client: &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
	}
	for _, route := range cfg.VerifiedPD {
		if !model.IsPD(route) {
			return nil, errors.New("verifiedPD accepts only ap-nd and np-ad")
		}
		h.verified[route] = true
	}
	go h.submitFeedback(handle.Context())
	return h, nil
}

func (h *Handler) TypedName() plugin.TypedName { return plugin.TypedName{Type: Type, Name: h.name} }

func (h *Handler) Produces() map[plugin.DataKey]any {
	return map[plugin.DataKey]any{h.stateKey: observationState{}}
}

func (h *Handler) Consumes() plugin.DataDependencies {
	return plugin.DataDependencies{Required: map[plugin.DataKey]any{
		attrprefix.PrefixCacheMatchInfoDataKey: attrprefix.PrefixCacheMatchInfo{},
		tokenproducer.TokenizedPromptDataKey:   fwkrequest.TokenizedRequest{},
	}}
}

type wholeRequestFilter struct{ name string }

func WholeFilterFactory(name string, _ *json.Decoder, _ plugin.Handle) (plugin.Plugin, error) {
	return &wholeRequestFilter{name: name}, nil
}

func (f *wholeRequestFilter) TypedName() plugin.TypedName {
	return plugin.TypedName{Type: WholeFilterType, Name: f.name}
}

func (f *wholeRequestFilter) Filter(_ context.Context, _ *scheduling.InferenceRequest, endpoints []scheduling.Endpoint) []scheduling.Endpoint {
	out := make([]scheduling.Endpoint, 0, len(endpoints))
	for _, endpoint := range endpoints {
		if p, d := roles(endpoint); p && d {
			out = append(out, endpoint)
		}
	}
	return out
}

func roles(ep scheduling.Endpoint) (prefill, decode bool) {
	switch ep.GetMetadata().Labels[bylabel.RoleLabel] {
	case "", bylabel.RolePrefillDecode, bylabel.RoleEncodePrefillDecode:
		return true, true
	case bylabel.RolePrefill, bylabel.RoleEncodePrefill:
		return true, false
	case bylabel.RoleDecode:
		return false, true
	}
	return false, false
}

func (h *Handler) Pick(ctx context.Context, req *scheduling.InferenceRequest, profiles map[string]scheduling.SchedulerProfile, results map[string]*scheduling.ProfileRunResult) map[string]scheduling.SchedulerProfile {
	pending := make(map[string]scheduling.SchedulerProfile, 2)
	for _, name := range []string{baselineProfile, candidateProfile} {
		if name == baselineProfile && h.calibrationRoute(ctx, req) != "" {
			continue
		}
		if _, ran := results[name]; !ran && profiles[name] != nil {
			pending[name] = profiles[name]
		}
	}
	return pending
}

type path struct {
	model.Candidate
	prefill, decode scheduling.Endpoint
}

func endpointID(ep scheduling.Endpoint) string {
	meta := ep.GetMetadata()
	return meta.GetNamespacedName().String() + "@" + address(ep)
}

func address(ep scheduling.Endpoint) string {
	meta := ep.GetMetadata()
	return net.JoinHostPort(meta.Address, meta.Port)
}

func makePath(req *scheduling.InferenceRequest, runtime, route string, prefill, decode scheduling.Endpoint, p, d model.Load, input int, budget int64) path {
	return path{
		Candidate: model.Candidate{
			ID:       route + "/" + endpointID(prefill) + "/" + endpointID(decode),
			Features: model.Features{Model: req.TargetModel, Runtime: runtime, Route: route, Input: input, Budget: budget, Prefill: p, Decode: d},
		},
		prefill: prefill, decode: decode,
	}
}

func requestFeatures(req *scheduling.InferenceRequest) (int, int64, string) {
	body := req.Body
	if body == nil || body.TokenizedRequest == nil || body.MaxOutputTokens == nil || *body.MaxOutputTokens <= 0 {
		return 0, 0, "missing-request-features"
	}
	input, budget := body.TokenizedRequest.TokenCount(), *body.MaxOutputTokens
	if input <= 0 {
		return 0, 0, "missing-input-tokens"
	}
	return input, budget, ""
}

func (h *Handler) paths(req *scheduling.InferenceRequest, base scheduling.Endpoint, candidates *scheduling.ProfileRunResult) (path, map[string]path, string) {
	input, budget, reason := requestFeatures(req)
	if reason != "" {
		return path{}, nil, reason
	}
	now := time.Now()
	paths := make(map[string]path)
	var baseline path
	if base != nil {
		baseLoad, ok := h.load(base, input, now)
		vendor := base.GetMetadata().Labels[vendorLabel]
		if !ok || (vendor != model.AMD && vendor != model.NVIDIA) {
			return path{}, nil, "missing-baseline-features"
		}
		baseline = makePath(req, h.config.Runtime, vendor, base, base, baseLoad, baseLoad, input, budget)
		paths[baseline.ID] = baseline
	}
	if candidates == nil {
		return baseline, paths, ""
	}
	type worker struct {
		ep              scheduling.Endpoint
		load            model.Load
		prefill, decode bool
	}
	workers := map[string][]worker{}
	for _, c := range candidates.ScoredCandidates {
		ep := c.Endpoint
		vendor := ep.GetMetadata().Labels[vendorLabel]
		if vendor != model.AMD && vendor != model.NVIDIA {
			continue
		}
		load, ok := h.load(ep, input, now)
		if !ok {
			continue
		}
		p, d := roles(ep)
		workers[vendor] = append(workers[vendor], worker{ep, load, p, d})
		if p && d {
			whole := makePath(req, h.config.Runtime, vendor, ep, ep, load, load, input, budget)
			if whole.ID != baseline.ID {
				paths[whole.ID] = whole
			}
		}
	}
	for _, pair := range []struct{ route, P, D string }{
		{model.APND, model.AMD, model.NVIDIA}, {model.NPAD, model.NVIDIA, model.AMD},
	} {
		if !h.verified[pair.route] {
			continue
		}
		for _, p := range workers[pair.P] {
			if !p.prefill {
				continue
			}
			for _, d := range workers[pair.D] {
				if !d.decode || address(p.ep) == address(d.ep) {
					continue
				}
				pd := makePath(req, h.config.Runtime, pair.route, p.ep, d.ep, p.load, d.load, input, budget)
				paths[pd.ID] = pd
			}
		}
	}
	return baseline, paths, ""
}

func (h *Handler) ProcessResults(ctx context.Context, req *scheduling.InferenceRequest, results map[string]*scheduling.ProfileRunResult) (*scheduling.SchedulingResult, error) {
	if route := h.calibrationRoute(ctx, req); route != "" {
		return h.selectCalibration(ctx, req, route, results[candidateProfile])
	}
	base := results[baselineProfile].FirstEndpoint()
	if req == nil || base == nil {
		return nil, errors.New("EPP-Opt has no eligible original endpoint")
	}
	logger := log.FromContext(ctx).V(1)
	fallback := func(reason string) (*scheduling.SchedulingResult, error) {
		logger.Info("EPP-Opt fallback", "requestID", req.RequestID, "reason", reason)
		return selected(base, nil), nil
	}
	// This pool serves one model. Unknown models follow the configured default profile.
	if req.TargetModel != h.config.Model {
		return fallback("unconfigured-model")
	}
	baseline, candidates, reason := h.paths(req, base, results[candidateProfile])
	if reason != "" {
		return fallback(reason)
	}
	chosen := baseline
	var estimates model.Predictions
	if len(candidates) > 1 {
		var elapsed time.Duration
		estimates, elapsed, reason = h.predict(ctx, candidates)
		if _, ok := estimates.ByID[baseline.ID]; !ok && reason == "" {
			reason = "baseline-prediction-unavailable"
		}
		chosen = candidates[model.Choose(baseline.ID, estimates.ByID, baseline.Features.Budget)]
		logger.Info("EPP-Opt prediction", "requestID", req.RequestID, "rpcMs", milliseconds(elapsed), "fallback", reason,
			"version", estimates.Version, "baseline", baseline.ID, "chosen", chosen.ID,
			"baselineEstimate", estimates.ByID[baseline.ID], "chosenEstimate", estimates.ByID[chosen.ID])
	}
	req.PutAttribute(h.stateKey, observationState{observation: model.Observation{
		RequestID: req.RequestID, Features: chosen.Features, Version: estimates.Version,
	}})
	if model.IsPD(chosen.Features.Route) {
		return selected(chosen.decode, chosen.prefill), nil
	}
	return selected(chosen.decode, nil), nil
}

func (h *Handler) load(ep scheduling.Endpoint, input int, now time.Time) (model.Load, bool) {
	m := ep.GetMetrics()
	if !m.Updated() || m.UpdateTime.After(now) || now.Sub(m.UpdateTime) > time.Duration(h.config.MetricsMaxAgeSeconds)*time.Second ||
		m.RunningRequestsSize < 0 || m.WaitingQueueSize < 0 {
		return model.Load{}, false
	}
	raw, _ := ep.Get(attrprefix.PrefixCacheMatchInfoDataKey)
	info, ok := raw.(*attrprefix.PrefixCacheMatchInfo)
	if !ok || info == nil || info.CachedBlockCount() < 0 || info.BlockSizeTokens() < 0 {
		return model.Load{}, false
	}
	return model.Load{
		Cached:  min(input, info.CachedBlockCount()*info.BlockSizeTokens()),
		Running: m.RunningRequestsSize, Waiting: m.WaitingQueueSize,
	}, true
}

func selected(decode, prefill scheduling.Endpoint) *scheduling.SchedulingResult {
	profiles := map[string]*scheduling.ProfileRunResult{
		baselineProfile: {TargetEndpoints: []scheduling.Endpoint{decode}},
	}
	if prefill != nil {
		profiles[prefillProfile] = &scheduling.ProfileRunResult{TargetEndpoints: []scheduling.Endpoint{prefill}}
	}
	return &scheduling.SchedulingResult{PrimaryProfileName: baselineProfile, ProfileResults: profiles}
}

func (h *Handler) PreRequest(ctx context.Context, req *scheduling.InferenceRequest, result *scheduling.SchedulingResult) error {
	route := h.calibrationRoute(ctx, req)
	delete(req.Headers, calibrationHeader)
	delete(req.Headers, calibrationAcceptedHeader)
	if route != "" {
		req.Headers[calibrationAcceptedHeader] = route
	}
	routing.DeleteRoutingHeader(req.Headers, routing.PrefillEndpointHeader)
	delete(req.Headers, model.DecodeHeader)
	if prefill := result.ProfileResults[prefillProfile].FirstEndpoint(); prefill != nil {
		routing.SetRoutingHeader(req.Headers, routing.PrefillEndpointHeader, address(prefill))
		req.Headers[model.DecodeHeader] = address(result.PrimaryEndpoint())
	}
	return nil
}
