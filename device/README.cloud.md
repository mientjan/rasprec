# Cloud recording without a VPN

The Pi opens an outbound **RTMPS** connection to AWS TCP port 1936. You do not
install Tailscale or forward any home-router ports for this mode. The website
continues to use HTTPS on port 443.

## Setup

1. Set up the server using [the AWS runbook](../server/docs/aws.md). Register one
   push camera in its private cameras.yml and generate a **different random token**
   for each camera. No camera IP address is needed on the server.
2. Install the Pi camera software with the usual root installer. Decline optional
   Tailscale (now the default), or use INSTALL_TAILSCALE=no.
3. Run the root compatibility command: **bash setup-publisher.sh**.
   It installs the distribution's python3-av and CA trust bundle, prompts privately
   for the server hostname, camera name, publishing token, and existing local camera
   username/password. It writes /etc/rasprec/publisher.json, root-owned mode 600.
4. Confirm the camera appears online on the website and a manual clip/photo succeeds.
   A running service is not proof that authentication or recording has succeeded.

The publisher uses the Pi's local authenticated RTSP stream at 127.0.0.1, copies
H.264 packets without re-encoding, and sends RTMP through verified TLS. Only the
local hop is plaintext. H.265 and other codecs are rejected instead of silently
re-encoding or changing transport security.

The implementation uses PyAV (FFmpeg libraries), but TLS is handled by Python's
SSL layer in a separate helper process. This verifies both the CA chain and the
server hostname, independent of FFmpeg's TLS backend. Separating the helper also
avoids blocking its network loop during PyAV's native RTMP handshake.
Credentials are read in memory, not supplied as subprocess command-line arguments.
The verification requirements follow Python's [SSL documentation](https://docs.python.org/3/library/ssl.html#ssl.create_default_context);
server-side permissions and TLS-only ingest use [MediaMTX configuration](https://mediamtx.org/docs/references/configuration-file).

## Operations

- Status: sudo systemctl status rasprec-publisher
- Safe diagnostics: sudo journalctl -u rasprec-publisher -n 30
- Retry after configuration changes: sudo systemctl restart rasprec-publisher
- Stop uploading: sudo systemctl disable --now rasprec-publisher
- Change credentials: rerun bash setup-publisher.sh, then verify delivery.
- The systemd service reads a private credential copy, runs as a dynamic unprivileged
  user, limits memory/CPU, and reconnects after failures. Raw media errors and URLs
  are suppressed to keep credentials out of logs.
- Certificate failures stop publishing. Never work around them by disabling verification.
  Check the domain, server certificate renewal, Pi CA bundle, and both machines' clocks.
- No local offline archive/backfill is added: internet outages still produce gaps.
- Test sustained CPU/RAM on your actual Pi. The publisher adds overhead even though
  it does not encode video.

Direct Pi RTSP/WebRTC ports remain local-network-only unless you deliberately
use the older optional VPN mode. Do not expose those ports publicly.
Setup does not uninstall or stop any existing Tailscale installation. Once the
new path is verified, you may explicitly disable an old tailscaled service yourself.

## Public repository rules

publisher.example.json contains invalid placeholders only. Do not fill it with real
credentials. Use the setup prompt or a root-owned runtime file outside the checkout.
Never share publisher.json, camera tokens, native media debug logs, or certificate keys.

## Publisher exits immediately with ValueError

Older publisher versions reject the permission bits on systemd's runtime
credential copy. With `DynamicUser` and `LoadCredential`, systemd can grant
read access through a named-user ACL. That ACL's mask appears as mode `0440`
to Python even though the owning group itself has no read access.

The publisher accepts this read-only mask only for the root-owned runtime copy
at `/run/credentials/rasprec-publisher.service/publisher.json`, when launched
with systemd's credential environment. Other group/world-accessible configs
are still rejected. Keep the original `/etc/rasprec/publisher.json` private;
do not relax its permissions or disable the service sandbox.

After obtaining the updated device code, update the installed publisher without
re-entering or printing credentials (run from the repository root):

```bash
sudo install -m 0755 device/scripts/publish.py /usr/local/lib/rasprec/publish.py
sudo systemctl restart rasprec-publisher
sudo journalctl -u rasprec-publisher -n 20 --no-pager
```

Failures now identify a safe stage (configuration, local RTSP, TLS connection,
RTMP output, stream-template copy or packet remuxing). Native error messages,
credentials and URLs remain suppressed. If publishing still fails, share only
the sanitized stage/error line, not configuration contents. Confirm delivery
on the server's camera page; service status alone does not prove video arrived.
