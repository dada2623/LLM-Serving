package handler

import (
	"context"
	"encoding/json"
	"log"
	"net"
	"os"
	"strings"

	"google.golang.org/grpc/peer"
	ctrlLog "sigs.k8s.io/controller-runtime/pkg/log"

	"github.com/llm-d/llm-d-router/eppopt/model"
	errcommon "github.com/llm-d/llm-d-router/pkg/common/error"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/scheduling"
)

const (
	calibrationHeader         = "x-epp-opt-calibration-route"
	calibrationAcceptedHeader = "x-epp-opt-calibration-accepted"
)

type observationState struct {
	observation model.Observation
	calibration bool
}

type calibrationEvidence struct {
	ClientRequestID string            `json:"client_request_id"`
	PrefillEndpoint string            `json:"prefill_endpoint"`
	DecodeEndpoint  string            `json:"decode_endpoint"`
	Observation     model.Observation `json:"observation"`
}

var calibrationLog = log.New(os.Stdout, "EPP_OPT_CALIBRATION ", 0)

// The renderer strips client values on all inference listeners. Only the local
// Envoy is trusted to supply the route on its ext_proc connection.
func (h *Handler) calibrationRoute(ctx context.Context, req *scheduling.InferenceRequest) string {
	if !h.config.CalibrationEnabled || req == nil {
		return ""
	}
	route := req.Headers[calibrationHeader]
	if route == "" {
		return ""
	}
	caller, ok := peer.FromContext(ctx)
	if !ok {
		return ""
	}
	addr, ok := caller.Addr.(*net.TCPAddr)
	if !ok || !addr.IP.IsLoopback() {
		return ""
	}
	return route
}

func (h *Handler) selectCalibration(ctx context.Context, req *scheduling.InferenceRequest, route string, candidates *scheduling.ProfileRunResult) (*scheduling.SchedulingResult, error) {
	fail := func(reason string) (*scheduling.SchedulingResult, error) {
		ctrlLog.FromContext(ctx).Info("EPP-Opt calibration rejected", "requestID", req.RequestID,
			"clientRequestID", req.Headers["x-request-id"], "route", route, "reason", reason)
		return nil, errcommon.Error{Code: errcommon.ServiceUnavailable, Msg: "EPP-Opt calibration: " + reason}
	}
	if req.TargetModel != h.config.Model {
		return fail("unconfigured model")
	}
	if model.IsPD(route) && !h.verified[route] {
		return fail("P/D direction unverified")
	}
	_, paths, reason := h.paths(req, nil, candidates)
	if reason != "" {
		return fail(reason)
	}
	var chosen path
	for id, candidate := range paths {
		if candidate.Features.Route == route && (chosen.ID == "" || id < chosen.ID) {
			chosen = candidate
		}
	}
	if chosen.ID == "" {
		return fail("no eligible endpoint with a fresh feature snapshot")
	}

	req.PutAttribute(h.stateKey, observationState{calibration: true, observation: model.Observation{
		RequestID: req.RequestID, Features: chosen.Features,
	}})
	if model.IsPD(route) {
		return selected(chosen.decode, chosen.prefill), nil
	}
	return selected(chosen.decode, nil), nil
}

func recordCalibration(req *scheduling.InferenceRequest, observation model.Observation) {
	decode := req.SchedulingResult.PrimaryEndpoint()
	prefill := req.SchedulingResult.ProfileResults[prefillProfile].FirstEndpoint()
	if prefill == nil {
		prefill = decode
	}
	record, err := json.Marshal(calibrationEvidence{
		ClientRequestID: strings.TrimSpace(req.Headers["x-request-id"]), Observation: observation,
		PrefillEndpoint: address(prefill), DecodeEndpoint: address(decode),
	})
	if err == nil {
		calibrationLog.Print(string(record))
	}
}
