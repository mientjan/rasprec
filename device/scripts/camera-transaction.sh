#!/bin/bash
# Sourced by run.sh. Preserve previous files and service states on failed activation.
# CAMERA_ROOT is only a fixture prefix for unit tests; run.sh always sets it empty.
camera_snapshot() {
    BACKUP_SUFFIX=".backup.$(date +%Y%m%d_%H%M%S).$$"
    OLD_ACTIVE=false; OLD_ENABLED=false; LEGACY_ACTIVE=false; LEGACY_ENABLED=false
    systemctl is-active --quiet mediamtx && OLD_ACTIVE=true
    systemctl is-enabled --quiet mediamtx && OLD_ENABLED=true
    systemctl is-active --quiet rtsp-camera && LEGACY_ACTIVE=true
    systemctl is-enabled --quiet rtsp-camera && LEGACY_ENABLED=true
    for file in /usr/local/bin/mediamtx /usr/local/etc/mediamtx.yml /etc/systemd/system/mediamtx.service; do
        if [ -e "${CAMERA_ROOT}${file}" ]; then
            sudo cp -p "${CAMERA_ROOT}${file}" "${CAMERA_ROOT}${file}${BACKUP_SUFFIX}"
        fi
    done
}

camera_rollback() {
    echo 'Activation failed; restoring previous camera installation.' >&2
    sudo systemctl stop mediamtx || true
    sudo systemctl disable mediamtx 2>/dev/null || true
    for file in /usr/local/bin/mediamtx /usr/local/etc/mediamtx.yml /etc/systemd/system/mediamtx.service; do
        if [ -e "${CAMERA_ROOT}${file}${BACKUP_SUFFIX}" ]; then
            sudo cp -p "${CAMERA_ROOT}${file}${BACKUP_SUFFIX}" "${CAMERA_ROOT}${file}"
        else
            sudo rm -f "${CAMERA_ROOT}${file}"
        fi
    done
    sudo systemctl daemon-reload || true
    if [ "$OLD_ENABLED" = true ]; then sudo systemctl enable mediamtx || true
    else sudo systemctl disable mediamtx 2>/dev/null || true; fi
    sudo systemctl reset-failed mediamtx 2>/dev/null || true
    if [ "$OLD_ACTIVE" = true ]; then sudo systemctl start mediamtx || true; fi
    if [ "$LEGACY_ENABLED" = true ]; then sudo systemctl enable rtsp-camera || true; fi
    if [ "$LEGACY_ACTIVE" = true ]; then sudo systemctl start rtsp-camera || true; fi
}
