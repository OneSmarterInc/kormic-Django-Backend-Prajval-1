from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict

from django.conf import settings

from identity_verification.crypto.mldsa import MLDSA44Provider


@dataclass(frozen=True)
class SigningKey:
    epoch: int
    public_key: bytes
    private_key: bytes


class KeyCustodyBackend(ABC):
    @abstractmethod
    def current_epoch(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def sign(self, epoch: int, payload: bytes) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def verify(self, epoch: int, payload: bytes, signature: bytes) -> bool:
        raise NotImplementedError

    @abstractmethod
    def public_key(self, epoch: int) -> bytes:
        raise NotImplementedError


class SoftwareKeyCustody(KeyCustodyBackend):
    """Development-only in-process key custody seam.

    Private keys are process memory only and are never serialized into models.
    Production deployments must replace this with an HSM-backed custody backend.
    """

    _keys: Dict[int, SigningKey] = {}

    def __init__(self):
        if not getattr(settings, "IDENTITY_ALLOW_DEV_KEY_CUSTODY", False):
            raise RuntimeError("SoftwareKeyCustody is development-only. Configure an HSM custody backend for production.")
        self._ensure_epoch(self.current_epoch())

    def current_epoch(self) -> int:
        return int(getattr(settings, "IDENTITY_CURRENT_SIGNING_EPOCH", 1))

    def _ensure_epoch(self, epoch: int) -> SigningKey:
        if epoch not in self._keys:
            public_key, private_key = MLDSA44Provider.keygen()
            self._keys[epoch] = SigningKey(epoch=epoch, public_key=public_key, private_key=private_key)
        return self._keys[epoch]

    def sign(self, epoch: int, payload: bytes) -> bytes:
        key = self._ensure_epoch(epoch)
        return MLDSA44Provider.sign(key.private_key, payload)

    def verify(self, epoch: int, payload: bytes, signature: bytes) -> bool:
        key = self._ensure_epoch(epoch)
        return MLDSA44Provider.verify(key.public_key, payload, signature)

    def public_key(self, epoch: int) -> bytes:
        return self._ensure_epoch(epoch).public_key


def get_key_custody() -> KeyCustodyBackend:
    backend = getattr(settings, "IDENTITY_KEY_CUSTODY_BACKEND", "software-dev")
    if backend != "software-dev":
        raise RuntimeError(f"Unsupported identity key custody backend: {backend}")
    return SoftwareKeyCustody()


def encode_signature(signature: bytes) -> str:
    return base64.b64encode(signature).decode("ascii")


def decode_signature(signature: str) -> bytes:
    return base64.b64decode(signature, validate=True)
