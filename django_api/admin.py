from django.contrib import admin

from django_api.models import (
    ChatMessage,
    FitAssessment,
    GitHubAnalysis,
    IntakeSession,
    LinkedInAnalysis,
    ResumeUpload,
    RoadmapVersion,
    StudentProfile,
)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("student_id", "name", "email", "institution", "major", "updated_at")
    search_fields = ("student_id", "name", "email", "institution")


@admin.register(IntakeSession)
class IntakeSessionAdmin(admin.ModelAdmin):
    list_display = ("student_key", "student_id", "step", "completed", "updated_at")
    search_fields = ("student_key", "student_id")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("student_id", "channel", "university_id", "sender", "created_at")
    search_fields = ("student_id", "university_id", "content")
    list_filter = ("channel", "sender")


@admin.register(ResumeUpload)
class ResumeUploadAdmin(admin.ModelAdmin):
    list_display = ("student", "original_filename", "created_at")
    search_fields = ("student__student_id", "original_filename")


@admin.register(GitHubAnalysis)
class GitHubAnalysisAdmin(admin.ModelAdmin):
    list_display = ("student", "github_url", "created_at")
    search_fields = ("student__student_id", "github_url")


@admin.register(LinkedInAnalysis)
class LinkedInAnalysisAdmin(admin.ModelAdmin):
    list_display = ("student", "created_at")
    search_fields = ("student__student_id",)


@admin.register(FitAssessment)
class FitAssessmentAdmin(admin.ModelAdmin):
    list_display = ("student", "university_id", "created_at")
    search_fields = ("student__student_id", "university_id")
    list_filter = ("university_id",)


@admin.register(RoadmapVersion)
class RoadmapVersionAdmin(admin.ModelAdmin):
    list_display = ("student", "created_at")
    search_fields = ("student__student_id",)
