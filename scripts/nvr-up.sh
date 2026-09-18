#!/bin/bash
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$ROOT/server/scripts/up.sh" ]; then
    echo "Server source is not checked out. Use a full clone on the recorder host." >&2
    exit 1
fi
exec bash "$ROOT/server/scripts/up.sh" "$@"
