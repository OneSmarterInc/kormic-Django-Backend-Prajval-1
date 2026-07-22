from __future__ import annotations

import logging
from typing import Dict, List

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_push_notification_task(self, log_id: int) -> None:
    """
    Deliver one NotificationLog to every active PushToken on its account via
    Expo. A token Expo reports as permanently dead (DeviceNotRegistered) is
    deactivated so future notifications stop trying it; a transient failure
    (network error, Expo 5xx) retries the whole task.
    """
    from notifications.expo import ExpoPushError, send_expo_push_messages
    from notifications.models import NotificationLog, PushToken

    try:
        log = NotificationLog.objects.get(id=log_id)
    except NotificationLog.DoesNotExist:
        logger.warning("send_push_notification_task: NotificationLog %s no longer exists", log_id)
        return

    tokens = list(PushToken.objects.filter(account_id=log.account_id, is_active=True))
    if not tokens:
        log.status = NotificationLog.Status.SKIPPED_NO_TOKEN
        log.save(update_fields=["status", "updated_at"])
        return

    messages = [
        {
            "to": token.token,
            "title": log.title,
            "body": log.body,
            "data": log.data,
            "sound": "default",
        }
        for token in tokens
    ]

    try:
        tickets = send_expo_push_messages(messages)
    except (ExpoPushError, Exception) as exc:  # network errors, Expo 5xx, etc.
        log.status = NotificationLog.Status.FAILED
        log.error = str(exc)
        log.save(update_fields=["status", "error", "updated_at"])
        raise self.retry(exc=exc)

    receipt_map: Dict[str, str] = {}
    for token, ticket in zip(tokens, tickets):
        if ticket.get("status") == "error":
            details = ticket.get("details") or {}
            if details.get("error") == "DeviceNotRegistered":
                token.is_active = False
                token.last_error = ticket.get("message", "")
                token.save(update_fields=["is_active", "last_error", "updated_at"])
        elif ticket.get("id"):
            receipt_map[ticket["id"]] = token.token

    log.status = NotificationLog.Status.SENT
    log.save(update_fields=["status", "updated_at"])

    if receipt_map:
        # Expo's ticket response only confirms it accepted the message, not
        # that it was actually delivered -- receipts (checked after a short
        # delay, per Expo's own guidance) catch delivery-time failures like a
        # token that looked valid but the device was since unregistered.
        check_push_receipts_task.apply_async(args=[receipt_map], countdown=20)


@shared_task(bind=True, max_retries=2, default_retry_delay=15)
def check_push_receipts_task(self, receipt_map: Dict[str, str]) -> None:
    """receipt_map: {expo_receipt_id: push_token_string}."""
    from notifications.expo import ExpoPushError, get_expo_push_receipts
    from notifications.models import PushToken

    try:
        receipts = get_expo_push_receipts(list(receipt_map.keys()))
    except (ExpoPushError, Exception) as exc:
        raise self.retry(exc=exc)

    for receipt_id, receipt in receipts.items():
        if receipt.get("status") != "error":
            continue
        token_str = receipt_map.get(receipt_id)
        if not token_str:
            continue
        details = receipt.get("details") or {}
        if details.get("error") == "DeviceNotRegistered":
            PushToken.objects.filter(token=token_str).update(
                is_active=False, last_error=receipt.get("message", "")
            )
