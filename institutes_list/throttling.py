# institutes_list/throttling.py
# rate limiting for the anonymous claim endpoints (claim/start,
# claim/verify -- public by design, the OTP is the auth). Per-IP throttling
# reuses DRF's AnonRateThrottle, which already resolves the real client IP
# correctly (honors X-Forwarded-For / NUM_PROXIES) -- no reason to hand-roll
# that. 
from __future__ import annotations

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle


class ClaimEmailRateThrottle(SimpleRateThrottle):

    def get_cache_key(self, request, view):
        ident = str(request.data.get("email") or request.data.get("token") or "").strip().lower()
        if not ident:
            # Nothing to key on -- the per-IP throttle still applies.
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ClaimStartIPThrottle(AnonRateThrottle):
    scope = "claim_start_ip"


class ClaimStartEmailThrottle(ClaimEmailRateThrottle):
    scope = "claim_start_email"


class ClaimVerifyIPThrottle(AnonRateThrottle):
    scope = "claim_verify_ip"


class ClaimVerifyEmailThrottle(ClaimEmailRateThrottle):
    scope = "claim_verify_email"
