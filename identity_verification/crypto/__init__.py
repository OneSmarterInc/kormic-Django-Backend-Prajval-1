"""Identity verification crypto boundary.

This package is server-side only. It exposes deterministic canonicalization,
AES-256-GCM envelope encryption, and ML-DSA-44 proof-token helpers behind a
key-custody seam that can be replaced with HSM custody later.
"""

ML_DSA_PARAMETER_SET = "ML-DSA-44"
AES_ENVELOPE_FIELDS = ("nonce", "ciphertext", "tag")
