from django.urls import path

from universities import views

urlpatterns = [
    path("profile/", views.UniversityProfileAPIView.as_view(), name="university-admin-profile"),
    path(
        "profile/completion/",
        views.UniversityProfileCompletionAPIView.as_view(),
        name="university-admin-profile-completion",
    ),
    path("agent-name/", views.UniversityAgentNameAPIView.as_view(), name="university-admin-agent-name"),
    path("scrape-urls/", views.ScrapeUrlsAPIView.as_view(), name="university-admin-scrape-urls"),
    path("scrape-urls/scrape-now/", views.ScrapeNowAPIView.as_view(), name="university-admin-scrape-now"),
    path("knowledge/", views.KnowledgeFactListCreateAPIView.as_view(), name="university-admin-knowledge"),
    path(
        "knowledge/sections/",
        views.KnowledgeSectionsAPIView.as_view(),
        name="university-admin-knowledge-sections",
    ),
    path(
        "knowledge/urls/",
        views.KnowledgeSourceUrlsAPIView.as_view(),
        name="university-admin-knowledge-urls",
    ),
    path(
        "knowledge/<int:fact_id>/",
        views.KnowledgeFactDetailAPIView.as_view(),
        name="university-admin-knowledge-detail",
    ),
]
