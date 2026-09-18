#!/bin/bash
# Run explicitly on the dedicated Ubuntu 24.04 server after mounting its data disk.
# Never formats disks, enrolls cameras, reads secrets, or starts RaspRec.
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] && [ "$(dpkg --print-architecture)" = amd64 ] ||
    { echo "This bootstrap targets Ubuntu 24.04 amd64 only." >&2; exit 1; }
mountpoint -q /srv/rasprec || { echo "Mount the data volume first." >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -d -m 0755 /etc/systemd/system/docker.service.d
install -m 0644 "$HERE/docker-mount.conf" /etc/systemd/system/docker.service.d/rasprec-mount.conf
apt-get update
apt-get install -y ca-certificates curl git
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'SOURCES'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl daemon-reload
systemctl enable --now docker
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb -o "$tmp/agent.deb"
dpkg -i "$tmp/agent.deb"
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl     -a fetch-config -m ec2 -c "file:$HERE/cloudwatch.json" -s
install -d -m 0700 /etc/rasprec /etc/rasprec/config
echo "Host prepared. Supply private runtime config, then run start.sh."
