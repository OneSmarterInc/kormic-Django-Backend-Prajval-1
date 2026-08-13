"""
Institute-list intake and the student claim flow.

"""
import csv
import hashlib
import io
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.models import Account
from institutes.models import Institute

from .models import ListedStudent, UniversityStudentList
from .throttling import (
    ClaimStartEmailThrottle,
    ClaimStartIPThrottle,
    ClaimVerifyEmailThrottle,
    ClaimVerifyIPThrottle,
)

OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
CLAIM_SESSION_MAX_AGE = 15 * 60  # seconds a verified claim session stays valid
REQUIRED_COLUMNS = ["full_name", "email", "field_of_study", "degree_level", "expected_graduation"]
OPTIONAL_COLUMNS = ["phone", "year_in_college", "program_name", "city", "state"]

_claim_signer = signing.TimestampSigner(salt="institutes-list.claim")


def _mask_email(email: str) -> str:
    """r•••••@gmail.com -- first character, then dots, then the domain."""
    local, _, domain = email.partition("@")
    if not domain:
        return "•••••"
    keep = local[0] if local else ""
    return f"{keep}•••••@{domain}"


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _prefill_payload(row: ListedStudent) -> dict:
    return {
        "full_name": row.full_name,
        "email": row.email,
        "field_of_study": row.field_of_study,
        "degree_level": row.degree_level,
        "expected_graduation": row.expected_graduation,
        "phone": row.phone,
        "year_in_college": row.year_in_college,
        "program_name": row.program_name,
        "city": row.city,
        "state": row.state,
        "institute_id": row.institute_id,
        "institute_name": row.source_list.institute.name,
    }


def _find_claimable(email: str = "", token: str = ""):
    """Latest unclaimed row on an ACTIVE list, by email or claim token."""
    qs = ListedStudent.objects.filter(
        status=ListedStudent.Status.UNCLAIMED,
        source_list__status=UniversityStudentList.Status.ACTIVE,
    )
    if token:
        return qs.filter(claim_token=token).first()
    if email:
        return qs.filter(email__iexact=email.strip()).order_by("-created_at").first()
    return None


