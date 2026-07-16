from django.urls import path

from django_api import views

urlpatterns = [
    path("", views.api_home, name="api-home"),

    # APIs 1-5: Profile Management
    path("profile/", views.ProfileCreateUpdateAPIView.as_view(), name="profile-create-update"),
    path("profile/image/", views.ProfileImageUploadAPIView.as_view(), name="profile-image-upload"),
    path("profile/resume/", views.ResumeUploadAPIView.as_view(), name="profile-resume"),
    path("profile/resume/<int:resume_id>/", views.ResumeDetailAPIView.as_view(), name="profile-resume-detail"),
    path("profile/github/", views.GitHubAnalyzeAPIView.as_view(), name="profile-github"),
    path("profile/linkedin/", views.LinkedInAnalyzeAPIView.as_view(), name="profile-linkedin"),
    path(
        "profile/linkedin/<int:analysis_id>/images/<int:index>/",
        views.LinkedInImageDetailAPIView.as_view(),
        name="profile-linkedin-image-detail",
    ),
    path("profile/<str:student_id>/", views.ProfileDetailAPIView.as_view(), name="profile-detail"),
    path("profile/<str:student_id>/image/", views.ProfileImageDetailAPIView.as_view(), name="profile-image-detail"),

    # Persistent GET APIs for profile sub-resource history
    path("profile/<str:student_id>/resumes/", views.ResumeHistoryView.as_view(), name="profile-resume-history"),
    path("profile/<str:student_id>/github-history/", views.GitHubHistoryView.as_view(), name="profile-github-history"),
    path("profile/<str:student_id>/linkedin-history/", views.LinkedInHistoryView.as_view(), name="profile-linkedin-history"),

    # APIs 6-8: Chat
    path("chat/intake/", views.profile_intake_chat, name="profile-intake-chat"),
    path("chat/aria/", views.aria_chat, name="aria-chat"),
    path("chat/aria/history/", views.aria_chat_history, name="aria-chat-history"),
    path("chat/university/<str:university_id>/", views.university_chat, name="university-chat"),
    path("chat/university/<str:university_id>/history/", views.university_chat_history, name="university-chat-history"),

    # API 9: Fit Assessment
    path("assessments/generate/<str:university_id>/", views.generate_fit_assessment, name="generate-fit-assessment"),
    path("assessments/<str:student_id>/", views.AssessmentHistoryView.as_view(), name="assessment-history"),
    path(
        "assessments/<str:university_id>/<str:student_id>/",
        views.AssessmentDetailView.as_view(),
        name="assessment-detail",
    ),

    # APIs 10-13: Roadmap, Queries, Export
    path("roadmap/<str:student_id>/", views.RoadmapView.as_view(), name="roadmap"),
    path("roadmap/<str:student_id>/history/", views.RoadmapHistoryView.as_view(), name="roadmap-history"),
    path("queries/pending/", views.PendingQueriesView.as_view(), name="pending-queries"),
    path("queries/answer/", views.AnswerPendingQueryView.as_view(), name="answer-pending-query"),
    path("queries/<int:query_id>/edit/", views.EditPendingQueryView.as_view(), name="edit-pending-query"),
    path("exports/pdf/<str:student_id>/", views.ExportProfilePDFView.as_view(), name="export-profile-pdf"),

    # University Dashboard APIs
    path("university/<str:university_id>/profiles/", views.UniversityProfilesListView.as_view(), name="university-profiles"),
    path(
        "university/<str:university_id>/profile/<str:student_id>/chat/",
        views.university_profile_presenter_chat,
        name="university-profile-chat",
    ),
    path(
        "university/<str:university_id>/profile/<str:student_id>/chat/history/",
        views.university_profile_presenter_chat_history,
        name="university-profile-chat-history",
    ),
    path("university/<str:university_id>/questions/", views.UniversityQuestionsView.as_view(), name="university-questions"),
    path("university/<str:university_id>/queries/", views.UniversityQueriesView.as_view(), name="university-queries"),
    path(
        "university/<str:university_id>/queries/active/",
        views.UniversityActiveQueriesView.as_view(),
        name="university-queries-active",
    ),
    path(
        "university/<str:university_id>/queries/archive/",
        views.UniversityArchiveQueriesView.as_view(),
        name="university-queries-archive",
    ),
    path(
        "university/<str:university_id>/knowledge/verified/",
        views.VerifiedKnowledgeView.as_view(),
        name="university-verified-knowledge",
    ),
]
