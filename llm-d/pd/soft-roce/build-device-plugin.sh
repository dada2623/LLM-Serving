#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo "Usage: $0 <output-binary>" >&2; exit 2; }
output=$(python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$1")
module=github.com/squat/generic-device-plugin
revision=v0.0.0-20261123092340-0ae001198898
export GOTOOLCHAIN=${GOTOOLCHAIN:-go1.26.11}
source_dir=$(go mod download -json "$module@$revision" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["Dir"])')
cd "$source_dir"
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -mod=readonly -ldflags='-s -w' -o "$output" .
