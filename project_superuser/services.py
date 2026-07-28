from __future__ import annotations

"""
Helpers backing project_superuser.views (and, via log_activity, accounts.
views): purge_* clean up the rows in django_api that only reference
student_id/university_id as a loose string (Django can't cascade those off
a deleted auth.User the way it does Account/TOTPDevice/TOTPBackupCode/
GitHubOAuthConnection); revoke_all_sessions backs the force-logout action;
log_activity writes every ActivityLog entry -- both the admin-initiated
ones (TOTP reset/password reset/session revoke) and the self-service auth
ones (register/login/logout) that accounts.views logs on the same model.
"""


def purge_student_data(student_id: str) -> None:
    from django_api.models import AriaMemory, ChatMessage, IntakeSession, StudentProfile

    # Cascades ResumeUpload/GitHubAnalysis/LinkedInAnalysis/FitAssessment/
    # RoadmapVersion, which are real FKs to StudentProfile.
    StudentProfile.objects.filter(student_id=student_id).delete()
    AriaMemory.objects.filter(student_id=student_id).delete()
    IntakeSession.objects.filter(student_id=student_id).delete()
    ChatMessage.objects.filter(student_id=student_id).delete()


def purge_university_data(university_id: str) -> None:
    from django_api.models import (
        ChatMessage,
        PendingQuery,
        PresenterAuditLog,
        UniversityKnowledgeEntry,
        UniversityQuestionLog,
        VerifiedAnswer,
    )

    UniversityKnowledgeEntry.objects.filter(university_id=university_id).delete()
    PendingQuery.objects.filter(university_id=university_id).delete()
    VerifiedAnswer.objects.filter(university_id=university_id).delete()
    UniversityQuestionLog.objects.filter(university_id=university_id).delete()
    PresenterAuditLog.objects.filter(university_id=university_id).delete()
    ChatMessage.objects.filter(university_id=university_id).delete()


def revoke_all_sessions(user) -> int:
    """
    Blacklists every not-yet-blacklisted outstanding refresh token for
    `user`. Relies on rest_framework_simplejwt.token_blacklist (already
    installed) recording an OutstandingToken row each time a RefreshToken
    is issued for a user. Returns how many tokens were newly blacklisted.
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    tokens = OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False)
    count = 0
    for token in tokens:
        BlacklistedToken.objects.get_or_create(token=token)
        count += 1
    return count


def log_activity(
    action: str,
    *,
    actor=None,
    target_user=None,
    target_email: str = "",
    target_role: str = "",
    target_student_id: str = "",
    target_university_id: str = "",
) -> None:
    """
    Writes one ActivityLog row. `actor` is who performed the action (None
    for a failed login, since nothing about the caller is proven yet);
    `target_user` is whose account it happened to (None for a failed
    login against an email with no matching account -- pass the attempted
    email as `target_email` in that case). `target_role`/`target_student_id`/
    `target_university_id` are derived from `target_user.account` when
    omitted and a target_user is given -- letting a caller filter/group
    entries by student or university without a join back to Account.
    """
    from project_superuser.models import ActivityLog

    if target_user is not None:
        target_email = target_email or target_user.email
        if not (target_role or target_student_id or target_university_id):
            account = getattr(target_user, "account", None)
            if account:
                target_role = account.role
                target_student_id = account.student_id or ""
                target_university_id = account.university_id or ""

    ActivityLog.objects.create(
        actor=actor,
        actor_email=actor.email if actor else "",
        action=action,
        target_user=target_user,
        target_email=target_email,
        target_role=target_role,
        target_student_id=target_student_id,
        target_university_id=target_university_id,
    )
