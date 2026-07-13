from __future__ import annotations

import hashlib
from typing import Any, Dict

from django.conf import settings
from django.utils import timezone

from identity_verification.crypto.canonical import canonical_bytes
from identity_verification.crypto.custody import decode_signature, encode_signature, get_key_custody


def sha3_256_hex(payload: bytes) -> str:
    return hashlib.sha3_256(payload).hexdigest()


def build_current_head(previous_head: str, record_hash: str, session_id: str) -> str:
    payload = canonical_bytes({
        "previous_head": previous_head or "",
        "record_hash": record_hash,
        "session_id": session_id,
    })
    return sha3_256_hex(payload)


def unsigned_proof_payload(proof: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "student_code": proof["student_code"],
        "verification_record_hash": proof["verification_record_hash"],
        "current_head": proof["current_head"],
        "freshness_timestamp": proof["freshness_timestamp"],
        "challenge": proof["challenge"],
        "epoch": proof["epoch"],
        "authority_identifier": proof["authority_identifier"],
    }


def sign_proof_record(*, student_code: str, verification_record_hash: str, current_head: str, challenge: str) -> Dict[str, Any]:
    custody = get_key_custody()
    epoch = custody.current_epoch()
    proof = {
        "student_code": student_code,
        "verification_record_hash": verification_record_hash,
        "current_head": current_head,
        "freshness_timestamp": timezone.now().isoformat(),
        "challenge": challenge,
        "epoch": epoch,
        "authority_identifier": getattr(settings, "IDENTITY_AUTHORITY_IDENTIFIER", "kormic-dev-authority"),
    }
    signature = custody.sign(epoch, canonical_bytes(proof))
    proof["signature"] = encode_signature(signature)
    return proof


def verify_proof_record(proof: Dict[str, Any]) -> bool:
    custody = get_key_custody()
    signature = decode_signature(proof["signature"])
    payload = canonical_bytes(unsigned_proof_payload(proof))
    return custody.verify(int(proof["epoch"]), payload, signature)
