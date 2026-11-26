#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
upstream=${1:?usage: prepare.sh UPSTREAM_DIRECTORY}
cd "$upstream"
for pkg in model handler predictor cmd/predictor; do
    mkdir -p "eppopt/$pkg"
    cp "$root/$pkg/"*.go "eppopt/$pkg/"
done
cp "$root/sidecar/"*.go pkg/sidecar/proxy/
cp "$root/observation/"*.go pkg/epp/handlers/

python3 - <<'PY'
from pathlib import Path


def replace(path, anchor, replacement):
    p = Path(path)
    source = p.read_text()
    if replacement in source or ("GeneratedTokenTimestamp time.Time" in replacement and "GeneratedTokenTimestamp" in source):
        return
    if source.count(anchor) != 1:
        raise ValueError(f"Unexpected upstream source in {path}: {anchor}")
    p.write_text(source.replace(anchor, replacement, 1))


replace('cmd/epp/main.go', '\t"github.com/llm-d/llm-d-router/cmd/epp/runner"',
        '''	"github.com/llm-d/llm-d-router/cmd/epp/runner"
	"github.com/llm-d/llm-d-router/eppopt/handler"
	"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/plugin"''')
replace('cmd/epp/main.go', '\tctx := ctrl.SetupSignalHandler()',
        '''	ctx := ctrl.SetupSignalHandler()
	plugin.Register(handler.Type, plugin.StabilityAlpha, handler.Factory)
	plugin.Register(handler.WholeFilterType, plugin.StabilityAlpha, handler.WholeFilterFactory)''')

# Reuse upstream serial NIXLv2 orchestration with a per-request decode target.
replace('pkg/sidecar/proxy/dispatch.go', '\t\tif prefillHostPort != "" {\n\t\t\troute.path',
        '''		var allowed bool
		r, allowed = s.eppOptRoute(r, prefillHostPort)
		if !allowed {
			denySSRF(w, r, logger, span, "decode target", "invalid or outside pool")
			return
		}
		if selected, ok := routeFromContext(r.Context()); ok {
			route.decoder = selected.decode
		}
		if prefillHostPort != "" {
			route.path''')
replace('pkg/sidecar/proxy/proxy_helpers.go',
        '\tdecoderProxy.Transport = s.newProxyTransport(decoderURL.Scheme, decoderInsecureSkipVerify)',
        '\tconfigureEPPOptDecode(decoderProxy)\n\tdecoderProxy.Transport = s.newProxyTransport(decoderURL.Scheme, decoderInsecureSkipVerify)')
replace('pkg/sidecar/proxy/connector_nixlv2.go', '\trewriteCachedTokens := pUsage != nil && len(pKVParams) > 0',
        '''	if _, dynamic := routeFromContext(r.Context()); dynamic && len(pKVParams) == 0 {
		observeStageError(r.Context(), routing.StagePrefill)
		if err := errorBadGateway(w, r); err != nil {
			logger.Error(err, "failed to report missing KV transfer parameters")
		}
		return nil, 0, false, false
	}
	rewriteCachedTokens := pUsage != nil && len(pKVParams) > 0''')
replace('pkg/epp/handlers/server.go', 'firstTokenTimestamp        time.Time',
        'firstTokenTimestamp        time.Time\n\tGeneratedTokenTimestamp time.Time')
replace('pkg/epp/handlers/response.go', 'parseBytes = completeSSELines(ctx, reqCtx, responseBytes, endOfStream)',
        '''parseBytes = completeSSELines(ctx, reqCtx, responseBytes, endOfStream)
		if reqCtx.GeneratedTokenTimestamp.IsZero() && containsGeneratedToken(parseBytes) {
			reqCtx.GeneratedTokenTimestamp = time.Now()
		}''')
replace('pkg/epp/framework/interface/requesthandling/types.go', '\tFirstTokenTimestamp time.Time',
        '\tFirstTokenTimestamp time.Time\n\t// GeneratedTokenTimestamp tracks the first generated OpenAI SSE delta.\n\tGeneratedTokenTimestamp time.Time')
replace('pkg/epp/requestcontrol/director.go', 'response.FirstTokenTimestamp = reqCtx.FirstTokenTimestamp()',
        'response.FirstTokenTimestamp = reqCtx.FirstTokenTimestamp()\n\t\tresponse.GeneratedTokenTimestamp = reqCtx.GeneratedTokenTimestamp')
replace('pkg/epp/handlers/response.go', 'mutation.RemoveHeaders = []string{routing.FailedStageHeader}\n\t}',
        '''mutation.RemoveHeaders = []string{routing.FailedStageHeader}
	}
	if _, ok := reqCtx.Response.Headers["x-epp-opt-executed-pd"]; ok {
		mutation.RemoveHeaders = append(mutation.RemoveHeaders, "x-epp-opt-executed-pd")
	}''')
PY

gofmt -w cmd/epp/main.go pkg/sidecar/proxy/{dispatch,proxy_helpers,connector_nixlv2}.go
gofmt -w pkg/epp/handlers/{response,server}.go pkg/epp/framework/interface/requesthandling/types.go pkg/epp/requestcontrol/director.go
