from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/verification/", include("verification.urls")),
    path("api/", include("django_api.urls")),
]
