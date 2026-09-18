# AWS deployment (explicit operator action; not automatically deployed)

## Architecture and prerequisites

One EC2 server runs Docker Compose, MediaMTX, the NVR, and Caddy. An encrypted retained
EBS data volume holds recordings, SQLite, images, and TLS state. Public ports are 80 (certificate challenges/HTTPS redirect), 443 (website), and
1936 (TLS-only RTMPS ingest). SSM provides administration. Each Pi initiates an
authenticated outbound connection; there is no Tailscale dependency and no router forwarding.

Use an AWS account with permission to create the CloudFormation resources/IAM role,
a domain whose DNS you control, and a full source checkout. Default sizing:
eu-west-1, t3.large, 250 GiB data plus 30 GiB root. T3 uses standard CPU credits to avoid
surprise surplus-credit billing; sustained work can exhaust credits. Measure before adding cameras.

**Approval/cost gate:** Before creating the stack, save a current regional estimate using
[AWS Pricing Calculator](https://calculator.aws/). Include 730 instance-hours/month,
root/data gp3, public IPv4, snapshot storage, CloudWatch/SNS, and internet playback egress.
Record the date/assumed camera count privately. No fixed monthly price is promised here.
At 3 Mbps each camera uploads about 32.4 GB/day; three need 9 Mbps sustained upstream
and 97 GB/day of continuous storage, plus retained event clips. Heavy motion may exhaust
250 GiB well before seven days; increase storage or reduce retention. Outages lose footage.

## 1. Provision, then mount storage

Validate locally with `cfn-lint server/infra/aws/stack.yml`. After explicit approval,
deploy via the CloudFormation console or CLI with CAPABILITY_IAM; select eu-west-1
(or your chosen region), instance type, disk size and optional alert email. Confirm the
SNS subscription. No credentials belong in template parameters.

Use the stack outputs to open an SSM Session Manager shell. Confirm the session agent
is online; do not open SSH as a workaround. Inspect the attached data volume using
`lsblk -f` and its EBS volume ID/serial. Device names on Nitro differ from /dev/sdf.

**Never automatically format a disk.** For a brand-new empty volume only, explicitly
verify its volume ID, absence of partitions/filesystems, and that it is not the root disk
before formatting it as ext4. For existing/restored media, mount its existing filesystem.
Add its UUID to /etc/fstab for /srv/rasprec; use ext4 defaults and fsck pass 2.
Do not use an arbitrary /dev/nvme name in fstab. Mount and verify with `findmnt /srv/rasprec`.

Clone the repository to /opt/rasprec (full checkout). Run:
`sudo bash /opt/rasprec/server/infra/aws/bootstrap-host.sh`.
This installs Docker and CloudWatch agent but **does not format storage or start recording**.
The installed Docker systemd drop-in requires the mount even on reboot, before container
restart policies run. Review package installation against the official
[Docker](https://docs.docker.com/engine/install/ubuntu/) instructions.

## 2. Register cameras and obtain certificates

Create /etc/rasprec/config/cameras.yml privately from **config/cameras.push.example.yml**
and /etc/rasprec/nvr.env privately from .env.example. Directories should be mode 700;
files should be root-owned mode 600. Never put actual values in the repository.

For each camera add source: push and a publish_token environment reference. Generate
a different 32-byte random token per camera with secrets.token_urlsafe(32), store it
in nvr.env, and transfer it privately to that Pi's setup prompt. Each token only
authorizes publishing the named camera; it does not grant website or playback access.
The server rejects missing, short, shared, or non-URL-safe tokens.

Set NVR_AUTH_MODE=session, NVR_USER, NVR_PASSWORD_HASH, NVR_DOMAIN (hostname only),
NVR_PUBLIC_ORIGIN=https://the-same-hostname, and NVR_ACME_EMAIL for certificate account
contact. Leave NVR_PASS blank. The AWS override forces session auth and the mounted
data location. NVR_INTERNAL_READER_IP must match the NVR container's private address.

Build with sudo docker build -t rasprec-nvr:latest /opt/rasprec/server and run
sudo docker run --rm -it rasprec-nvr:latest -m nvr.password in a private terminal.
Store the single-quoted assignment only in nvr.env, never in source or shell history.

Point the domain's A record at the Elastic IP. Remove stale AAAA records unless IPv6
is deliberately configured. Ports 80, 443 and 1936 must reach this server.
Run sudo bash /opt/rasprec/server/infra/aws/start.sh.

The launcher starts Caddy first for HTTP certificate challenges, obtains a separate
RTMPS certificate with Certbot, then starts the recorder. Caddy continues to manage
the website's HTTPS certificate. These certificates cover the same hostname but have
separate renewal lifecycles. RTMPS is strict: there is no plaintext 1935 listener.
Certificate keys/ACME state live outside Git in /etc/rasprec/letsencrypt and are mounted
read-only only into MediaMTX, not the web application.

The launcher enables rasprec-certificates.timer (twice daily, with jitter). On successful
renewal it restarts MediaMTX only if the certificate changed; cameras reconnect automatically.
This causes a short recording gap. Watch the timer's status and journal; ACME renewal
requires the DNS/HTTP challenge path to remain reachable. Expired/untrusted/wrong-host
certificates are rejected by the Pi, never bypassed. Test renewal with Certbot's
renew --dry-run before relying on unattended service.

Finally run bash setup-publisher.sh on each Pi as described in
[device cloud setup](../../../device/README.cloud.md). Verify the camera in the website.
No camera IP addresses or inbound home-router ports are required.

### Rotate or revoke one camera

Change only that camera's token in the private server environment and rerun start.sh.
Recreating MediaMTX disconnects existing publishers, so the old token is immediately
unusable for new connections. Install the matching new token on the intended Pi and
restart its publisher. To revoke without replacement, rotate to an unused token,
or remove the camera entry and recreate the stack if other cameras remain. The
configuration requires at least one camera; stop the recorder to retire all cameras. Other credentials stay unchanged, though the restart briefly
interrupts all streams. Restart rather than merely editing a file: existing sessions
must also be closed. Never log or publish old/new tokens.

## 3. Verify and operate

- From a normal browser, load HTTPS, sign in, view camera status, and capture media.
- Verify logged-out direct image/video/API access fails; ports 8080/8554/8888/9996/9997
  and plaintext 1935 must be inaccessible from outside. Port 1936 accepts only TLS
  and authenticated camera publishing; camera credentials must not read any stream. Check the security group and Compose port bindings.
- Confirm CloudWatch disk metrics arrive and alarms/alert subscription work.
  Missing disk metrics are an alarm, not silently healthy.
- Reboot and confirm the EBS mount precedes Docker startup; confirm recordings survive.
- Start with one camera; monitor disk growth, CPU credits, memory, and event volume.
- Use the same --env-file and both Compose files for operational commands. Use bounded
  logs privately; do not publish raw configurations or logs.
- Apply OS and application dependency updates regularly, testing them first.

A restart marks interrupted captures failed. There is no HA or automatic offline backfill.
The login limiter is global for this single-user server, so repeated external attempts
can temporarily block sign-in; use private access if that tradeoff is unacceptable.

## 4. Backup, replacement and removal

Stop the stack with the same Compose files (never down -v), then take an encrypted
EBS snapshot. Restart recording afterward; this creates a brief recording gap.
Keep runtime configuration/secrets and /etc/rasprec/letsencrypt in a separate encrypted private backup: they are
not on the media volume by default. TLS data and SQLite are on the data volume.

For restore, stop services, attach a snapshot-restored volume in the same AZ, verify
its UUID, update fstab, mount it, restore private configuration, and start.
Test restoration before relying on backups.

Before instance replacement, stop recording, snapshot, and detach the retained data volume.
Coordinate CloudFormation attachment changes rather than allowing concurrent writers.
Do not change availability zones without a snapshot-based migration; EBS is AZ-bound.
DeletionPolicy/UpdateReplacePolicy retain the media volume, not a complete working deployment.
See [AWS EBS CloudFormation behavior](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-volume.html).

When retiring: stop recording; decide whether to retain or securely delete media/backups;
delete the stack; explicitly review retained EBS volumes, snapshots, orphaned Elastic IPs,
CloudWatch resources, and obsolete device credentials. Retained resources continue to incur charges.
