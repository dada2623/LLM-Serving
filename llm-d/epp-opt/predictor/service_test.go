package predictor

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/llm-d/llm-d-router/eppopt/model"
)

func post(t *testing.T, handler http.Handler, path string, value any, want int) *httptest.ResponseRecorder {
	t.Helper()
	var body bytes.Buffer
	if err := json.NewEncoder(&body).Encode(value); err != nil {
		t.Fatal(err)
	}
	reply := httptest.NewRecorder()
	handler.ServeHTTP(reply, httptest.NewRequest(http.MethodPost, path, &body))
	if reply.Code != want {
		t.Fatalf("%s returned %d, want %d: %s", path, reply.Code, want, reply.Body)
	}
	return reply
}

func TestOnlineFitPredictionAndReload(t *testing.T) {
	dir := t.TempDir()
	s, err := New("", dir)
	if err != nil {
		t.Fatal(err)
	}
	query := model.Features{Model: "test", Runtime: "v1", Route: model.NPAD, Input: 2048, Budget: 128,
		Prefill: model.Load{Cached: 576, Running: 2}, Decode: model.Load{Running: 2}}
	unknown := query
	unknown.Runtime = "v2"
	batch := model.Batch{Candidates: []model.Candidate{{ID: "warm", Features: query}, {ID: "unknown", Features: unknown}}}
	predict := func(service *Service) model.Predictions {
		t.Helper()
		response := post(t, service.Handler(), "/v1/predict", batch, http.StatusOK)
		var estimates model.Predictions
		if err := json.NewDecoder(response.Body).Decode(&estimates); err != nil {
			t.Fatal(err)
		}
		return estimates
	}
	if got := predict(s); len(got.ByID) != 0 {
		t.Fatalf("cold predictor returned estimates: %v", got)
	}

	for n := 0; n < 24; n++ {
		f := query
		f.Prefill.Cached = (n % 2) * 1152
		f.Prefill.Running, f.Decode.Running = (n%2)*4, (n%2)*4
		first := float64(300 + (n%2)*100)
		post(t, s.Handler(), "/v1/observe", model.Observation{
			RequestID: fmt.Sprintf("seed-%d", n), Features: f, Output: 121, TTFT: first, E2E: first + 1200,
		}, http.StatusNoContent)
	}
	if err := s.Train(); err != nil {
		t.Fatal(err)
	}
	fitted := predict(s)
	if p := fitted.ByID["warm"]; fitted.Version == "" || p.TTFT < 300 || p.TTFT > 400 || math.Abs(p.TPOT-10) > 1e-9 || len(fitted.ByID) != 1 {
		t.Fatalf("warm interpolation and runtime isolation failed: %v", fitted)
	}

	reloaded, err := New("", dir)
	if err != nil {
		t.Fatal(err)
	}
	if got := predict(reloaded); got.Version != fitted.Version || got.ByID["warm"] != fitted.ByID["warm"] {
		t.Fatalf("persisted model changed on restart: %v", got)
	}
	query.Prefill.Running = 20
	batch.Candidates[0].Features = query
	if got := predict(reloaded); len(got.ByID) != 0 {
		t.Fatalf("predicted outside calibrated coverage: %v", got)
	}
}
