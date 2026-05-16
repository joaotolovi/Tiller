#!/bin/sh
set -eu

REPO_OWNER="joaotolovi"
REPO_NAME="Tiller"
REPO_REF="master"
ARCHIVE_URL="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/refs/heads/${REPO_REF}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf '[tiller-bootstrap] ERROR: required command not found: %s\n' "$1" >&2
    exit 1
  }
}

main() {
  require_command curl
  require_command tar
  require_command bash

  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT

  archive_path="$tmp_dir/tiller.tar.gz"
  extract_dir="$tmp_dir/extract"

  mkdir -p "$extract_dir"

  printf '[tiller-bootstrap] Downloading %s/%s@%s\n' "$REPO_OWNER" "$REPO_NAME" "$REPO_REF"
  curl -fsSL "$ARCHIVE_URL" -o "$archive_path"
  tar -xzf "$archive_path" -C "$extract_dir"

  source_dir="$extract_dir/${REPO_NAME}-${REPO_REF}"
  [ -d "$source_dir" ] || {
    printf '[tiller-bootstrap] ERROR: extracted installer directory not found\n' >&2
    exit 1
  }

  exec bash "$source_dir/installers/install.sh" "$@"
}

main "$@"
