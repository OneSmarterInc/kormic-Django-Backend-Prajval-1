from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("django_api.urls")),
    path("api/identity/", include("identity_verification.urls")),
]

