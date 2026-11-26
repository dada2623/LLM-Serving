#!/usr/bin/env bash
set -euo pipefail

usage() { echo "Usage: $0 {rxe|tcp} [--dry-run]" >&2; exit 2; }
transport=${1:-}
case "$transport" in rxe|tcp) ;; *) usage ;; esac
case "${2:-}" in ""|--dry-run) ;; *) usage ;; esac
[[ $# -le 2 ]] || usage

root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
namespace=${LLMD_NAMESPACE:-llm-d-serving}
kube() {
  if [[ -n "${LLMD_KUBECTL_SSH_HOST:-}" ]]; then
    # Quote each argument because SSH executes the command through the remote shell.
    local arg command='k3s kubectl'
    for arg in "$@"; do
      command+=" '${arg//\'/\'\\\'\'}'"
    done
    ssh -T -- "$LLMD_KUBECTL_SSH_HOST" "$command"
  else
    kubectl "$@"
  fi
}

umask 077
backup=$(mktemp -d "${TMPDIR:-/tmp}/llm-d-kv-transport.XXXXXX")
echo "Deployment snapshots and patches in $backup"
vendors=(nvidia amd)
for vendor in "${vendors[@]}"; do
  deployment=vllm-$vendor
  kube -n "$namespace" get deploy "$deployment" -o json > "$backup/$vendor.before.json"
  python3 "$root/render-patch.py" "$transport" < "$backup/$vendor.before.json" > "$backup/$vendor.patch.json"
  kube -n "$namespace" patch deploy "$deployment" --type=strategic --patch-file=/dev/stdin --dry-run=server -o json \
    < "$backup/$vendor.patch.json" > "$backup/$vendor.after.json"
done

python3 - "$backup" <<'PYTHON'
import json
import sys
from pathlib import Path

backup = Path(sys.argv[1])
for vendor in ("nvidia", "amd"):
    before, after = (
        json.loads((backup / f"{vendor}.{name}.json").read_text())["spec"]
        for name in ("before", "after")
    )
    if before["template"] == after["template"]:
        continue
    for source, dest, suffix in ((before, after, "apply"), (after, before, "restore")):
        conditions = [
            {"op": "test", "path": f"/spec/{field}", "value": source[field]}
            for field in ("strategy", "replicas", "template")
        ]
        ops = conditions + [{"op": "replace", "path": "/spec/template", "value": dest["template"]}]
        (backup / f"{vendor}.{suffix}.json").write_text(json.dumps(ops))
PYTHON

patch_template() {
  local vendor=$1 patch=$2
  shift 2
  kube -n "$namespace" patch deploy "vllm-$vendor" --type=json --patch-file=/dev/stdin "$@" \
    < "$backup/$vendor.$patch.json"
}
for vendor in "${vendors[@]}"; do
  if [[ -f "$backup/$vendor.apply.json" ]]; then
    patch_template "$vendor" apply --dry-run=server
  else
    echo "vllm-$vendor already uses the requested template"
  fi
done
[[ "${2:-}" != --dry-run ]] || exit 0

applied=()
for vendor in "${vendors[@]}"; do
  if [[ -f "$backup/$vendor.apply.json" ]] && patch_template "$vendor" apply; then
    applied+=("$vendor")
  elif [[ -f "$backup/$vendor.apply.json" ]]; then
    break
  fi
  if ! kube -n "$namespace" rollout status "deploy/vllm-$vendor" --timeout="${LLMD_ROLLOUT_TIMEOUT:-240s}"; then
    break
  fi
  if [[ "$vendor" == amd ]]; then
    exit 0
  fi
done

echo "Rollout interrupted. Restoring templates changed by this run." >&2
for (( index=${#applied[@]} - 1; index>=0; index-- )); do
  vendor=${applied[index]}
  if patch_template "$vendor" restore; then
    kube -n "$namespace" rollout status "deploy/vllm-$vendor" --timeout="${LLMD_ROLLOUT_TIMEOUT:-240s}" || true
  else
    echo "Could not restore vllm-$vendor automatically. Compare its current template with the snapshots in $backup." >&2
  fi
done
exit 1
