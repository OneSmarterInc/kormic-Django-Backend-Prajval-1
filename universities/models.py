
from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def default_priority_tier_bounds() -> dict:
    return {"high": 80, "medium": 60, "low": 40}


class University(models.Model):
    id = models.SlugField(primary_key=True, max_length=255)

    # -- identity (mirrors the old UNIVERSITY_PERSONAS entry shape) --
    name = models.CharField(max_length=500)
    agent_name = models.CharField(max_length=100, unique=True, null=True, blank=True, db_index=True)
    location = models.CharField(max_length=255, blank=True, default="")
    tagline = models.CharField(max_length=500, blank=True, default="")

    # -- setup-phase structured fields, admin-editable any time --
    description = models.TextField(blank=True, default="")

    contact_email = models.CharField(max_length=255, blank=True, default="")
    contact_phone = models.CharField(max_length=50, blank=True, default="")
    website_url = models.CharField(max_length=1000, blank=True, default="")
    admissions_office_address = models.TextField(blank=True, default="")

    # [{"criterion": "...", "detail": "..."}]
    eligibility_criteria = models.JSONField(default=list, blank=True)
    # ["https://...", ...] -- same flat shape knowledge.scraper.scrape_university() expects
    scrape_urls = models.JSONField(default=list, blank=True)

    # Officer-tunable pass/fail cutoff (0-100) against FitAssessment.match_score
    # for the officer-facing shortlist (see django_api.services.get_shortlisted_profiles).
    # 40 mirrors the "target" tier cutoff used by the deterministic fallback
    # assessment (agents.commons._fallback_fit_assessment) as a sane default,
    # but is per-university and officer-editable, not a hardcoded gate.
    min_fit_score_threshold = models.IntegerField(
        default=40,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    # Officer-tunable score bands (0-100) for grouping shortlisted students by
    # priority -- see django_api.services.compute_priority_tier. Keys are
    # fixed ("high"/"medium"/"low"); values are the minimum match_score for
    # each band, editable per university rather than hardcoded.
    priority_tier_bounds = models.JSONField(default=default_priority_tier_bounds, blank=True)

    # -- persona-input fields feeding personas.university_persona_builder --
    tone_descriptors = models.JSONField(default=list, blank=True)
    best_fit_notes = models.TextField(blank=True, default="")
    not_best_fit_notes = models.TextField(blank=True, default="")
    communication_style_notes = models.TextField(blank=True, default="")
    never_do_notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"University({self.id}, {self.name})"
