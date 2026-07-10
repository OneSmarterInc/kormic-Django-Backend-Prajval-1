from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

try:
    from dilithium_py.ml_dsa import ML_DSA_44
except ImportError as exc:  # pragma: no cover
    ML_DSA_44 = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class MLDSA44Provider:
    algorithm = "ML-DSA-44"
    import_path = "dilithium_py.ml_dsa.ML_DSA_44"

    @staticmethod
    def _impl():
        if ML_DSA_44 is None:  # pragma: no cover
            raise ImproperlyConfigured("dilithium_py with ML_DSA_44 support is required.") from _IMPORT_ERROR
        return ML_DSA_44

    @classmethod
    def keygen(cls):
        return cls._impl().keygen()

    @classmethod
    def sign(cls, private_key: bytes, message: bytes) -> bytes:
        return cls._impl().sign(private_key, message)

    @classmethod
    def verify(cls, public_key: bytes, message: bytes, signature: bytes) -> bool:
        try:
            return bool(cls._impl().verify(public_key, message, signature))
        except Exception:  # noqa: BLE001
            return False
