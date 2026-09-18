#!/bin/bash
# Compatibility entrypoint: intentionally read-only.
set -eo pipefail
echo 'Legacy gpu_mem tuning is deprecated: libcamera uses Linux CMA buffers.'
echo 'No boot settings will be changed. Existing custom settings require manual review.'
if [ -r /proc/meminfo ]; then
    grep -E '^(MemTotal|MemAvailable|CmaTotal|CmaFree):' /proc/meminfo
fi
if command -v vcgencmd >/dev/null; then vcgencmd get_mem gpu || true; fi
