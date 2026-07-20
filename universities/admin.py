from django.contrib import admin

from universities.models import University


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "agent_name", "location", "created_at", "updated_at")
    search_fields = ("id", "name", "agent_name", "location")
