# RaspRec contributor instructions

This repository is PUBLIC. Never commit real credentials, password hashes, camera
URLs containing credentials, AWS keys, Tailscale keys, session tokens, private
keys, recordings, databases, runtime configuration, or sensitive logs. Examples
and tests must use synthetic values. Never print discovered secret values.
Rotate exposed credentials; obtain approval before rewriting history.

- device/ owns Raspberry Pi software; server/ owns the recorder, website and AWS.
- Root scripts are compatibility entrypoints. Keep device-only sparse installs working.
- Resolve script resources relative to the script, not the caller's directory.
- Never discard local changes during installer updates.
- Keep secrets out of Docker build contexts and infrastructure templates/user data.
- Use IAM roles, not embedded AWS keys. Do not deploy infrastructure without approval.
- Tests: python -m pytest device/tests; cd server && python -m pytest.
