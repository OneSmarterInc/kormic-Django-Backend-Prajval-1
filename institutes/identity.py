from __future__ import annotations

import re


def make_institute_id(name: str) -> str:
    """Slugify to [a-z0-9_]+ (same charset as universities.identity.make_university_id)
    and resolve collisions with a numbered suffix, since this becomes a
    permanent primary key and must be guaranteed unique up front."""
    from institutes.models import Institute

    cleaned = str(name or "institute").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned)
    cleaned = cleaned.strip("_") or "institute"
    cleaned = cleaned[:255]

    if not Institute.objects.filter(pk=cleaned).exists():
        return cleaned

    suffix = 2
    while True:
        candidate = f"{cleaned}_{suffix}"[:255]
        if not Institute.objects.filter(pk=candidate).exists():
            return candidate
        suffix += 1
