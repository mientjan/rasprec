"""Run privately: python -m nvr.password. Never commit the printed hash."""

from getpass import getpass
from .auth import hasher

if __name__ == "__main__":
    password = getpass("New administrator password: ")
    if len(password) < 12:
        raise SystemExit("Use at least 12 characters.")
    if password != getpass("Repeat password: "):
        raise SystemExit("Passwords did not match.")
    print("NVR_PASSWORD_HASH='" + hasher.hash(password) + "'")