# ---------------------------------------------------------------------------
# Intake (university / superuser side)
# ---------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upload_list(request):
    """
    POST /api/institute-lists/upload/
    multipart: file=<csv>; fields: institute_id, contact_name, contact_email,
    contact_verification.
    Creates the provenance record and one unclaimed pre-profile per accepted
    row. Rejected rows are reported back, accepted rows ingested (spec S3).
    Re-uploads reconcile by email within the same institute: unclaimed rows
    update; CLAIMED rows are never overwritten (the student owns their data
    after claiming) -- changed claimed rows are reported for review instead.
    """
    account = getattr(request.user, "account", None)
    role = getattr(account, "role", "")
    if role not in (Account.Role.INSTITUTE, Account.Role.SUPERUSER):
        return Response({"error": "institute or superuser role required"}, status=status.HTTP_403_FORBIDDEN)

    upload = request.FILES.get("file")
    institute_id = str(request.data.get("institute_id") or "").strip()
    contact_name = str(request.data.get("contact_name") or "").strip()
    contact_email = str(request.data.get("contact_email") or "").strip()
    if not upload or not institute_id or not contact_name or not contact_email:
        return Response(
            {"error": "file, institute_id, contact_name and contact_email are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Ownership: an institute account may only upload on behalf of itself.
    # Only a superuser may upload for an institute_id other than their own.
    if role == Account.Role.INSTITUTE and account.institute_id != institute_id:
        return Response(
            {"error": "you may only upload lists for your own institute"},
            status=status.HTTP_403_FORBIDDEN,
        )

    institute = Institute.objects.filter(pk=institute_id).first()
    if institute is None:
        return Response({"error": "no registered institute with that institute_id"}, status=status.HTTP_404_NOT_FOUND)

    try:
        text = upload.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        header = [h.strip() for h in (reader.fieldnames or [])]
    except Exception:
        return Response({"error": "could not parse CSV"}, status=status.HTTP_400_BAD_REQUEST)

    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        return Response(
            {"error": f"missing required columns: {', '.join(missing)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    source_list = UniversityStudentList.objects.create(
        institute=institute,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_verification=str(request.data.get("contact_verification") or "").strip(),
        uploaded_by=getattr(request.user, "email", "") or str(request.user),
    )

    accepted, rejected, skipped_claimed, seen = 0, [], [], set()
    for i, raw in enumerate(reader, start=2):  # row 1 is the header
        row = {k: str(raw.get(k) or "").strip() for k in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
        email = row["email"].lower()
        if not email or "@" not in email:
            rejected.append({"row": i, "reason": "invalid email"})
            continue
        if any(not row[c] for c in REQUIRED_COLUMNS):
            rejected.append({"row": i, "reason": "missing required field"})
            continue
        if email in seen:
            rejected.append({"row": i, "reason": "duplicate email in file"})
            continue
        seen.add(email)

        existing = (
            ListedStudent.objects.filter(institute_id=institute.id, email__iexact=email)
            .order_by("-created_at")
            .first()
        )
        if existing and existing.status == ListedStudent.Status.CLAIMED:
            # Never overwrite a claimed profile from a list (spec S3); report
            # list-side changes for human review instead.
            skipped_claimed.append({"row": i, "email": _mask_email(email)})
            continue
        if existing and existing.status == ListedStudent.Status.UNCLAIMED:
            for field in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
                setattr(existing, field, row[field])
            existing.source_list = source_list
            existing.save()
        else:
            ListedStudent.objects.create(
                source_list=source_list, institute_id=institute.id, **row
            )
        accepted += 1

    source_list.row_count = accepted
    source_list.save(update_fields=["row_count"])
    return Response(
        {
            "list_id": source_list.id,
            "accepted": accepted,
            "rejected": rejected,
            "skipped_claimed": skipped_claimed,
        }
    )


# ---------------------------------------------------------------------------
# Roster visibility + invite dispatch (institute / superuser side)
# ---------------------------------------------------------------------------

def _require_institute_or_superuser(request):
    """Returns (account, None) or (None, error Response)."""
    account = getattr(request.user, "account", None)
    role = getattr(account, "role", "")
    if role not in (Account.Role.INSTITUTE, Account.Role.SUPERUSER):
        return None, Response({"error": "institute or superuser role required"}, status=status.HTTP_403_FORBIDDEN)
    return account, None


def _get_owned_list(account, list_id):
    """Returns (list, None) or (None, error Response). Scopes an institute
    account to lists belonging to its own institute_id; a superuser may
    access any list."""
    lst = UniversityStudentList.objects.select_related("institute").filter(id=list_id).first()
    if lst is None:
        return None, Response({"error": "list not found"}, status=status.HTTP_404_NOT_FOUND)
    if account.role == Account.Role.INSTITUTE and lst.institute_id != account.institute_id:
        return None, Response(
            {"error": "you may only access your own institute's lists"}, status=status.HTTP_403_FORBIDDEN
        )
    return lst, None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_lists(request):
    """
    GET /api/institute-lists/lists/   ?institute_id=<...> (superuser only)
    An institute account sees only its own uploaded lists; a superuser sees
    all, optionally filtered. Answers "which lists have I uploaded and how
    are they progressing" -- claimed/unclaimed counts per list.
    """
    account, error = _require_institute_or_superuser(request)
    if error:
        return error

    qs = UniversityStudentList.objects.select_related("institute")
    if account.role == Account.Role.INSTITUTE:
        qs = qs.filter(institute_id=account.institute_id)
    else:
        institute_id = request.query_params.get("institute_id")
        if institute_id:
            qs = qs.filter(institute_id=institute_id)

    status_counts_by_list = {
        row["source_list_id"]: row
        for row in (
            ListedStudent.objects.filter(source_list__in=qs)
            .values("source_list_id")
            .annotate(claimed=Count("id", filter=Q(status=ListedStudent.Status.CLAIMED)))
        )
    }

    lists = []
    for lst in qs:
        counts = status_counts_by_list.get(lst.id, {"claimed": 0})
        lists.append(
            {
                "list_id": lst.id,
                "institute_id": lst.institute_id,
                "institute_name": lst.institute.name,
                "contact_name": lst.contact_name,
                "contact_email": lst.contact_email,
                "status": lst.status,
                "row_count": lst.row_count,
                "claimed_count": counts.get("claimed", 0),
                "unclaimed_count": lst.row_count - counts.get("claimed", 0),
                "created_at": lst.created_at,
            }
        )
    return Response({"lists": lists})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_students(request, list_id):
    """
    GET /api/institute-lists/lists/<list_id>/students/
    Every student row on this list plus their claim status -- this is the
    "who is registered for this list" view. Full email is shown here (not
    masked) because the caller already owns this list; masking only matters
    for someone who has *just* a claim link/token, not the uploading institute.
    """
    account, error = _require_institute_or_superuser(request)
    if error:
        return error

    lst, error = _get_owned_list(account, list_id)
    if error:
        return error

    students = [
        {
            "id": row.id,
            "full_name": row.full_name,
            "email": row.email,
            "field_of_study": row.field_of_study,
            "degree_level": row.degree_level,
            "expected_graduation": row.expected_graduation,
            "status": row.status,
            "invited_at": row.invited_at,
            "claimed_at": row.claimed_at,
        }
        for row in lst.students.all()
    ]
    return Response({"list_id": lst.id, "students": students})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def send_invites(request, list_id):
    """
    POST /api/institute-lists/lists/<list_id>/send-invites/
    Emails every not-yet-invited UNCLAIMED row on this list a claim link
    (CLAIM_PAGE_URL?token=...). Safe to call again after a re-upload --
    already-invited rows are skipped, not re-spammed; pass "resend": true to
    override that and re-invite everyone unclaimed regardless.
    """
    account, error = _require_institute_or_superuser(request)
    if error:
        return error

    lst, error = _get_owned_list(account, list_id)
    if error:
        return error

    if not settings.CLAIM_PAGE_URL:
        return Response(
            {"error": "CLAIM_PAGE_URL is not configured on the server -- set it before sending invites."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    resend = bool(request.data.get("resend"))
    rows = lst.students.filter(status=ListedStudent.Status.UNCLAIMED)
    if not resend:
        rows = rows.filter(invited_at__isnull=True)

    now = timezone.now()
    sent = 0
    for row in rows:
        claim_link = f"{settings.CLAIM_PAGE_URL}?{urlencode({'token': row.claim_token})}"
        send_mail(
            subject="You're invited to claim your Kormic profile",
            message=(
                f"Hi {row.full_name},\n\n"
                f"{lst.institute.name} has listed you for a Kormic profile. "
                f"Claim it here: {claim_link}\n\n"
                "This link identifies you but reveals nothing on its own -- "
                "you'll still need to verify your email with a one-time code."
            ),
            from_email=None,  # DEFAULT_FROM_EMAIL
            recipient_list=[row.email],
            fail_silently=False,
        )
        row.invited_at = now
        row.save(update_fields=["invited_at"])
        sent += 1

    return Response({"list_id": lst.id, "invites_sent": sent})


# ---------------------------------------------------------------------------
# Claim flow (student side; unauthenticated by design -- the OTP is the auth)
# ---------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ClaimStartIPThrottle, ClaimStartEmailThrottle])
def start_claim(request):
    """
    POST /api/claim/start/   {"email": ...} or {"token": ...}
    Sends a one-time code to the LISTED address and returns only the masked
    email. Reveals nothing else -- a forwarded link or photographed QR gets
    an attacker no further than this masked string.

    Rate limited per-IP and per-email/token so it can't be hammered --
    each call sends a real email, so the email-side budget is deliberately
    tight (see ClaimStartEmailThrottle / DEFAULT_THROTTLE_RATES).
    """
    row = _find_claimable(
        email=str(request.data.get("email") or ""),
        token=str(request.data.get("token") or ""),
    )
    if not row:
        # Deliberately generic: don't confirm which emails are on a list.
        return Response(
            {"error": "No claimable invitation found for that information."},
            status=status.HTTP_404_NOT_FOUND,
        )

    code = f"{secrets.randbelow(10**6):06d}"
    row.otp_hash = _hash_otp(code)
    row.otp_expires_at = timezone.now() + timezone.timedelta(seconds=OTP_TTL_SECONDS)
    row.otp_attempts = 0
    row.save(update_fields=["otp_hash", "otp_expires_at", "otp_attempts"])

    send_mail(
        subject="Your Kormic claim code",
        message=(
            f"Your one-time code is {code}. It expires in 10 minutes.\n\n"
            "Your university listed this address so you can claim your Kormic "
            "profile. If you didn't request this, you can ignore it."
        ),
        from_email=None,  # DEFAULT_FROM_EMAIL
        recipient_list=[row.email],
        fail_silently=False,
    )
    return Response({"masked_email": _mask_email(row.email)})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ClaimVerifyIPThrottle, ClaimVerifyEmailThrottle])
def verify_claim(request):
    """
    POST /api/claim/verify/   {"email"|"token": ..., "code": ...}
    Correct code -> the pre-fill payload plus a short-lived signed claim
    session for the confirm step. Wrong code -> attempts counted, nothing
    revealed.

    """
    row = _find_claimable(
        email=str(request.data.get("email") or ""),
        token=str(request.data.get("token") or ""),
    )
    code = str(request.data.get("code") or "").strip()
    if not row or not code:
        return Response({"error": "invalid request"}, status=status.HTTP_400_BAD_REQUEST)

    if not row.otp_hash or not row.otp_expires_at or timezone.now() > row.otp_expires_at:
        return Response({"error": "code expired -- request a new one"}, status=status.HTTP_400_BAD_REQUEST)
    if row.otp_attempts >= OTP_MAX_ATTEMPTS:
        return Response({"error": "too many attempts -- request a new code"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    row.otp_attempts += 1
    row.save(update_fields=["otp_attempts"])
    if _hash_otp(code) != row.otp_hash:
        return Response({"error": "incorrect code"}, status=status.HTTP_400_BAD_REQUEST)

    claim_session = _claim_signer.sign(str(row.id))
    return Response({"claim_session": claim_session, "prefill": _prefill_payload(row)})


@api_view(["POST"])
@permission_classes([AllowAny])
def confirm_claim(request):
    """
    POST /api/claim/confirm/   {"claim_session": ..., "fields": {...}}
    Marks the row claimed (single-use: the token dies here), records every
    divergence between the list's values and the student's confirmed values,
    and creates/updates the StudentProfile tagged university-sourced with
    full provenance in extra_data.
    """
    session = str(request.data.get("claim_session") or "")
    fields = request.data.get("fields") or {}
    try:
        row_id = int(_claim_signer.unsign(session, max_age=CLAIM_SESSION_MAX_AGE))
    except (signing.BadSignature, signing.SignatureExpired, ValueError):
        return Response({"error": "invalid or expired claim session"}, status=status.HTTP_400_BAD_REQUEST)

    row = ListedStudent.objects.filter(id=row_id).first()
    if not row or row.status != ListedStudent.Status.UNCLAIMED:
        return Response({"error": "this invitation is no longer claimable"}, status=status.HTTP_400_BAD_REQUEST)

    now = timezone.now()
    divergences = []
    confirmed = {}
    for field in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
        list_value = getattr(row, field)
        student_value = str(fields.get(field, list_value) or "").strip()
        if field == "email":
            # The claim address is what was vouched for; it is never editable
            # here (spec S5). Fixes go through the institute contact.
            confirmed[field] = row.email
            continue
        confirmed[field] = student_value
        if student_value != list_value:
            divergences.append(
                {"field": field, "list_value": list_value, "student_value": student_value, "at": now.isoformat()}
            )

    from django_api.models import StudentProfile
    from django_api.services import make_student_id

    student_id = make_student_id(row.email)
    profile, _ = StudentProfile.objects.get_or_create(student_id=student_id)
    profile.name = confirmed["full_name"] or profile.name
    profile.email = row.email
    profile.major = confirmed["field_of_study"] or profile.major
    profile.program = confirmed.get("program_name") or profile.program
    profile.institution = row.source_list.institute.name or profile.institution
    extra = profile.extra_data or {}
    extra["institute_sourced"] = {
        "list_id": row.source_list_id,
        "institute_id": row.institute_id,
        "institute_name": row.source_list.institute.name,
        "contact_name": row.source_list.contact_name,
        "claimed_at": now.isoformat(),
        "divergence_count": len(divergences),
    }
    profile.extra_data = extra
    profile.save()

    row.status = ListedStudent.Status.CLAIMED
    row.claimed_at = now
    row.claimed_student_id = student_id
    row.divergences = divergences
    row.otp_hash = ""
    row.save()

    return Response(
        {
            "status": "claimed",
            "student_id": student_id,
            "confirmed": confirmed,
            "divergences_recorded": len(divergences),
            "badge": {
                "institute_sourced": True,
                "institute_name": row.source_list.institute.name,
            },
        }
    )
