from rest_framework.permissions import BasePermission

from accounts.models import Account


class IsIdentityStudent(BasePermission):
    message = "Student identity verification access is required."

    def has_permission(self, request, view):
        account = getattr(request.user, "account", None)
        return bool(request.user and request.user.is_authenticated and account and account.role == Account.Role.STUDENT)
