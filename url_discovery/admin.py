from django.contrib import admin

from url_discovery.models import DiscoveredUrl, DiscoveryJob


@admin.register(DiscoveryJob)
class DiscoveryJobAdmin(admin.ModelAdmin):
    list_display = ("id", "university", "status", "pages_crawled", "pages_discovered", "created_at")
    list_filter = ("status",)
    search_fields = ("university__name", "base_url")


@admin.register(DiscoveredUrl)
class DiscoveredUrlAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "normalized_url", "primary_category", "decision_status", "relevance_score")
    list_filter = ("decision_status", "primary_category")
    search_fields = ("normalized_url", "page_title")
