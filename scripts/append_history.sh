#!/usr/bin/env bash
# Compatibility wrapper for harness.history.

set -euo pipefail

python -m harness.history "$@"
