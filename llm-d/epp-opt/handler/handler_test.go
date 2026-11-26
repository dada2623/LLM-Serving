package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/llm-d/llm-d-router/eppopt/model"
	"github.com/llm-d/llm-d-router/pkg/common/routing"
	fwkrequest "github.com/llm-d/llm-d-router/pkg/epp/framework/common/request"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/datalayer"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/plugin"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/requesthandling"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/scheduling"
	attrprefix "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/datalayer/attribute/prefix"
)

func worker(vendor, host string, at time.Time) scheduling.Endpoint {
	ep := scheduling.NewEndpoint(&datalayer.EndpointMetadata{
		ID: datalayer.ID{Name: vendor}, Address: host, Port: "8000",
		Labels: map[string]string{vendorLabel: vendor},
	}, &datalayer.Metrics{UpdateTime: at}, nil)
	ep.Put(attrprefix.PrefixCacheMatchInfoDataKey, attrprefix.NewPrefixCacheMatchInfo(0, 1, 576))
	return ep
}

func TestSelectReverseAndFallback(t *testing.T) {
	predicted := map[string]model.Prediction{
		model.AMD:    {TTFT: 500, TPOT: 25},
		model.NVIDIA: {TTFT: 500, TPOT: 30},
		model.APND:   {TTFT: 1200, TPOT: 27},
		model.NPAD:   {TTFT: 5500, TPOT: 8},
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/predict" {
			t.Error("unexpected prediction path")
			return
		}
		var batch model.Batch
		if err := json.NewDecoder(r.Body).Decode(&batch); err != nil {
			t.Error(err)
			return
		}
		answer := model.Predictions{Version: "test-v1", ByID: make(map[string]model.Prediction)}
		for _, c := range batch.Candidates {
			if p, ok := predicted[c.Features.Route]; ok {
				answer.ByID[c.ID] = p
			}
		}
		_ = json.NewEncoder(w).Encode(answer)
	}))
	defer server.Close()
	h := &Handler{config: config{Model: "m", Runtime: "r", PredictorURL: server.URL, PredictionTimeoutMillis: 50, MetricsMaxAgeSeconds: 5},
		client: server.Client(), verified: map[string]bool{model.APND: true, model.NPAD: true},
		stateKey: plugin.NewDataKey("request", "test"), feedback: make(chan model.Observation, 1)}
	nvidia := worker(model.NVIDIA, "192.0.2.10", time.Now())
	amd := worker(model.AMD, "192.0.2.11", time.Now())
	budget := int64(512)
	req := &scheduling.InferenceRequest{RequestID: "req", TargetModel: "m", Headers: map[string]string{},
		Body: &fwkrequest.InferenceRequestBody{MaxOutputTokens: &budget,
			TokenizedRequest: fwkrequest.NewTokenizedRequest([][]uint32{make([]uint32, 1024)})}}
	results := map[string]*scheduling.ProfileRunResult{
		baselineProfile:  {TargetEndpoints: []scheduling.Endpoint{nvidia}},
		candidateProfile: {ScoredCandidates: []scheduling.ScoredEndpoint{{Endpoint: amd}, {Endpoint: nvidia}}},
	}

	check := func(decode, prefill scheduling.Endpoint) {
		t.Helper()
		result, err := h.ProcessResults(context.Background(), req, results)
		if err != nil {
			t.Fatal(err)
		}
		req.SchedulingResult = result
		if result.PrimaryEndpoint() != decode || result.ProfileResults[prefillProfile].FirstEndpoint() != prefill {
			t.Fatalf("unexpected scheduling result %+v", result)
		}
		if err := h.PreRequest(context.Background(), req, result); err != nil {
			t.Fatal(err)
		}
		if prefill != nil && (req.Headers[model.DecodeHeader] != address(decode) || req.Headers[routing.PrefillEndpointHeader] != address(prefill)) {
			t.Fatalf("unexpected execution headers: %v", req.Headers)
		}
		if prefill == nil && (req.Headers[model.DecodeHeader] != "" || req.Headers[routing.PrefillEndpointHeader] != "") {
			t.Fatalf("stale execution headers: %v", req.Headers)
		}
	}
	check(amd, nvidia)

	start := time.Now()
	resp := &requesthandling.Response{
		Headers: map[string]string{":status": "200"}, EndOfStream: true, Streaming: true, TerminationCause: requesthandling.TerminationCauseNatural,
		Usage: fwkrequest.Usage{CompletionTokens: 21}, ReceivedTimestamp: start,
		GeneratedTokenTimestamp: start.Add(4 * time.Second), CompleteTimestamp: start.Add(5 * time.Second),
	}
	h.ResponseBody(context.Background(), req, resp, amd.GetMetadata())
	if len(h.feedback) != 0 {
		t.Fatal("unconfirmed split produced a training label")
	}
	resp.Headers[model.ExecutedHeader] = address(nvidia) + "," + address(amd)
	h.ResponseBody(context.Background(), req, resp, amd.GetMetadata())
	select {
	case o := <-h.feedback:
		if o.Features.Route != model.NPAD || o.Output != 21 || o.TTFT != 4000 || o.E2E != 5000 || o.Version != "test-v1" {
			t.Fatalf("unexpected training observation: %+v", o)
		}
	default:
		t.Fatal("confirmed split produced no training label")
	}

	delete(h.verified, model.NPAD)
	check(amd, nil)
	delete(predicted, model.NVIDIA)
	check(nvidia, nil)
	server.Close()
	check(nvidia, nil)
}
