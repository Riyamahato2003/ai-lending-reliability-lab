"""Create an untracked, secure local staging env file without echoing secrets."""
import os
from pathlib import Path
import secrets

ENV_FILE = Path(".env.staging.local")
KEYS = (
    "STAGING_APP_DB_PASSWORD",
    "STAGING_LAB_DB_PASSWORD",
    "STAGING_MIGRATION_DB_PASSWORD",
    "STAGING_SESSION_SIGNING_SECRET",
    "STAGING_APPROVAL_HMAC_SECRET",
    "STAGING_PRIVACY_HMAC_SECRET",
)

def main() -> None:
    # Exclusive creation prevents accidental overwrite of real staging credentials.
    fd = os.open(ENV_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as destination:
            for key in KEYS:
                destination.write(f"{key}={secrets.token_hex(32)}\n")
            destination.write("STAGING_SMOKE_ENABLE=0\n")
    except BaseException:
        ENV_FILE.unlink(missing_ok=True)
        raise
    print("Generated .env.staging.local with restricted permissions; secrets not displayed.")
    print("This is a disposable synthetic staging environment, not an Internet release.")

if __name__ == "__main__":
    main()
