package proxy

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"

	"k8s.io/utils/set"
	"testing"

	"github.com/llm-d/llm-d-router/eppopt/model"
	reqcommon "github.com/llm-d/llm-d-router/pkg/common/request"
	"github.com/llm-d/llm-d-router/pkg/common/routing"
	"github.com/llm-d/llm-d-router/pkg/sidecar/constants"
)

func TestEPPOptDynamicDecoder(t *testing.T) {
	t.Setenv("EPP_OPT_DYNAMIC_DECODER", "true")
	var missing atomic.Bool
	worker := func(name string) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			var body struct {
				KV map[string]any `json:"kv_transfer_params"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if r.Header.Get(model.DecodeHeader) != "" {
				t.Error("internal routing header reached the backend")
			}
			w.Header().Set("Content-Type", "application/json")
			if body.KV["do_remote_decode"] == true {
				if missing.Load() {
					_, _ = w.Write([]byte("{}"))
					return
				}
				_ = json.NewEncoder(w).Encode(map[string]any{
					"kv_transfer_params": map[string]any{"from": name, "do_remote_prefill": true},
				})
				return
			}
			if body.KV["from"] == nil {
				t.Error("decode did not receive prefill metadata")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"decoded_by": name})
		}))
	}
	amd, nvidia := worker("amd"), worker("nvidia")
	defer amd.Close()
	defer nvidia.Close()
	urlAMD, _ := url.Parse(amd.URL)
	urlNVIDIA, _ := url.Parse(nvidia.URL)

	server := NewProxy(Config{DecoderURL: urlNVIDIA, KVConnector: constants.KVConnectorNIXLV2})
	server.allowlistValidator = &AllowlistValidator{enabled: true, allowedTargets: set.New(urlAMD.Host, urlNVIDIA.Host)}
	server.decoderProxy = server.createDecoderProxyHandler(urlNVIDIA, false)
	handler := server.disaggregatedPrefillHandler(reqcommon.APITypeChatCompletions)
	call := func(p, d string) *httptest.ResponseRecorder {
		t.Helper()
		recorder := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, reqcommon.PathChatCompletions,
			strings.NewReader("{\"model\":\"stub\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":8}"))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set(model.DecodeHeader, d)
		req.Header.Set(routing.PrefillEndpointHeader, p)
		handler.ServeHTTP(recorder, req)
		return recorder
	}
	for _, tc := range []struct{ prefill, decode, name string }{
		{urlNVIDIA.Host, urlAMD.Host, "amd"},
		{urlAMD.Host, urlNVIDIA.Host, "nvidia"},
	} {
		recorder := call(tc.prefill, tc.decode)
		if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), tc.name) ||
			recorder.Header().Get(model.ExecutedHeader) != tc.prefill+","+tc.decode {
			t.Fatalf("failed dynamic decode at %s: %d %s", tc.name, recorder.Code, recorder.Body.String())
		}
	}
	if recorder := call(urlAMD.Host, "127.0.0.1:9999"); recorder.Code != http.StatusForbidden {
		t.Fatalf("decode outside the inference pool returned %d", recorder.Code)
	}
	missing.Store(true)
	if recorder := call(urlAMD.Host, urlNVIDIA.Host); recorder.Code != http.StatusBadGateway || recorder.Header().Get(model.ExecutedHeader) != "" {
		t.Fatalf("missing KV metadata returned %d with headers %v", recorder.Code, recorder.Header())
	}
}
