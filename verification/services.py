from __future__ import annotations

from typing import Any, Dict, List

from django.utils import timezone

from django_api.models import GitHubAnalysis, LinkedInAnalysis, ResumeUpload, StudentProfile
from verification.ai_agent import AIVerificationAgent
from verification.models import VerificationCheck, VerificationItem
from verification.verification_agent import VerificationAgent, VerificationCandidate

NEXT_STEPS = {
    "profile": "POST /api/profile/",
    "resume": "POST /api/profile/resume/",
    "github": "POST /api/profile/github/",
    "linkedin": "POST /api/profile/linkedin/",
    "verification_status": "GET /api/verification/status/",
    "verification_reanalyze": "POST /api/verification/reanalyze/",
    "verification_items": "GET /api/verification/items/",
    "verification_item_decision": "POST /api/verification/items/<item_id>/decision/",
}

RESOLUTION_BY_ACTION = {
    "confirm": VerificationItem.Resolution.CONFIRMED,
    "ignore": VerificationItem.Resolution.IGNORED,
    "clarify": VerificationItem.Resolution.CLARIFIED,
}

# Resolutions that represent a genuine, informed student decision -- as
# opposed to churn the system itself introduced (AUTO_CLEARED, SUPERSEDED).
# Only these are treated as "already answered, don't re-ask" in
# _reconcile_items() below.
STUDENT_DECISIONS = {
    VerificationItem.Resolution.CONFIRMED,
    VerificationItem.Resolution.IGNORED,
    VerificationItem.Resolution.CLARIFIED,
}


class ItemNotFound(Exception):
    pass


class ItemNotOwned(Exception):
    pass


class ItemAlreadyResolved(Exception):
    pass


def _resolve_github_verified_email(student_id: str) -> str:
    try:
        from accounts.github_oauth import get_connection_for_student_id
    except Exception:
        return ""
    connection = get_connection_for_student_id(student_id)
    return (connection.github_email or "") if connection else ""


def _item_context(item: VerificationItem) -> Dict[str, Any]:
    """Compact shape fed back into the AI prompt so it stays self-consistent
    across reanalyses -- reusing the same dimension/sources for an issue
    it's already raised, and knowing not to re-raise something the student
    already resolved unless the underlying value changed."""
    return {
        "dimension": item.dimension,
        "sources_involved": item.sources,
        "expected_value": item.expected_value,
        "found_value": item.found_value,
        "resolution": item.resolution or None,
        "student_note": item.student_note or None,
    }


def _run_engine(
    *,
    check: VerificationCheck,
    expected_name: str,
    profile_facts: Dict[str, Any],
    resume_data: Dict[str, Any],
    github_data: Dict[str, Any],
    linkedin_data: Dict[str, Any],
    github_verified_email: str,
    sources_present: Dict[str, bool],
) -> Dict[str, Any]:
    """Tries the AI holistic judge first; falls back to the deterministic
    field-by-field comparator if the LLM call fails for any reason (missing
    API key, network error, malformed response). Records which engine
    actually produced the result on `check.engine` so a degraded run is
    visibly distinguishable, not silently passed off as a full AI analysis."""
    open_items_context = [_item_context(i) for i in check.items.filter(is_resolved=False)]

    try:
        result = AIVerificationAgent().analyze(
            expected_name=expected_name,
            profile_facts=profile_facts,
            resume_data=resume_data,
            github_data=github_data,
            linkedin_data=linkedin_data,
            sources_present=sources_present,
            open_items_context=open_items_context,
        )
        check.engine = VerificationCheck.Engine.AI
        return result
    except Exception:
        result = VerificationAgent().analyze_rule_based(
            expected_name=expected_name,
            expected_email=profile_facts.get("email") or "",
            expected_institution=profile_facts.get("institution") or "",
            expected_major=profile_facts.get("major") or "",
            expected_work_months=profile_facts.get("work_months"),
            resume_data=resume_data,
            github_data=github_data,
            linkedin_data=linkedin_data,
            github_verified_email=github_verified_email,
            sources_present=sources_present,
        )
        check.engine = VerificationCheck.Engine.RULE_FALLBACK
        return result


