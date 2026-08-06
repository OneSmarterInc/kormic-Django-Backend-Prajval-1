from __future__ import annotations

from institutes.identity import make_institute_id
from institutes.models import Institute


def register_institute(
    name: str,
    contact_email: str = "",
    contact_phone: str = "",
    address: str = "",
) -> Institute:
    """Create an Institute row with an auto-generated unique id -- the whole
    registration flow (no setup phase, unlike universities.register_university,
    since institutes carry no persona/agent configuration)."""
    institute_id = make_institute_id(name)
    return Institute.objects.create(
        id=institute_id,
        name=name.strip(),
        contact_email=contact_email.strip(),
        contact_phone=contact_phone.strip(),
        address=address.strip(),
    )
