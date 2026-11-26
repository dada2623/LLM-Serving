package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/peer"

	"github.com/llm-d/llm-d-router/eppopt/model"
	fwkrequest "github.com/llm-d/llm-d-router/pkg/epp/framework/common/request"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/plugin"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/requesthandling"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/scheduling"
)

func TestFixedCalibration(t *testing.T) {
	ctx := peer.NewContext(context.Background(), &peer.Peer{Addr: &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1)}})
	var events bytes.Buffer
	originalLog := calibrationLog
	calibrationLog = log.New(&events, "EPP_OPT_CALIBRATION ", 0)
	t.Cleanup(func() { calibrationLog = originalLog })

	h := &Handler{config: config{Model: "m", Runtime: "r", MetricsMaxAgeSeconds: 10, CalibrationEnabled: true},
		verified: map[string]bool{model.APND: true, model.NPAD: true},
		stateKey: plugin.NewDataKey("request", "calibration"), feedback: make(chan model.Observation, 1)}
	now := time.Now()
	nvidia := worker(model.NVIDIA, "192.0.2.10", now)
	amd := worker(model.AMD, "192.0.2.11", now)
	budget := int64(512)
	results := map[string]*scheduling.ProfileRunResult{
		candidateProfile: {ScoredCandidates: []scheduling.ScoredEndpoint{{Endpoint: amd}, {Endpoint: nvidia}}},
	}
	for _, arm := range []struct {
		route           string
		decode, prefill scheduling.Endpoint
	}{
		{model.AMD, amd, nil}, {model.NVIDIA, nvidia, nil},
		{model.APND, nvidia, amd}, {model.NPAD, amd, nvidia},
	} {
		t.Run(arm.route, func(t *testing.T) {
			req := &scheduling.InferenceRequest{RequestID: "epp-" + arm.route, TargetModel: "m",
				Headers: map[string]string{calibrationHeader: arm.route, "x-request-id": "client-id"},
				Body: &fwkrequest.InferenceRequestBody{MaxOutputTokens: &budget,
					TokenizedRequest: fwkrequest.NewTokenizedRequest([][]uint32{make([]uint32, 2048)})}}
			result, err := h.ProcessResults(ctx, req, results)
			if err != nil {
				t.Fatal(err)
			}
			req.SchedulingResult = result
			if result.PrimaryEndpoint() != arm.decode || result.ProfileResults[prefillProfile].FirstEndpoint() != arm.prefill {
				t.Fatal("incorrect endpoint selection")
			}
			if err := h.PreRequest(ctx, req, result); err != nil {
				t.Fatal(err)
			}
			if req.Headers[calibrationHeader] != "" || req.Headers[calibrationAcceptedHeader] != arm.route {
				t.Fatalf("incorrect forwarding headers: %v", req.Headers)
			}

			events.Reset()
			resp := &requesthandling.Response{Headers: map[string]string{":status": "200"},
				EndOfStream: true, Streaming: true, TerminationCause: requesthandling.TerminationCauseNatural,
				Usage: fwkrequest.Usage{CompletionTokens: 512}, ReceivedTimestamp: now,
				GeneratedTokenTimestamp: now.Add(time.Second), CompleteTimestamp: now.Add(2 * time.Second)}
			if arm.prefill != nil {
				h.ResponseBody(ctx, req, resp, arm.decode.GetMetadata())
				if events.Len() != 0 {
					t.Fatal("unverified P/D execution produced a sample")
				}
				resp.Headers[model.ExecutedHeader] = address(arm.prefill) + "," + address(arm.decode)
			}
			h.ResponseBody(ctx, req, resp, arm.decode.GetMetadata())
			var evidence calibrationEvidence
			if err := json.Unmarshal([]byte(strings.TrimPrefix(events.String(), "EPP_OPT_CALIBRATION ")), &evidence); err != nil {
				t.Fatal(err)
			}
			if evidence.Observation.Features.Route != arm.route || evidence.Observation.Features.Input != 2048 ||
				evidence.Observation.Output != 512 || evidence.Observation.TTFT != 1000 || evidence.Observation.E2E != 2000 ||
				evidence.ClientRequestID != "client-id" || evidence.DecodeEndpoint != address(arm.decode) || len(h.feedback) != 0 {
				t.Fatalf("incorrect calibration evidence or training leakage: %+v", evidence)
			}
		})
	}

	req := &scheduling.InferenceRequest{TargetModel: "m", Headers: map[string]string{calibrationHeader: model.NPAD}}
	for _, caller := range []context.Context{
		context.Background(),
		peer.NewContext(context.Background(), &peer.Peer{Addr: &net.TCPAddr{IP: net.IPv4(10, 0, 0, 1)}}),
	} {
		if h.calibrationRoute(caller, req) != "" {
			t.Fatal("untrusted ext_proc connection activated fixed routing")
		}
	}
	h.config.CalibrationEnabled = false
	if h.calibrationRoute(ctx, req) != "" {
		t.Fatal("disabled calibration accepted a route")
	}
	h.config.CalibrationEnabled = true
	req.Body = &fwkrequest.InferenceRequestBody{MaxOutputTokens: &budget,
		TokenizedRequest: fwkrequest.NewTokenizedRequest([][]uint32{make([]uint32, 2048)})}
	if result, err := h.ProcessResults(ctx, req, nil); result != nil || err == nil {
		t.Fatal("missing endpoint did not fail closed")
	}
	delete(h.verified, model.NPAD)
	if result, err := h.ProcessResults(ctx, req, results); result != nil || err == nil {
		t.Fatal("unverified P/D direction did not fail closed")
	}
}
