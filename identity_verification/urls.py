from django.urls import path

from identity_verification import views

urlpatterns = [
    path("sessions/", views.IdentitySessionCreateView.as_view(), name="identity-session-create"),
    path("sessions/<uuid:session_id>/", views.IdentitySessionDetailView.as_view(), name="identity-session-detail"),
    path("sessions/<uuid:session_id>/complete/", views.IdentitySessionCompleteView.as_view(), name="identity-session-complete"),
    path("device-biometrics/", views.DeviceBiometricPreferenceView.as_view(), name="identity-device-biometrics"),
]
