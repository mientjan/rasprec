# Migrating the old docker/ recorder

1. Before updating, stop the old stack without removing volumes:
   `docker compose -f docker/compose.yml down` (never use `-v`).
2. Back up the data directory and local configuration privately.
3. Update source. Copy your ignored `docker/.env` and
   `docker/config/cameras.yml` into their matching `server/` locations.
4. Set DATA_DIR in server/.env to the **absolute existing data directory**.
   A former relative ./data path otherwise resolves under server/ instead of docker/.
5. Set NVR_AUTH_MODE=basic to retain private legacy login, or configure session
   authentication as described in the server README.
6. Run `bash server/scripts/up.sh`. The Compose project remains rasprec-nvr.
   Database migrations are additive. Keep your backup for rollback.

The launcher never copies secrets, relocates recordings, or changes an existing
data path automatically. New defaults do not override explicit retention settings.
