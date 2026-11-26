#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
upstream=${1:?usage: prepare.sh UPSTREAM_DIRECTORY}
cd "$upstream"
mkdir -p eppopt/model eppopt/handler
cp "$root"/model/*.go eppopt/model/
cp "$root"/handler/*.go eppopt/handler/
python3 - <<'PY'
from pathlib import Path
p = Path('cmd/epp/main.go')
s = p.read_text()
if 'eppopt/handler' not in s:
    anchor = '\t"github.com/llm-d/llm-d-router/cmd/epp/runner"'
    register = '\tctx := ctrl.SetupSignalHandler()'
    assert anchor in s and register in s
    s = s.replace(anchor, anchor + '\n\t"github.com/llm-d/llm-d-router/eppopt/handler"\n\t"github.com/llm-d/llm-d-router/pkg/epp/framework/interface/plugin"', 1)
    s = s.replace(register, register + '\n\tplugin.Register(handler.Type, plugin.StabilityAlpha, handler.Factory)', 1)
    p.write_text(s)
# Keep the official NIXL execution path and permit a decoder URL inside the cluster.
p = Path('cmd/pd-sidecar/main.go')
s = p.read_text()
if 'EPP_OPT_DECODER_URL' not in s:
    assert '\t"context"' in s and '\tproxyServer := proxy.NewProxy(opts.Config)' in s
    s = s.replace('\t"context"', '\t"context"\n\t"net/url"', 1)
    s = s.replace('\tproxyServer := proxy.NewProxy(opts.Config)', '''\tif remote := os.Getenv("EPP_OPT_DECODER_URL"); remote != "" {
		decoder, err := url.ParseRequestURI(remote)
		if err != nil || decoder.Host == "" || decoder.Scheme != "http" {
			logger.Error(err, "Invalid EPP_OPT_DECODER_URL")
			return 1
		}
		opts.DecoderURL = decoder
	}

	proxyServer := proxy.NewProxy(opts.Config)''', 1)
    p.write_text(s)
PY
