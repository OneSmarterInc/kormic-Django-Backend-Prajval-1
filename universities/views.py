

from __future__ import annotations

from typing import Any, Dict

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsTOTPEnrolled, IsUniversityRole, get_account
from universities import services
from universities.identity import is_agent_name_available
from universities.models import University

UNIVERSITY_ADMIN_PERMISSIONS = [IsAuthenticated, IsTOTPEnrolled, IsUniversityRole]

# Profile fields that are also mirrored into the knowledge base -- editing
# any of these should re-sync the derived KB facts.
_KB_SYNCED_FIELDS = {
    "description",
    "contact_email",
    "contact_phone",
    "website_url",
    "admissions_office_address",
    "eligibility_criteria",
}

_PATCHABLE_PROFILE_FIELDS = _KB_SYNCED_FIELDS | {
    "name",
    "location",
    "tagline",
    "tone_descriptors",
    "best_fit_notes",
    "not_best_fit_notes",
    "communication_style_notes",
    "never_do_notes",
}


def _error(message: str, http_status=status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"status": "error", "message": str(message)}, status=http_status)


def _get_own_university(request) -> University | None:
    account = get_account(request)
    if account is None or not account.university_id:
        return None
    return University.objects.filter(pk=account.university_id).first()


def _serialize_profile(university: University) -> Dict[str, Any]:
    return {
        "id": university.id,
        "name": university.name,
        "agent_name": university.agent_name,
        "location": university.location,
        "tagline": university.tagline,
        "description": university.description,
        "contact_email": university.contact_email,
        "contact_phone": university.contact_phone,
        "website_url": university.website_url,
        "admissions_office_address": university.admissions_office_address,
        "eligibility_criteria": university.eligibility_criteria,
        "scrape_urls": university.scrape_urls,
        "tone_descriptors": university.tone_descriptors,
        "best_fit_notes": university.best_fit_notes,
        "not_best_fit_notes": university.not_best_fit_notes,
        "communication_style_notes": university.communication_style_notes,
        "never_do_notes": university.never_do_notes,
        "setup_status": services.university_setup_status(university.id),
        "created_at": university.created_at,
        "updated_at": university.updated_at,
    }


def _serialize_knowledge_entry(entry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "topic": entry.topic,
        "content": entry.content,
        "source_type": entry.source_type,
        "source_url": entry.source_url,
        "confidence": entry.confidence,
        "times_used": entry.times_used,
        "created_at": entry.created_at,
    }


class UniversityProfileAPIView(APIView):
    """
    GET /api/university-admin/profile/
    PATCH /api/university-admin/profile/
    The setup-phase profile for the authenticated officer's own university.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def get(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)
        return Response(_serialize_profile(university))

    def patch(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        changed_kb_fields = False

        for field in _PATCHABLE_PROFILE_FIELDS:
            if field not in data:
                continue
            setattr(university, field, data[field])
            if field in _KB_SYNCED_FIELDS:
                changed_kb_fields = True

        university.save()

        if changed_kb_fields:
            services.sync_profile_facts_to_kb(university)

        return Response(_serialize_profile(university))


class UniversityProfileCompletionAPIView(APIView):
    """
    GET /api/university-admin/profile/completion/
    Standalone completion checklist for the authenticated officer's own
    university -- same derived data embedded as "setup_status" in
    UniversityProfileAPIView, exposed separately so a setup-progress widget
    doesn't need to fetch (and can't accidentally leak) the full profile.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def get(self, request):
        account = get_account(request)
        if account is None or not account.university_id:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)
        return Response(services.university_setup_status(account.university_id))


