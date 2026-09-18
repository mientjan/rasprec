# RaspRec NVR

A small recording station for the streams rasprec publishes. It records
continuously, cuts a clip whenever something moves, throws old footage away on
a schedule, and gives you one password-protected page to browse it all.

It runs in Docker on a **separate machine** — an always-on x86 box or a Pi 4/5
with a USB SSD. It does not run on the camera Pi: that Pi has a separate, constrained streaming memory
budget and an SD card you do not want to write video to.

Nothing on the camera Pi changes. The `view` user `run.sh` already creates has
`read` permission, which is all the recorder needs.

## What it is made of

| Container | Image | Job |
|---|---|---|
| `nvr-config` | built here | One-shot. Turns `cameras.yml` into `mediamtx.yml`, then exits. |
| `mediamtx` | `bluenviron/mediamtx:1.19.3` | Pulls every camera, records segments, deletes expired ones, serves playback + HLS. |
| `nvr` | built here | Motion detection, clip extraction, clip retention, web UI. |

MediaMTX does the heavy lifting — reconnecting RTSP ingest, segmented fMP4
recording, retention, and a playback endpoint that returns any
`[start, duration]` window as MP4. The Python container is small because of it.

Only the `nvr` container publishes a port, and it binds to `127.0.0.1` by
default. Live video, clips and archive scrubbing are all proxied through that
one authenticated port, so MediaMTX never has to be exposed.

## Requirements

- Docker with the compose plugin (`curl -fsSL https://get.docker.com | sh`)
- amd64 or arm64 Linux
- A real disk for `DATA_DIR`. Budget roughly **32 GB per 1080p camera per day**
  at 3 Mbps — 72 hours of one camera is about 97 GB.

## Setup

```bash
git clone https://github.com/mientjan/rasprec.git
cd rasprec
./scripts/nvr-up.sh          # first run drops the config templates and stops
```

Edit the two files it created:

**`docker/.env`** — every secret lives here, and it is gitignored.

```ini
NVR_USER=admin
NVR_PASS=pick-something-real   # the UI refuses to start without this
GARAGE_PASS=...                # camera passwords, one per camera
DATA_DIR=/mnt/ssd/nvr          # where recordings and clips go
BIND_ADDR=127.0.0.1            # 0.0.0.0 only behind Tailscale or a trusted LAN
```

**`docker/config/cameras.yml`** — passwords are referenced as `${VAR}`, so this
file stays safe to commit or share.

```yaml
retention:
  continuous: 72h   # the 24/7 archive
  clips_days: 30    # motion clips, kept longer
  segment: 10m      # length of each archive file

motion:
  sensitivity: 25
  min_area: 0.4
  cooldown: 15s
  pre_roll: 5s
  post_roll: 10s

cameras:
  - name: garage
    url: rtsp://view:${GARAGE_PASS}@garage-pi:8554/cam

  - name: driveway
    url: rtsp://admin:${DRIVEWAY_PASS}@192.168.1.40:554/Preview_01_main
    sub_url: rtsp://admin:${DRIVEWAY_PASS}@192.168.1.40:554/Preview_01_sub
    motion:
      min_area: 1.0
      mask:
        - [0, 0, 100, 20]   # ignore the road across the top
```

Then run `./scripts/nvr-up.sh` again. Re-run it after any edit to
`cameras.yml` — `docker compose restart` is not enough, because `mediamtx.yml`
has to be regenerated first.

## Camera settings

| Key | Default | Meaning |
|---|---|---|
| `name` | — | Lowercase, digits, `_` or `-`. Becomes the MediaMTX path and the folder name. |
| `url` | — | The stream that gets recorded. |
| `sub_url` | none | A low-resolution stream to run detection on. Big CPU saver for 4K or H.265 cameras. Without it, detection reads `url`. |
| `record` | `true` | Set to `false` for a camera you only want to watch live. |

### Motion settings

Set them globally under `motion:` and override per camera.

| Key | Default | Meaning |
|---|---|---|
| `sensitivity` | `25` | Per-pixel brightness difference that counts as changed, 0–255. Lower is touchier. |
| `min_area` | `0.4` | Percent of the frame that must change. Raise it if leaves and rain trigger events. |
| `consecutive` | `3` | Motion frames in a row before an event opens. Filters out single-frame noise. |
| `cooldown` | `15s` | Quiet time before an event is closed. Short pauses do not split an event in two. |
| `pre_roll` | `5s` | Archive kept before the trigger. |
| `post_roll` | `10s` | Archive kept after the last motion. |
| `min_duration` | `1s` | Events shorter than this are dropped. |
| `background_alpha` | `0.02` | How fast the background model adapts. A subject who stops moving is absorbed after roughly `1/background_alpha` frames — about 12 seconds at the default. Lower it to hold on to a stationary subject for longer, at the cost of slower adaptation to changing light. |
| `mask` | `[]` | `[[x, y, w, h], ...]` in percent — regions to ignore. |