def _reconcile_items(check: VerificationCheck, candidates: List[VerificationCandidate]) -> None:
    """
    Diffs a fresh analysis against the check's existing item history so a
    student's past decisions survive reanalysis:

    - An open item whose key no longer appears among the fresh candidates
      is auto-cleared (the disagreement resolved itself, e.g. the student
      fixed their profile name to match).
    - An open item whose key is still flagged but the found value changed
      (source was reuploaded with different data) is superseded, and a
      fresh open item takes its place.
    - A key with no open item, but whose most recent item was a genuine
      student decision (confirm/ignore/clarify) against the *same* found
      value, is left alone -- already answered, don't re-ask.
    - Everything else (first time seeing this key, or the found value moved
      on from what was last decided) gets a fresh open item.
    """
    fresh_by_key = {c.key: c for c in candidates}
    now = timezone.now()

    all_items = list(check.items.all())
    open_items = [item for item in all_items if not item.is_resolved]

    for item in open_items:
        candidate = fresh_by_key.get(item.key)
        if candidate is None:
            item.is_resolved = True
            item.resolution = VerificationItem.Resolution.AUTO_CLEARED
            item.resolved_at = now
            item.save(update_fields=["is_resolved", "resolution", "resolved_at", "updated_at"])
        elif candidate.found != item.found_value:
            item.is_resolved = True
            item.resolution = VerificationItem.Resolution.SUPERSEDED
            item.resolved_at = now
            item.save(update_fields=["is_resolved", "resolution", "resolved_at", "updated_at"])

    # Most recent item per key, after the closures above (created_at desc ->
    # first hit per key is the latest).
    latest_by_key: Dict[str, VerificationItem] = {}
    for item in sorted(all_items, key=lambda i: i.created_at, reverse=True):
        latest_by_key.setdefault(item.key, item)

    for key, candidate in fresh_by_key.items():
        latest = latest_by_key.get(key)

        if latest is not None and not latest.is_resolved:
            continue  # still open, unchanged -- untouched by the closure pass above

        if (
            latest is not None
            and latest.is_resolved
            and latest.resolution in STUDENT_DECISIONS
            and latest.found_value == candidate.found
        ):
            continue  # student already decided this exact disagreement -- don't re-ask

        VerificationItem.objects.create(
            verification_check=check,
            key=candidate.key,
            dimension=candidate.dimension,
            sources=list(candidate.sources),
            severity=candidate.severity,
            confidence=candidate.confidence,
            expected_value=candidate.expected,
            found_value=candidate.found,
            message=candidate.message,
        )


def _recompute_status(check: VerificationCheck) -> str:
    if check.missing_sources:
        return VerificationCheck.Status.INCOMPLETE
    if check.items.filter(is_resolved=False).exists():
        return VerificationCheck.Status.NEEDS_REVIEW
    return VerificationCheck.Status.VERIFIED


def _status_message(status_value: str, open_count: int) -> str:
    if status_value == VerificationCheck.Status.ERROR:
        return "Verification check failed."
    if status_value == VerificationCheck.Status.INCOMPLETE:
        return "Upload the remaining sources before verification can complete."
    if status_value == VerificationCheck.Status.NEEDS_REVIEW:
        noun = "item" if open_count == 1 else "items"
        return f"{open_count} verification {noun} need your review."
    return "All uploaded sources match your profile. Verification complete."


def serialize_item(item: VerificationItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "key": item.key,
        "dimension": item.dimension,
        "sources": item.sources,
        "severity": item.severity,
        "confidence": item.confidence,
        "expected_value": item.expected_value,
        "found_value": item.found_value,
        "message": item.message,
        "is_resolved": item.is_resolved,
        "resolution": item.resolution or None,
        "student_note": item.student_note or "",
        "created_at": item.created_at,
        "resolved_at": item.resolved_at,
    }


def serialize_check(check: VerificationCheck, include_items: bool = True) -> Dict[str, Any]:
    items = list(check.items.all()) if include_items else []
    open_count = sum(1 for i in items if not i.is_resolved) if include_items else check.items.filter(is_resolved=False).count()

    payload: Dict[str, Any] = {
        "status": check.status,
        "verified": check.status == VerificationCheck.Status.VERIFIED,
        "message": _status_message(check.status, open_count),
        "student_id": check.student.student_id,
        "missing_sources": check.missing_sources,
        "pending_items_count": open_count,
        "engine": check.engine or None,
        "last_analyzed_at": check.last_analyzed_at,
        "next_steps": NEXT_STEPS,
    }
    if check.status == VerificationCheck.Status.ERROR:
        payload["error"] = check.last_error
    if include_items:
        payload["items"] = [serialize_item(i) for i in items]
    return payload


