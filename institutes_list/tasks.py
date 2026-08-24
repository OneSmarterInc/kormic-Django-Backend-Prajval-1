# institutes_list/tasks.py
# Background delivery for claim-invite emails. send_invites (views.py) used
# to call send_mail() once per row in a loop inside the HTTP request -- a
# 200-row batch was 200 sequential SMTP round-trips in one request, easily
# exceeding any reasonable proxy/load-balancer timeout. Same fire-and-forget
# pattern as notifications/services.py: the view does the fast DB
# bookkeeping (marking invited_at) synchronously and hands the actual send
# off to Celery, one task per recipient.
from __future__ import annotations

import logging

from celery import shared_task
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_invite_email_task(self, listed_student_id: int) -> None:
    from django.conf import settings
    from urllib.parse import urlencode

    from institutes_list.models import ListedStudent

    row = (
        ListedStudent.objects.select_related("source_list__institute")
        .filter(id=listed_student_id)
        .first()
    )
    if row is None:
        logger.warning("send_invite_email_task: ListedStudent %s no longer exists.", listed_student_id)
        return

    claim_link = f"{settings.CLAIM_PAGE_URL}?{urlencode({'token': row.claim_token})}"

    try:
        send_mail(
            subject="You're invited to claim your Kormic profile",
            message=(
                f"Hi {row.full_name},\n\n"
                f"{row.source_list.institute.name} has listed you for a Kormic profile. "
                f"Claim it here: {claim_link}\n\n"
                f"Already have the app open? Enter this token directly instead: {row.claim_token}\n\n"
                "This link/token identifies you but reveals nothing on its own -- "
                "you'll still need to verify your email with a one-time code."
            ),
            from_email=None,  # DEFAULT_FROM_EMAIL
            recipient_list=[row.email],
            fail_silently=False,
        )
    except Exception as exc:
        raise self.retry(exc=exc)