Detection decodes each stream down to 480×270 grayscale at 4 fps, which costs a
few percent of a core per camera.

## How recording works

The archive is written once, continuously. A "clip" is not a second recording —
when an event closes, the recorder asks MediaMTX's playback server for exactly
that window plus the pre/post roll and saves the result as an MP4. That means
motion clips can be kept for 30 days while the full archive rolls over every 3,
without doubling the write load on the disk.

```
/data
  recordings/<camera>/...            fMP4 archive, purged by MediaMTX
  clips/<camera>/<date>/<time>.mp4   motion clips, purged by the nvr container
  thumbs/<camera>/<date>/<time>.jpg
  events.db                          SQLite index of every event
```

## Security

- The web UI is behind HTTP Basic auth and refuses to start if `NVR_PASS` is
  empty. It is the only thing between the internet and your recordings.
- `BIND_ADDR` is `127.0.0.1` by default. To reach the NVR from your phone,
  install Tailscale on the NVR host (`./setup-tailscale.sh` works there too),
  set `BIND_ADDR=0.0.0.0`, and browse to `http://<tailnet-name>:8080`.
- **Do not forward this port on your router**, the same rule that applies to
  8554 and 8889 on the camera Pi.
- The generated `mediamtx.yml` contains expanded camera passwords. It is
  written to a Docker volume with mode 600 and never touches the repo.

## Troubleshooting

**A camera shows "no signal".** Check the recorder's view of it:

```bash
docker compose -f docker/compose.yml logs -f nvr | grep <camera>
```

ffmpeg errors are logged verbatim. `401` means the password in `.env` is wrong;
a timeout usually means the Pi is unreachable from the NVR host.

**Events fire constantly.** Raise `min_area` first, then `sensitivity`. Mask out
roads, trees and anything with a moving light source.

**Events never fire.** Lower `min_area` to `0.1` and watch the log — the
detector logs the changed-area percentage when an event opens.

**Clips are missing but events exist.** The playback request failed, which is
logged with the reason. The most common cause is a `pre_roll` that reaches back
before the archive starts, right after a fresh install.

**Disk filling up.** Lower `retention.continuous`. MediaMTX deletes segments
itself; the recorder only purges clips and thumbnails.

## Development

The config and detection logic have tests that run on any machine — no Pi, no
Docker, no cameras:

```bash
cd docker
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest
```

Building for both architectures:

```bash
docker buildx build --platform linux/amd64,linux/arm64 docker/
```

## Resource budgets and failure recovery

Defaults target a small installation on an SSD-backed Pi 4/5 or x86 host, not an
arbitrary number of cameras. Both `nvr` and `mediamtx` have a 1 GB RAM ceiling and
two-CPU limit; `nvr-config` has 256 MB and one CPU. These limits are not memory
reservations. Leave host RAM for Linux, filesystem cache and other services.
Tune `NVR_MEMORY_LIMIT`, `NVR_CPUS`, `MTX_MEMORY_LIMIT`, `MTX_CPUS`,
`CONFIG_MEMORY_LIMIT` and `CONFIG_CPUS` in `.env` for the actual camera count,
resolution and codec. Container logs rotate at 10 MB × three files per service.

Clip extraction uses `NVR_CLIP_WORKERS=2` workers and a bounded
`NVR_CLIP_QUEUE_SIZE=32` pending-job queue. Both settings must be positive
integers. If overloaded, the event remains visible without a clip and a warning
is logged; continuous recording is unaffected. Jobs queued or active during
shutdown are cancelled rather than retried at next startup; their event metadata
and continuous recordings remain. Cameras with `record: false` retain motion
metadata but do not attempt archive-based clips.

`motion.max_duration` defaults to `5m` (positive, at most `1h`) and splits
sustained motion into consecutive events. Pre/post-roll may overlap between
clips. Retention must be positive and finite; the segment duration must be at
least one second and no longer than continuous retention. Explicit camera names
must not collide with generated `<camera>_sub` paths.

A motion reader that produces no frame for 15 seconds is killed and reconnected.
FFmpeg decoder threads are bounded. Thumbnails seek before decoding and time out
after 30 seconds; an entire capture job has a five-minute deadline including
post-roll wait and download. Failed or cancelled jobs clean partial files and
reap subprocesses. Very slow storage or extreme post-roll settings may therefore
leave an event without a clip. Existing database and recording formats do not
change.

Measure `docker stats` and inspect logs under realistic multi-camera load before
raising limits. Increasing clip workers also increases simultaneous decoding,
connections and disk activity. Run the regression suite described in README;
real camera/SSD performance still needs an integration and soak test.

Camera URL `${VAR}` substitutions remain literal. Percent-encode special
characters in the username/password portions of RTSP URLs (for example `#` as
`%23`, space as `%20`, `/` as `%2F`). Do not URL-encode the entire URL, and do not
use the stored MediaMTX hash as the login password.