def run_verification(student_id: str, user: Any = None) -> Dict[str, Any]:
    """
    Single source of truth for (re)computing a student's verification
    state. Always reads the latest resume/GitHub/LinkedIn rows from the DB
    -- there is no caching layer -- so calling this again after any
    reupload or profile edit IS the "reanalyze" action.
    """
    profile = StudentProfile.objects.filter(student_id=student_id).first()

    if profile is None:
        return {
            "status": VerificationCheck.Status.INCOMPLETE,
            "verified": False,
            "message": "Student profile is not created yet.",
            "student_id": student_id,
            "missing_sources": ["profile", "resume", "github", "linkedin"],
            "pending_items_count": 0,
            "items": [],
        }

    check, _ = VerificationCheck.objects.get_or_create(student=profile)

    try:
        latest_resume = ResumeUpload.objects.filter(student=profile).order_by("-created_at").first()
        latest_github = GitHubAnalysis.objects.filter(student=profile).order_by("-created_at").first()
        latest_linkedin = LinkedInAnalysis.objects.filter(student=profile).order_by("-created_at").first()

        expected_name = profile.name or ""
        if not expected_name and user is not None:
            expected_name = getattr(user, "first_name", "") or (getattr(user, "email", "") or "").split("@")[0]

        profile_facts = {
            "name": expected_name,
            "email": profile.email or "",
            "institution": profile.institution or "",
            "major": profile.major or "",
            "graduation_year": profile.graduation_year,
            "work_months": profile.work_months,
        }

        result = _run_engine(
            check=check,
            expected_name=expected_name,
            profile_facts=profile_facts,
            resume_data=latest_resume.extracted_data if latest_resume else {},
            github_data=latest_github.result if latest_github else {},
            linkedin_data=latest_linkedin.extracted if latest_linkedin else {},
            github_verified_email=_resolve_github_verified_email(student_id),
            sources_present={
                "resume": latest_resume is not None,
                "github": latest_github is not None,
                "linkedin": latest_linkedin is not None,
            },
        )

        _reconcile_items(check, result["candidates"])

        check.missing_sources = result["missing_sources"]
        check.last_error = ""
        check.last_analyzed_at = timezone.now()
        check.status = _recompute_status(check)
        check.save()

        profile.verified = check.status == VerificationCheck.Status.VERIFIED
        profile.save(update_fields=["verified", "updated_at"])

    except Exception as exc:
        check.status = VerificationCheck.Status.ERROR
        check.last_error = str(exc)
        check.save(update_fields=["status", "last_error", "engine", "updated_at"])

    return serialize_check(check)


def list_items(student_id: str, filter_status: str = "open") -> List[Dict[str, Any]]:
    profile = StudentProfile.objects.filter(student_id=student_id).first()
    if profile is None:
        return []

    check = VerificationCheck.objects.filter(student=profile).first()
    if check is None:
        return []

    queryset = check.items.all()
    if filter_status == "open":
        queryset = queryset.filter(is_resolved=False)
    elif filter_status == "resolved":
        queryset = queryset.filter(is_resolved=True)

    return [serialize_item(item) for item in queryset]


def resolve_item(*, student_id: str, item_id: int, action: str, note: str = "") -> Dict[str, Any]:
    """Applies a student's confirm/ignore/clarify decision to one item, and
    rolls the parent check's status forward (e.g. to VERIFIED once every
    open item has been answered)."""
    try:
        item = VerificationItem.objects.select_related("verification_check__student").get(pk=item_id)
    except VerificationItem.DoesNotExist:
        raise ItemNotFound()

    if item.verification_check.student.student_id != student_id:
        raise ItemNotOwned()
    if item.is_resolved:
        raise ItemAlreadyResolved()

    item.is_resolved = True
    item.resolution = RESOLUTION_BY_ACTION[action]
    item.student_note = note
    item.resolved_at = timezone.now()
    item.save(update_fields=["is_resolved", "resolution", "student_note", "resolved_at", "updated_at"])

    check = item.verification_check
    check.status = _recompute_status(check)
    check.save(update_fields=["status", "updated_at"])

    check.student.verified = check.status == VerificationCheck.Status.VERIFIED
    check.student.save(update_fields=["verified", "updated_at"])

    return {"item": serialize_item(item), "check": serialize_check(check)}
