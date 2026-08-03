#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${HOME}/.hermes/skills/career-engine"
mkdir -p "$(dirname "${TARGET_DIR}")"
ln -sfn "${SOURCE_DIR}" "${TARGET_DIR}"
printf 'Career Engine skill linked: %s -> %s\n' "${TARGET_DIR}" "${SOURCE_DIR}"
