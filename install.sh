#!/bin/bash
# Public, standalone Pi installer. Server software is never installed here.
set -euo pipefail
REPO_URL="https://github.com/mientjan/rasprec.git"
INSTALL_DIR="$HOME/rasprec"
command -v git >/dev/null || { echo "Install git first: sudo apt install git" >&2; exit 1; }
if [ -e "$INSTALL_DIR" ]; then
    [ -e "$INSTALL_DIR/.git" ] && git -C "$INSTALL_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
        { echo "$INSTALL_DIR is not a Git checkout; move it aside yourself." >&2; exit 1; }
    if [ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=all)" ]; then
        echo "Checkout has local changes. Back them up and reconcile before updating." >&2
        exit 1
    fi
    git -C "$INSTALL_DIR" fetch origin main
    # Refuse even an ahead-only local branch: never run unexpected local commits.
    git -C "$INSTALL_DIR" merge-base --is-ancestor HEAD FETCH_HEAD ||
        { echo "Local history diverges from main; reconcile it before updating." >&2; exit 1; }
    git -C "$INSTALL_DIR" merge --ff-only FETCH_HEAD
else
    (git sparse-checkout -h 2>&1 || true) | grep -q 'sparse-checkout' ||
        { echo "Upgrade Git to a version supporting sparse checkout and partial clone." >&2; exit 1; }
    git clone --filter=blob:none --sparse "$REPO_URL" "$INSTALL_DIR"
    git -C "$INSTALL_DIR" sparse-checkout set --cone device
fi
echo "Device source is ready in $INSTALL_DIR/device (server dependencies are not installed)."
read -r -p "Run camera setup now? (Y/n): " REPLY
case "$REPLY" in
    n|N) echo "Later: bash \"$INSTALL_DIR/run.sh\"" ;;
    *) exec bash "$INSTALL_DIR/run.sh" ;;
esac
