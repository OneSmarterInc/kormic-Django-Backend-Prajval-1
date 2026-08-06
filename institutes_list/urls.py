from django.urls import path

from . import views

urlpatterns = [
    path("institute-lists/upload/", views.upload_list, name="institute-list-upload"),
    path("institute-lists/lists/", views.list_lists, name="institute-list-lists"),
    path("institute-lists/lists/<int:list_id>/students/", views.list_students, name="institute-list-students"),
    path("institute-lists/lists/<int:list_id>/send-invites/", views.send_invites, name="institute-list-send-invites"),
    path("claim/start/", views.start_claim, name="claim-start"),
    path("claim/verify/", views.verify_claim, name="claim-verify"),
    path("claim/confirm/", views.confirm_claim, name="claim-confirm"),
]
