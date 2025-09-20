#!/bin/bash
# RaspRec One-Line Installer - Copy and paste this command on your Raspberry Pi:
# curl -sSL https://raw.githubusercontent.com/mientjan/rasprec/main/quick-install.sh | bash

set -e
echo "=== RaspRec Quick Install ==="

# Install git if not present
if ! command -v git &> /dev/null; then
    echo "Installing git..."
    sudo apt-get update && sudo apt-get install -y git
fi

# Clone and setup
REPO_URL="https://github.com/mientjan/rasprec.git"
INSTALL_DIR="$HOME/rasprec"

[ -d "$INSTALL_DIR" ] && rm -rf "$INSTALL_DIR"
git clone "$REPO_URL" "$INSTALL_DIR"
cd "$INSTALL_DIR"
chmod +x run.sh

echo ""
echo "✅ Repository cloned to: $INSTALL_DIR"
echo ""
echo "🚀 To complete setup, run:"
echo "   cd $INSTALL_DIR && ./run.sh"
echo ""
echo "📋 Or run setup automatically:"
read -p "Run setup now? (y/N): " -n 1 -r
echo
[[ $REPLY =~ ^[Yy]$ ]] && ./run.sh || echo "Run './run.sh' when ready!"
