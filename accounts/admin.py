from django.contrib import admin

from accounts.models import Account, TOTPBackupCode, TOTPDevice


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "student_id", "university_id", "created_at")
    search_fields = ("user__email", "student_id", "university_id")
    list_filter = ("role",)


@admin.register(TOTPDevice)
class TOTPDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "confirmed_at", "last_used_at", "created_at")
    search_fields = ("user__email",)


@admin.register(TOTPBackupCode)
class TOTPBackupCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "used_at", "created_at")
    search_fields = ("user__email",)
