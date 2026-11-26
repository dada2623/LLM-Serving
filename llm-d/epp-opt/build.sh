#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
upstream=$(cd "${1:?usage: build.sh UPSTREAM_CHECKOUT IMAGE_TAG}" && pwd)
image=${2:?usage: build.sh UPSTREAM_CHECKOUT IMAGE_TAG}
revision=$(cat "$root/upstream.rev")
if [[ $(git -C "$upstream" rev-parse HEAD) != "$revision" ]]; then
  echo "Expected llm-d-router revision $revision" >&2
  exit 1
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/src" "$work/image"
git -C "$upstream" archive "$revision" | tar -x -C "$work/src"
"$root/prepare.sh" "$work/src"
cp "$root/Dockerfile" "$work/image/"
(
  cd "$work/src"
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o "$work/image/epp" ./cmd/epp
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o "$work/image/pd-sidecar" ./cmd/pd-sidecar
)
docker build --platform linux/amd64 --build-arg "UPSTREAM_REVISION=$revision" -t "$image" "$work/image"
