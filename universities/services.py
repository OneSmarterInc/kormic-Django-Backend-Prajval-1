
from __future__ import annotations

from typing import Any, Dict, List, Optional

from personas.university_persona_builder import build_constitution
from universities.identity import ensure_agent_name, make_university_id
from universities.models import University


PROGRAM_OVERVIEW_TOPIC = "Program Overview"
CONTACT_INFO_TOPIC = "Contact Information"


def build_persona_dict(university: University) -> Dict[str, Any]:
    """Adapt a University row into the exact shape
    personas.university_personas.UNIVERSITY_PERSONAS[id] used to have, so
    agents.university_agent.UniversityAgent needs no further changes."""
    constitution = build_constitution(
        agent_name=university.agent_name or university.id,
        program_name=university.name,
        location=university.location,
        tagline=university.tagline,
        description=university.description,
        tone_descriptors=university.tone_descriptors,
        best_fit_notes=university.best_fit_notes,
        not_best_fit_notes=university.not_best_fit_notes,
        communication_style_notes=university.communication_style_notes,
        never_do_notes=university.never_do_notes,
    )

    return {
        "name": university.name,
        "agent_name": university.agent_name or university.id,
        "location": university.location,
        "tagline": university.tagline,
        "constitution": constitution,
        "scrape_urls": list(university.scrape_urls or []),
        # Seed/manual/scraped facts live only in UniversityKnowledgeEntry
        # (already DB-backed) -- never re-derived from the persona dict.
        "key_facts_seed": [],
    }


def register_university(institution_name: str) -> University:
    """Create a bare University row with an auto-generated unique id and
    agent name -- the registration-time half of the two-phase flow. Setup
    (description/contacts/eligibility/scrape URLs/knowledge) all happens
    afterward via the universities-admin endpoints."""
    university_id = make_university_id(institution_name)
    university = University.objects.create(id=university_id, name=institution_name.strip())
    ensure_agent_name(university)
    return university


def _kb_for(university_id: str):
    from knowledge.university_kb import UniversityKnowledgeBase

    return UniversityKnowledgeBase(university_id)


def sync_profile_facts_to_kb(university: University) -> None:
    """Project description/contacts/eligibility_criteria into
    UniversityKnowledgeEntry rows (source_type="seed") so the agent can
    actually answer from them, not just recite them in its constitution.
    Upserts by fixed topic name since KB.store() only dedups by exact
    (topic, content)."""
    from django_api.models import UniversityKnowledgeEntry

    kb = _kb_for(university.id)
    topics_to_replace: List[str] = [PROGRAM_OVERVIEW_TOPIC, CONTACT_INFO_TOPIC]

    eligibility_topics = [
        f"Eligibility: {str(item.get('criterion', '')).strip()}"
        for item in (university.eligibility_criteria or [])
        if isinstance(item, dict) and str(item.get("criterion", "")).strip()
    ]
    topics_to_replace.extend(eligibility_topics)

    UniversityKnowledgeEntry.objects.filter(university_id=university.id, topic__in=topics_to_replace).delete()
    # The in-memory KB instance was loaded before the delete above; drop its
    # cached copies of these topics too so store()'s duplicate-detection
    # doesn't resurrect a stale entry instead of writing the new content.
    kb.entries = [entry for entry in kb.entries if entry.topic not in topics_to_replace]

    if university.description:
        kb.store(
            topic=PROGRAM_OVERVIEW_TOPIC,
            content=university.description,
            source_type="seed",
            confidence=1.0,
        )

    contact_parts = []
    if university.contact_email:
        contact_parts.append(f"Email: {university.contact_email}")
    if university.contact_phone:
        contact_parts.append(f"Phone: {university.contact_phone}")
    if university.website_url:
        contact_parts.append(f"Website: {university.website_url}")
    if university.admissions_office_address:
        contact_parts.append(f"Admissions office: {university.admissions_office_address}")

    if contact_parts:
        kb.store(
            topic=CONTACT_INFO_TOPIC,
            content=" | ".join(contact_parts),
            source_type="seed",
            confidence=1.0,
        )

    for item in university.eligibility_criteria or []:
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion", "")).strip()
        detail = str(item.get("detail", "")).strip()
        if not criterion:
            continue
        kb.store(
            topic=f"Eligibility: {criterion}",
            content=detail or criterion,
            source_type="seed",
            confidence=1.0,
        )


def scrape_now(university: University) -> Dict[str, Any]:
    """Synchronously scrape every saved URL, one at a time, so a failure on
    one page doesn't lose results from the others. knowledge.scraper's
    scrape_university() signature is untouched -- this just calls it once
    per URL and aggregates. It already sleeps ~1.5s per URL internally, so
    looping single-URL calls costs nothing extra in wall time versus one
    batched call, and buys per-URL visibility."""
    from knowledge.scraper import scrape_university

    urls = list(university.scrape_urls or [])
    kb = _kb_for(university.id)

    results: List[Dict[str, Any]] = []
    for url in urls:
        try:
            count = scrape_university(university.id, [url], university.name, kb)
            results.append({"url": url, "status": "ok", "facts_stored": count})
        except Exception as exc:
            results.append({"url": url, "status": "failed", "facts_stored": 0, "error": str(exc)})

    return {
        "total_facts_stored": sum(r["facts_stored"] for r in results),
        "results": results,
    }


def add_manual_knowledge_fact(
    university_id: str,
    topic: str,
    content: str,
    confidence: float = 1.0,
    source_url: Optional[str] = None,
) -> "Any":
    """Admin-entered fact, always stored as source_type="manual" regardless
    of caller input -- the direct write path that used to only exist
    reactively via resolving a PendingQuery."""
    kb = _kb_for(university_id)
    return kb.store(
        topic=topic,
        content=content,
        source_type="manual",
        source_url=source_url,
        confidence=confidence,
    )


def university_setup_status(university_id: str) -> Dict[str, Any]:
    """Derived fresh from real data every call, never a stored flag --
    mirrors accounts.serializers.student_onboarding_status."""
    from django_api.models import UniversityKnowledgeEntry

    university = University.objects.filter(pk=university_id).first()

    if university is None:
        return {
            "profile_exists": False,
            "has_description": False,
            "has_contacts": False,
            "has_eligibility_criteria": False,
            "has_scrape_urls": False,
            "has_knowledge_facts": False,
            "setup_complete": False,
        }

    has_contacts = bool(
        university.contact_email
        or university.contact_phone
        or university.website_url
        or university.admissions_office_address
    )
    has_eligibility_criteria = bool(university.eligibility_criteria)
    has_scrape_urls = bool(university.scrape_urls)
    has_knowledge_facts = UniversityKnowledgeEntry.objects.filter(university_id=university_id).exists()
    has_description = bool(university.description)

    return {
        "profile_exists": True,
        "has_description": has_description,
        "has_contacts": has_contacts,
        "has_eligibility_criteria": has_eligibility_criteria,
        "has_scrape_urls": has_scrape_urls,
        "has_knowledge_facts": has_knowledge_facts,
        "setup_complete": (
            has_description and has_contacts and has_eligibility_criteria and has_knowledge_facts
        ),
    }
