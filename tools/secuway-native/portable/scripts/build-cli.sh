#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
output_dir="${1:-$project_dir/dist/cli}"
go_binary="${GO_BINARY:-go}"

mkdir -p "$output_dir"

build_target() {
  local target_os="$1"
  local target_arch="$2"
  local cgo_enabled="$3"
  local suffix=""
  if [[ "$target_os" == "windows" ]]; then
    suffix=".exe"
  fi
  local output="$output_dir/secuway-${target_os}-${target_arch}${suffix}"
  echo "BUILD ${target_os}/${target_arch} -> ${output}"
  (
    cd "$project_dir"
    CGO_ENABLED="$cgo_enabled" GOOS="$target_os" GOARCH="$target_arch" \
      "$go_binary" build \
      -buildvcs=false \
      -trimpath \
      -ldflags="-s -w -buildid=" \
      -o "$output" \
      ./cmd/secuway
  )
}

build_target linux amd64 0
build_target linux arm64 0
build_target windows amd64 0
build_target windows arm64 0

if [[ "$(uname -s)" == "Darwin" ]]; then
  build_target darwin amd64 1
  build_target darwin arm64 1
else
  echo "SKIP darwin: Security.framework builds must run on macOS" >&2
fi

if command -v shasum >/dev/null 2>&1; then
  (
    cd "$output_dir"
    shasum -a 256 secuway-* > SHA256SUMS
  )
elif command -v sha256sum >/dev/null 2>&1; then
  (
    cd "$output_dir"
    sha256sum secuway-* > SHA256SUMS
  )
else
  echo "WARN no SHA-256 utility found; checksums were not written" >&2
fi

echo "OK ${output_dir}"
