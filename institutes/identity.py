from __future__ import annotations


def make_institute_id(name: str) -> str:
    """Slugify to [a-z0-9_]+ (same charset as universities.identity.make_university_id)
    and resolve collisions with a numbered suffix, since this becomes a
    permanent primary key and must be guaranteed unique up front. See
    korgut_backend.slugs.unique_slug."""
    from institutes.models import Institute
    from korgut_backend.slugs import unique_slug

    return unique_slug(
        name,
        exists=lambda candidate: Institute.objects.filter(pk=candidate).exists(),
        default="institute",
    )
