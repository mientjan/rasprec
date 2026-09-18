#!/bin/bash
# Compatibility entrypoint; implementation lives in device/.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/device/diagnose.sh" "$@"
