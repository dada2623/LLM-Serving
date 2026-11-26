package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"time"

	"sigs.k8s.io/controller-runtime/pkg/log"

	"github.com/llm-d/llm-d-router/eppopt/model"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/datalayer"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/requesthandling"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/scheduling"
)

func milliseconds(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }

func (h *Handler) post(ctx context.Context, resource string, body, out any) error {
	b, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.config.PredictorURL+resource, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := h.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNoContent {
		return errors.New(resp.Status)
	}
	if out != nil {
		return json.NewDecoder(io.LimitReader(resp.Body, 256<<10)).Decode(out)
	}
	return nil
}

func (h *Handler) predict(ctx context.Context, candidates map[string]path) (model.Predictions, time.Duration, string) {
	start := time.Now()
	ctx, cancel := context.WithTimeout(ctx, time.Duration(h.config.PredictionTimeoutMillis)*time.Millisecond)
	defer cancel()
	batch := model.Batch{Candidates: make([]model.Candidate, 0, len(candidates))}
	for _, p := range candidates {
		batch.Candidates = append(batch.Candidates, p.Candidate)
	}
	sort.Slice(batch.Candidates, func(i, j int) bool { return batch.Candidates[i].ID < batch.Candidates[j].ID })
	var response model.Predictions
	if err := h.post(ctx, "/v1/predict", batch, &response); err != nil {
		return model.Predictions{}, time.Since(start), err.Error()
	}
	for id := range response.ByID {
		if _, ok := candidates[id]; !ok {
			delete(response.ByID, id)
		}
	}
	return response, time.Since(start), ""
}

func (h *Handler) ResponseBody(ctx context.Context, req *scheduling.InferenceRequest, resp *requesthandling.Response, endpoint *datalayer.EndpointMetadata) {
	if !resp.EndOfStream || resp.TerminationCause != requesthandling.TerminationCauseNatural ||
		!resp.Streaming || resp.Headers[":status"] != "200" || endpoint == nil ||
		resp.Usage.CompletionTokens <= 0 || resp.ReceivedTimestamp.IsZero() ||
		!resp.GeneratedTokenTimestamp.After(resp.ReceivedTimestamp) || resp.CompleteTimestamp.Before(resp.GeneratedTokenTimestamp) {
		return
	}
	state, ok := scheduling.ReadRequestAttribute[observationState](req, h.stateKey)
	if !ok {
		return
	}
	o := state.observation
	result := req.SchedulingResult
	if result == nil || result.PrimaryEndpoint() == nil ||
		result.PrimaryEndpoint().GetMetadata().ID != endpoint.ID {
		return
	}
	if model.IsPD(o.Features.Route) {
		p := result.ProfileResults[prefillProfile].FirstEndpoint()
		if p == nil || resp.Headers[model.ExecutedHeader] != address(p)+","+address(result.PrimaryEndpoint()) {
			return
		}
	}
	o.Output = resp.Usage.CompletionTokens
	o.TTFT = milliseconds(resp.GeneratedTokenTimestamp.Sub(resp.ReceivedTimestamp))
	o.E2E = milliseconds(resp.CompleteTimestamp.Sub(resp.ReceivedTimestamp))
	if !o.Valid() {
		return
	}
	if state.calibration {
		recordCalibration(req, o)
		return
	}
	log.FromContext(ctx).V(1).Info("EPP-Opt completed route", "requestID", o.RequestID, "route", o.Features.Route,
		"version", o.Version, "ttftMs", o.TTFT, "e2eMs", o.E2E, "outputTokens", o.Output)
	select {
	case h.feedback <- o:
	default:
		log.FromContext(ctx).V(1).Info("EPP-Opt feedback queue full", "requestID", o.RequestID)
	}
}

func (h *Handler) submitFeedback(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case o := <-h.feedback:
			callCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
			err := h.post(callCtx, "/v1/observe", o, nil)
			cancel()
			if err != nil && ctx.Err() == nil {
				log.FromContext(ctx).V(1).Info("EPP-Opt feedback delivery failed", "requestID", o.RequestID, "error", err.Error())
			}
		}
	}
}
