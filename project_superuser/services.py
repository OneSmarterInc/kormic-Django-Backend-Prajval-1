from __future__ import annotations

"""
Cleanup helpers for the superuser "delete student" / "delete university"
actions. Deleting the auth.User row already cascades everything that's a
real FK off Account/TOTPDevice/TOTPBackupCode/GitHubOAuthConnection -- these
functions cover the rows in django_api that only reference student_id /
university_id as a loose string, which Django can't cascade for us.
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
