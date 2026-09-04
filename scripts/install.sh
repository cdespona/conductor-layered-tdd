#!/usr/bin/env bash
set -euo pipefail

owner="cdespona"
repository="conductor-layered-tdd"
binary="ltdd"
method="binary"
install_dir="${HOME}/.local/bin"

usage() {
  printf '%s\n' 'usage: install.sh [--method binary|go] [--dir PATH]'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    --dir) install_dir="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ "$method" = "go" ]; then
  command -v go >/dev/null 2>&1 || { printf 'go is required for --method go\n' >&2; exit 1; }
  go install "github.com/${owner}/${repository}/cmd/${binary}@latest"
  printf 'installed %s with go install\n' "$binary"
  exit 0
fi

if [ "$method" != "binary" ]; then
  printf 'unsupported method: %s\n' "$method" >&2
  exit 2
fi

command -v curl >/dev/null 2>&1 || { printf 'curl is required\n' >&2; exit 1; }

case "$(uname -s)" in
  Darwin) platform=darwin ;;
  Linux) platform=linux ;;
  *) printf 'unsupported operating system: %s\n' "$(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64) architecture=amd64 ;;
  arm64|aarch64) architecture=arm64 ;;
  *) printf 'unsupported architecture: %s\n' "$(uname -m)" >&2; exit 1 ;;
esac

release_api="https://api.github.com/repos/${owner}/${repository}/releases/latest"
tag=$(curl -fsSL "$release_api" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
if [ -z "$tag" ]; then
  printf 'could not determine the latest release\n' >&2
  exit 1
fi

version=${tag#v}
archive="${repository}_${version}_${platform}_${architecture}.tar.gz"
base_url="https://github.com/${owner}/${repository}/releases/download/${tag}"
temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/ltdd-install.XXXXXX")
trap 'rm -rf "$temporary_dir"' EXIT

curl -fsSL "${base_url}/${archive}" -o "${temporary_dir}/${archive}"
curl -fsSL "${base_url}/checksums.txt" -o "${temporary_dir}/checksums.txt"
expected=$(awk -v name="$archive" '$2 == name { print $1 }' "${temporary_dir}/checksums.txt")
if [ -z "$expected" ]; then
  printf 'checksum not found for %s\n' "$archive" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "${temporary_dir}/${archive}" | awk '{print $1}')
else
  actual=$(shasum -a 256 "${temporary_dir}/${archive}" | awk '{print $1}')
fi
if [ "$expected" != "$actual" ]; then
  printf 'checksum verification failed for %s\n' "$archive" >&2
  exit 1
fi

tar -xzf "${temporary_dir}/${archive}" -C "$temporary_dir"
mkdir -p "$install_dir"
install -m 0755 "${temporary_dir}/${binary}" "${install_dir}/${binary}"
printf 'installed %s %s to %s\n' "$binary" "$tag" "${install_dir}/${binary}"