class UniversityAgentNameAPIView(APIView):
    """
    GET /api/university-admin/agent-name/
    PATCH /api/university-admin/agent-name/  Body: {"agent_name": "..."}
    Mirrors django_api.views.AgentNameAPIView for students.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def get(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)
        return Response({"agent_name": university.agent_name})

    def patch(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        new_name = str(request.data.get("agent_name", "")).strip()

        if not new_name:
            return _error("agent_name is required.")
        if len(new_name) > 100:
            return _error("agent_name must be 100 characters or fewer.")
        if not is_agent_name_available(new_name, exclude_university_id=university.id):
            return _error("This agent name is already taken. Please choose another.", status.HTTP_409_CONFLICT)

        university.agent_name = new_name
        university.save(update_fields=["agent_name", "updated_at"])
        return Response({"agent_name": university.agent_name})


class ScrapeUrlsAPIView(APIView):
    """
    GET /api/university-admin/scrape-urls/
    PUT /api/university-admin/scrape-urls/  Body: {"scrape_urls": ["https://...", ...]}
    Replaces the whole saved URL list -- knowledge.scraper.scrape_university()
    reads from this list when scrape-now is triggered.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def get(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)
        return Response({"scrape_urls": university.scrape_urls})

    def put(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        urls = request.data.get("scrape_urls")
        if not isinstance(urls, list) or not all(isinstance(url, str) and url.strip() for url in urls):
            return _error("scrape_urls must be a list of non-empty URL strings.")

        university.scrape_urls = [url.strip() for url in urls]
        university.save(update_fields=["scrape_urls", "updated_at"])
        return Response({"scrape_urls": university.scrape_urls})


class ScrapeNowAPIView(APIView):
    """
    POST /api/university-admin/scrape-urls/scrape-now/
    Synchronously scrapes every saved URL and stores extracted facts as
    source_type="scraped" -- the explicit, visible replacement for the old
    silent KORGUT_AUTO_SCRAPE-gated construction-time scrape.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def post(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        if not university.scrape_urls:
            return _error("No scrape URLs are saved yet. Save some with PUT /api/university-admin/scrape-urls/ first.")

        result = services.scrape_now(university)
        return Response(result)


class KnowledgeFactListCreateAPIView(APIView):
    """
    GET /api/university-admin/knowledge/  -- every fact (any source_type) for this university.
        Optional query params narrow the list:
          ?section=<source_type>   e.g. seed, manual, scraped, conversation, human_verified
          ?source_url=<url>        exact match against the scraped page a fact came from
    POST /api/university-admin/knowledge/  Body: {"topic": "...", "content": "...", "confidence": 1.0}
    POST always stores as source_type="manual" regardless of request body --
    the direct write path that used to only exist reactively via resolving
    a PendingQuery.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def get(self, request):
        from django_api.models import UniversityKnowledgeEntry

        account = get_account(request)
        if account is None or not account.university_id:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        entries = UniversityKnowledgeEntry.objects.filter(university_id=account.university_id)

        section = request.query_params.get("section")
        if section:
            entries = entries.filter(source_type=section.strip().lower())

        source_url = request.query_params.get("source_url")
        if source_url:
            entries = entries.filter(source_url=source_url.strip())

        return Response({"knowledge": [_serialize_knowledge_entry(entry) for entry in entries]})

    def post(self, request):
        university = _get_own_university(request)
        if university is None:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        topic = str(request.data.get("topic", "")).strip()
        content = str(request.data.get("content", "")).strip()

        if not topic or not content:
            return _error("topic and content are required.")

        try:
            confidence = float(request.data.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0

        from django_api.models import UniversityKnowledgeEntry

        entry = services.add_manual_knowledge_fact(
            university.id, topic, content, confidence=confidence
        )
        # add_manual_knowledge_fact returns the KB wrapper's in-memory
        # KnowledgeEntry (has .db_id, not .id) -- re-fetch the real row so
        # _serialize_knowledge_entry (which expects model attributes) works
        # the same for both GET (lists model rows directly) and POST.
        row = UniversityKnowledgeEntry.objects.get(id=entry.db_id)
        return Response(_serialize_knowledge_entry(row), status=status.HTTP_201_CREATED)


class KnowledgeSectionsAPIView(APIView):
    """
    GET /api/university-admin/knowledge/sections/
    Knowledge facts grouped by section (source_type), with a count per
    section -- backs a sidebar/tab view over the flat knowledge list.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def get(self, request):
        from django.db.models import Count
        from django_api.models import UniversityKnowledgeEntry

        account = get_account(request)
        if account is None or not account.university_id:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        rows = (
            UniversityKnowledgeEntry.objects.filter(university_id=account.university_id)
            .values("source_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        return Response({
            "sections": [
                {"section": row["source_type"], "count": row["count"]} for row in rows
            ],
        })


class KnowledgeSourceUrlsAPIView(APIView):
    """
    GET /api/university-admin/knowledge/urls/
    Knowledge facts grouped by the source URL they were scraped from, with a
    count per URL -- backs a "facts by page" view. Facts with no source_url
    (seed/manual/conversation) are omitted since there is no URL to group by.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def get(self, request):
        from django.db.models import Count
        from django_api.models import UniversityKnowledgeEntry

        account = get_account(request)
        if account is None or not account.university_id:
            return _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        rows = (
            UniversityKnowledgeEntry.objects.filter(university_id=account.university_id)
            .exclude(source_url__isnull=True)
            .exclude(source_url="")
            .values("source_url")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        return Response({
            "urls": [
                {"source_url": row["source_url"], "count": row["count"]} for row in rows
            ],
        })


class KnowledgeFactDetailAPIView(APIView):
    """
    PATCH /api/university-admin/knowledge/<int:fact_id>/
        Body: any of {"topic": "...", "content": "...", "confidence": 1.0}
    DELETE /api/university-admin/knowledge/<int:fact_id>/
    Both are restricted to source_type in ("manual", "seed") -- editing or
    deleting scraped/conversation/human_verified rows is blocked to avoid
    corrupting provenance through a generic edit action; re-scrape or
    resolving a pending query are the correct paths for those.
    """

    permission_classes = UNIVERSITY_ADMIN_PERMISSIONS

    def _get_editable_entry(self, request, fact_id: int):
        """Returns (entry, error_response). entry is None iff error_response is set."""
        from django_api.models import UniversityKnowledgeEntry

        account = get_account(request)
        if account is None or not account.university_id:
            return None, _error("No university profile found for this account.", status.HTTP_404_NOT_FOUND)

        entry = UniversityKnowledgeEntry.objects.filter(
            id=fact_id, university_id=account.university_id
        ).first()

        if entry is None:
            return None, _error("Knowledge fact not found.", status.HTTP_404_NOT_FOUND)

        if entry.source_type not in ("manual", "seed"):
            return None, _error(
                f"Cannot modify a '{entry.source_type}' fact through this endpoint. "
                "Re-scrape to refresh scraped facts, or resolve the related pending "
                "query for human-verified/conversation facts."
            )

        return entry, None

    def patch(self, request, fact_id: int):
        entry, error = self._get_editable_entry(request, fact_id)
        if error is not None:
            return error

        data = request.data or {}
        update_fields = []

        if "topic" in data:
            topic = str(data["topic"]).strip()
            if not topic:
                return _error("topic cannot be empty.")
            entry.topic = topic
            update_fields.append("topic")

        if "content" in data:
            content = str(data["content"]).strip()
            if not content:
                return _error("content cannot be empty.")
            entry.content = content
            update_fields.append("content")

        if "confidence" in data:
            try:
                confidence = float(data["confidence"])
            except (TypeError, ValueError):
                return _error("confidence must be a number.")
            entry.confidence = max(0.0, min(1.0, confidence))
            update_fields.append("confidence")

        if not update_fields:
            return _error("Provide at least one of topic, content, confidence to update.")

        entry.save(update_fields=update_fields)
        return Response(_serialize_knowledge_entry(entry))

    def delete(self, request, fact_id: int):
        entry, error = self._get_editable_entry(request, fact_id)
        if error is not None:
            return error

        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
