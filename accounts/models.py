from django.contrib.auth.models import User
from django.db import models


class Account(models.Model):
    """
    Role + ownership record linking a stock auth.User to this project's
    domain identifiers. Kept separate from AUTH_USER_MODEL (rather than a
    custom user model) because db.sqlite3 already has real dev data and
    swapping AUTH_USER_MODEL post-hoc is high-risk.
    """

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        UNIVERSITY = "university", "University"
        # High-level operator role for project_superuser -- not selectable via
        # the public /api/auth/register/ endpoint (see RegisterSerializer),
        # only created via the create_superuser_account management command or
        # an existing superuser's /api/superuser/users/create-superuser/.
        SUPERUSER = "superuser", "Superuser"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="account")
    role = models.CharField(max_length=20, choices=Role.choices)

    # Only one of these is populated, depending on role.
    student_id = models.CharField(max_length=255, null=True, blank=True, unique=True, db_index=True)
    university_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Account({self.user.email}, {self.role})"


class TOTPDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    # Plaintext base32 secret. Follow-up hardening item before any production
    # deploy: encrypt at rest (e.g. a Fernet-encrypted field).
    secret = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"TOTPDevice(user={self.user_id}, confirmed={bool(self.confirmed_at)})"


class TOTPBackupCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="totp_backup_codes")
    code_hash = models.CharField(max_length=64, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"TOTPBackupCode(user={self.user_id}, used={bool(self.used_at)})"


class GitHubOAuthConnection(models.Model):
    """
    A student's own GitHub OAuth grant, so GitHub API calls made on their
    behalf count against their personal rate limit instead of one shared
    server-wide GITHUB_TOKEN. Tokens are encrypted at rest (accounts.crypto)
    since, unlike the TOTP secret, a leaked GitHub token is a live,
    externally-usable credential.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="github_oauth_connection")

    github_user_id = models.BigIntegerField()
    github_username = models.CharField(max_length=255)

    github_name = models.CharField(max_length=255, blank=True, default="")
    github_email = models.CharField(max_length=255, blank=True, default="")

    access_token_encrypted = models.TextField()
    refresh_token_encrypted = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scope = models.CharField(max_length=255, blank=True, default="")

    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"GitHubOAuthConnection(user={self.user_id}, github={self.github_username})"
