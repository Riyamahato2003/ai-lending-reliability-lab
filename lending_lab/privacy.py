"""Keyed privacy digests for values that should not be guessable from retained metadata."""

import hashlib
import hmac
import json
from typing import Any

from lending_lab.config import get_settings


def privacy_hmac(domain: str, value: bytes) -> tuple[str, str]:
    settings = get_settings()
    prefix = ("ai-lending-privacy-v1:" + domain + "\n").encode("ascii")
    digest = hmac.new(settings.privacy_hmac_secret.encode(), prefix + value, hashlib.sha256).hexdigest()
    return digest, settings.privacy_hmac_key_version


def json_hmac(domain: str, value: Any) -> tuple[str, str]:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                           allow_nan=False).encode("utf-8")
    return privacy_hmac(domain, canonical)


def consent_text_hmac(purpose: str, version: str, text: str) -> tuple[str, str]:
    encoded = (purpose + "\0" + version + "\0" + text).encode("utf-8")
    return privacy_hmac("consent-text", encoded)
