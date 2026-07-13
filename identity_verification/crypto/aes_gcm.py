from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any, Dict

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from Crypto.Cipher import AES
except ImportError as exc:  # pragma: no cover
    AES = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _decode_key() -> bytes:
    configured = getattr(settings, "IDENTITY_AES_KEY_B64", "")
    if configured:
        try:
            key = base64.b64decode(configured, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ImproperlyConfigured("IDENTITY_AES_KEY_B64 must be valid base64.") from exc
    elif getattr(settings, "IDENTITY_ALLOW_DEV_KEY_CUSTODY", False):
        seed = getattr(settings, "SECRET_KEY", "").encode("utf-8")
        key = hashlib.sha3_256(b"kormic-dev-identity-aes:" + seed).digest()
    else:
        raise ImproperlyConfigured("IDENTITY_AES_KEY_B64 is required outside development custody mode.")
    if len(key) != 32:
        raise ImproperlyConfigured("IDENTITY_AES_KEY_B64 must decode to exactly 32 bytes for AES-256-GCM.")
    return key


def encrypt(plaintext: bytes, *, associated_data: bytes = b"") -> Dict[str, str]:
    if AES is None:  # pragma: no cover
        raise ImproperlyConfigured("pycryptodome is required for AES-256-GCM.") from _IMPORT_ERROR
    nonce = secrets.token_bytes(12)
    cipher = AES.new(_decode_key(), AES.MODE_GCM, nonce=nonce)
    if associated_data:
        cipher.update(associated_data)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return {
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }


def decrypt(envelope: Dict[str, Any], *, associated_data: bytes = b"") -> bytes:
    if AES is None:  # pragma: no cover
        raise ImproperlyConfigured("pycryptodome is required for AES-256-GCM.") from _IMPORT_ERROR
    try:
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        tag = base64.b64decode(envelope["tag"], validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid AES-GCM envelope.") from exc
    cipher = AES.new(_decode_key(), AES.MODE_GCM, nonce=nonce)
    if associated_data:
        cipher.update(associated_data)
    return cipher.decrypt_and_verify(ciphertext, tag)
