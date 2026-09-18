# AWS deployment (explicit operator action; not automatically deployed)

## Architecture and prerequisites

One EC2 server runs Docker Compose, MediaMTX, the NVR, and Caddy. An encrypted retained
EBS data volume holds recordings, SQLite, images, and TLS state. Only 80/443 are public.
SSM provides administration; host Tailscale reaches private Pi RTSP streams. No viewer VPN.

Use an AWS account with permission to create the CloudFormation resources/IAM role,
a domain whose DNS you control, a tailnet, and a full source checkout. Default sizing:
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
This installs Docker, Tailscale and CloudWatch agent but **does not format storage or start recording**.
The installed Docker systemd drop-in requires the mount even on reboot, before container
restart policies run. Review package installation against the official
[Docker](https://docs.docker.com/engine/install/ubuntu/) and
[Tailscale](https://tailscale.com/docs/install/linux) instructions.

## 2. Enroll cameras privately and configure runtime

Run `sudo tailscale up` interactively, not with a key stored in a script.
Enroll each Pi separately. Set a tailnet policy allowing the recorder to reach only
the required Pi RTSP ports; no router forwarding. Use stable tailnet IPs in recorder URLs.
Check connectivity from the MediaMTX container too; host-only connectivity is not sufficient.

Create /etc/rasprec/config/cameras.yml privately from the example and
/etc/rasprec/nvr.env privately from .env.example. Both directories should be mode 700;
the files should be root-owned mode 600. Never copy runtime values into the repository.

Set NVR_AUTH_MODE=session, NVR_USER, NVR_PASSWORD_HASH,
NVR_DOMAIN (hostname only), NVR_PUBLIC_ORIGIN=https://the-same-hostname, and
the camera-password environment variables used by cameras.yml. The AWS override
forces session authentication and hardcodes the mounted data location.
Leave NVR_PASS blank in public mode.

Build the image with `sudo docker build -t rasprec-nvr:latest /opt/rasprec/server`,
then run `sudo docker run --rm -it rasprec-nvr:latest -m nvr.password` in a private
terminal to generate the hash. Store its single-quoted assignment only in nvr.env.
Never enable shell tracing or run non-quiet Compose config commands with real secrets.
Prefer interactive editing over commands containing secrets in shell history.

Point the domain's A record at the Elastic IP. Ensure no stale AAAA record points elsewhere.
Start with `sudo bash /opt/rasprec/server/infra/aws/start.sh`.
The script validates configuration quietly, regenerates camera configuration, builds,
and recreates the services. Caddy manages [HTTPS certificates](https://caddyserver.com/docs/automatic-https).
Camera credentials remain inside private runtime mounts/container environments.

## 3. Verify and operate

- From a non-tailnet browser, load HTTPS, sign in, view camera status, and capture media.
- Verify logged-out direct image/video/API access fails; ports 8080/8554/8888/9996/9997
  must be inaccessible from outside. Check the security group and Compose port bindings.
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
Keep runtime configuration/secrets in a separate encrypted private backup: they are
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
CloudWatch resources, and tailnet devices. Retained resources continue to incur charges.
