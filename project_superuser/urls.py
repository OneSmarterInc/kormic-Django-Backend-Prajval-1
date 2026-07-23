from django.urls import path

from project_superuser import views

urlpatterns = [
    path("students/", views.AdminStudentListCreateAPIView.as_view(), name="superuser-students"),
    path("students/<str:student_id>/", views.AdminStudentDetailAPIView.as_view(), name="superuser-student-detail"),
    path("universities/", views.AdminUniversityListCreateAPIView.as_view(), name="superuser-universities"),
    path(
        "universities/<str:university_id>/",
        views.AdminUniversityDetailAPIView.as_view(),
        name="superuser-university-detail",
    ),
    path("users/", views.AdminUserListAPIView.as_view(), name="superuser-users"),
    path(
        "users/create-superuser/",
        views.AdminCreateSuperuserAPIView.as_view(),
        name="superuser-create-superuser",
    ),
    path("users/<int:user_id>/", views.AdminUserDetailAPIView.as_view(), name="superuser-user-detail"),
]
