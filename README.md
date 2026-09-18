# RaspRec

One public repository, two independently installed projects:

- **[Device](device/README.md):** Raspberry Pi capture and secure outbound RTMPS publishing, without a VPN.
- **[Server](server/README.md):** multi-camera recording, photo/video gallery, and AWS hosting.

## Install on a Raspberry Pi

Download the root `install.sh` and run it with Bash, or use an existing checkout:
`bash run.sh`. The installer uses a device-only sparse/partial clone for new
installations. It does not install Docker, the website, or server dependencies.
Existing full checkouts remain full checkouts.

## Secure cloud connection

For cloud recording, each Pi initiates verified RTMPS to the server. No Tailscale
or home-router port forwarding is needed. See [Pi cloud setup](device/README.cloud.md)
and [AWS setup](server/docs/aws.md). Existing private pull-mode installations remain supported.

## Development

Use a full clone to work on both projects. Each has its own tests and setup.
Root camera scripts remain compatibility entrypoints. See [migration](server/docs/migration.md)
for existing recorder installations.

## Public repository

Commit examples only—never credentials, local configuration, recordings or logs.
Read [AGENTS.md](AGENTS.md) before contributing.
