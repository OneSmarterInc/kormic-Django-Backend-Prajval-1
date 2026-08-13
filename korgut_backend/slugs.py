# korgut_backend/slugs.py
# Shared slug-with-collision-suffix logic. Used wherever a human-entered
# name becomes a permanent primary key (universities.identity.make_university_id,
# institutes.identity.make_institute_id) and must be guaranteed unique up
# front (B4: this was duplicated near-identically in both places).

from __future__ import annotations

import re
from typing import Callable


def unique_slug(name: str, *, exists: Callable[[str], bool], default: str, max_length: int = 255) -> str:
    """Slugify `name` to [a-z0-9_]+ and resolve collisions with a numbered
    suffix. `exists(candidate)` should return True if that primary key is
    already taken."""
    cleaned = str(name or default).strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned)
    cleaned = cleaned.strip("_") or default
    cleaned = cleaned[:max_length]

    if not exists(cleaned):
        return cleaned

    suffix = 2
    while True:
        candidate = f"{cleaned}_{suffix}"[:max_length]
        if not exists(candidate):
            return candidate
        suffix += 1
