#!/usr/bin/env bash
set -euo pipefail

model_id=${MODEL_ID:-Qwen/Qwen3-0.6B}
revision=${MODEL_REVISION:-master}
destination=${1:-/srv/llm-serving/models/Qwen3-0.6B}
api_base=${MODELSCOPE_API_BASE:-https://www.modelscope.cn}

command -v curl >/dev/null || { echo '缺少 curl' >&2; exit 127; }
command -v python3 >/dev/null || { echo '缺少 python3' >&2; exit 127; }
command -v sha256sum >/dev/null || { echo '缺少 sha256sum' >&2; exit 127; }

mkdir -p "$destination"
manifest=$(mktemp)
trap 'rm -f "$manifest" "$destination/.manifest.tsv"' EXIT

curl -fsSL --retry 3 --connect-timeout 10 \
  "$api_base/api/v1/models/$model_id/repo/files?Revision=$revision&Recursive=true" \
  -o "$manifest"

python3 - "$manifest" "$destination" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2]) / '.manifest.tsv'
with out.open('w') as f:
    for item in payload['Data']['Files']:
        f.write(f"{item['Path']}\t{item['Size']}\t{item['Sha256']}\n")
PY

while IFS=$'\t' read -r path size expected_sha; do
  [ -n "$path" ] || continue
  target="$destination/$path"
  mkdir -p "$(dirname "$target")"
  if [ -f "$target" ] && [ "$(wc -c < "$target")" -eq "$size" ]; then
    printf '已有 %s\n' "$path"
  else
    printf '下载 %s，大小 %s bytes\n' "$path" "$size"
    curl -fL --retry 3 --connect-timeout 10 -C - \
      "$api_base/models/$model_id/resolve/$revision/$path" \
      -o "$target"
  fi
  actual_sha=$(sha256sum "$target" | awk '{print $1}')
  [ "$actual_sha" = "$expected_sha" ] || {
    echo "SHA256 校验失败：$path" >&2
    exit 1
  }
done < "$destination/.manifest.tsv"

rm -f "$destination/.manifest.tsv"
printf '模型已准备完成：%s\n' "$destination"
du -sh "$destination"
