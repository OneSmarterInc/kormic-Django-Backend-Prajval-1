# universities/notifications.py
# Here the caller is an officer
# who explicitly clicked "notify" and needs to know whether the email went
# out, so send_escalation_digest_email reports success/failure instead.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

from django.conf import settings
from django.core.mail import send_mail

if TYPE_CHECKING:
    from django_api.models import PendingQuery

logger = logging.getLogger(__name__)


def send_escalation_digest_email(
    *,
    to_email: str,
    to_name: str,
    group_label: str,
    university_name: str,
    escalations: Iterable["PendingQuery"],
    custom_message: str = "",
) -> bool:
    """Email a knowledge group's escalation contact a digest of the student
    questions routed to them. Returns True if the send succeeded."""
    escalations = list(escalations)
    subject = f"[{university_name}] {len(escalations)} escalation(s) routed to {group_label}"

    lines = [f"Hi {to_name or group_label},", ""]
    if custom_message:
        lines += [custom_message, ""]
    lines.append(
        f"The following student question(s) have been routed to {group_label} "
        f"for {university_name} and need a response:"
    )
    lines.append("")

    for query in escalations:
        lines.append(f"- [{(query.priority or '').upper()}] {query.student_name or 'A student'}: {query.question}")
        if query.urgency_reason:
            lines.append(f"  Reason flagged urgent: {query.urgency_reason}")
        if query.created_at:
            lines.append(f"  Submitted: {query.created_at:%Y-%m-%d %H:%M}")
        lines.append("")

    lines.append(f"-- {university_name} (via Korgut)")
    message = "\n".join(lines)

    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send escalation digest email to %s", to_email)
        return False
