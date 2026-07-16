from django.urls import path

from verification import views

urlpatterns = [
    path("status/", views.VerificationStatusAPIView.as_view(), name="verification-status"),
    path("reanalyze/", views.VerificationReanalyzeAPIView.as_view(), name="verification-reanalyze"),
    path("items/", views.VerificationItemListAPIView.as_view(), name="verification-items"),
    path("items/<int:item_id>/decision/", views.VerificationItemDecisionAPIView.as_view(), name="verification-item-decision"),
]
