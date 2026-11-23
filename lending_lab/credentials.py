import base64
import hashlib
import hmac
import os


ITERATIONS = 600_000
SALT_BYTES = 16


def hash_demo_password(username: str, password: str) -> str:
    # Synthetic fixture hashes need not be byte-for-byte reproducible; salts must be unique.
    salt = os.urandom(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def verify_demo_password(username: str, password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_encoded, expected_encoded = stored.split("$", 3)
        salt = base64.b64decode(salt_encoded, validate=True)
        if scheme != "pbkdf2_sha256" or int(rounds) != ITERATIONS or len(salt) != SALT_BYTES:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
        return hmac.compare_digest(base64.b64encode(actual).decode(), expected_encoded)
    except (ValueError, TypeError):
        return False
