# RaspRec server

Multi-camera recording and a small photo/video website, separate from the Pi software.
Use a **full Git clone** on the recorder host. No server code is installed by the Pi installer.

## Features

- Continuous RTSP recording; configurable 24-hour default retention.
- Motion clips and full-stream-resolution JPEGs; seven-day saved-media default retention.
- Manual photo and 15-second video capture, with durable pending/ready/failed states.
- Live view, filtered event gallery, downloads, and archive playback.
- One administrator: HTTPS session login for public hosting, optional Basic auth for private legacy installations.
- Bounded capture queue/workers and subprocess timeouts, automatic stream reconnects.
- No AI classification or offline camera synchronization.

See [AWS deployment](docs/aws.md), [migration](docs/migration.md), and [recording configuration](docs/nvr.md).

## Private local setup

Run `bash server/scripts/up.sh` from the repository root once to create templates.
Edit ignored `server/.env` and `server/config/cameras.yml`. For private LAN/Tailscale
testing set NVR_AUTH_MODE=basic and a strong NVR_PASS (never the placeholder).
Set DATA_DIR to an absolute path on a real disk and rerun the launcher.
Open http://localhost:8080. Do not expose this Basic-auth HTTP listener publicly.

For public hosting use the AWS runbook: session cookies deliberately require HTTPS.
A photo uses the camera's stream resolution; it does not interrupt streaming for
a separate sensor capture. Offline cameras cannot capture; cloud recording gaps
are not backfilled. Photo events can work for live-only cameras; videos require recording.

## Development and checks

Python 3.12+ and ffmpeg are required. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt pytest pytest-asyncio
(cd server && ../.venv/bin/python -m pytest -q)
.venv/bin/python -m pytest device/tests -q
node --check server/nvr/web/app.js
```

Generate a password hash **privately**, never in CI logs or a tracked file:
`cd server && ../.venv/bin/python -m nvr.password`.
The printed environment assignment is single-quoted so Compose preserves dollar signs.
Changing the password should be accompanied by deleting sessions from the private SQLite
database while the service is stopped, to invalidate existing logins.

Only one Uvicorn process should run: it owns camera workers and a globally bounded
login limiter (10 attempts/minute). Do not configure multiple web workers.
The capture queue defaults to 32 jobs and two workers. Pending captures interrupted
by restart become explicitly failed rather than silently generating a new photo.
Retention excludes active jobs; crash leftovers are removed at startup.

## Manual smoke test

With two configured cameras, confirm both appear in Live; trigger motion, take a photo,
and request a clip. In Events filter by camera/source/type/date; open and download images
and video. Disconnect one camera and verify the other continues recording. Restart the
server during a capture: it must become failed, not pending forever. Check archive playback.
For session mode, sign out and confirm direct media URLs are inaccessible.

## Security

Never commit environment files, actual camera configurations, data, logs, certificates,
password hashes, or cloud credentials. The Docker context is allowlisted to application
code and dependencies. Run the CI secret scan locally before committing:

```sh
gitleaks git --redact --no-banner
gitleaks dir --redact --no-banner .
```

These tools supplement review; a clean scan does not prove absence of secrets.
Never publish raw scan reports containing secret values. Rotate real leaked credentials.
