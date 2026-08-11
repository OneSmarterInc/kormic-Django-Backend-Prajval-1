from __future__ import annotations

from django.test import TestCase
from rest_framework import status

from django_api.models import PendingQuery
from django_api.tests import make_university_client
from universities.knowledge_groups import (
    classify_group_slug,
    ensure_default_groups,
    escalation_counts_by_group,
    resolve_group_for_question,
)
from universities.models import KnowledgeGroup, University


class KnowledgeGroupClassificationTests(TestCase):
    def test_classifies_international_keywords(self):
        self.assertEqual(
            classify_group_slug("Do I need a new I-20 for CPT?"), KnowledgeGroup.Slug.INTERNATIONAL
        )

    def test_classifies_money_keywords(self):
        self.assertEqual(
            classify_group_slug("What is the assistantship stipend?"), KnowledgeGroup.Slug.MONEY
        )

    def test_classifies_campus_life_keywords(self):
        self.assertEqual(
            classify_group_slug("Is there a meal plan option in the dining hall?"), KnowledgeGroup.Slug.CAMPUS_LIFE
        )

    def test_defaults_to_admissions_when_nothing_matches(self):
        self.assertEqual(classify_group_slug("What is the application deadline?"), KnowledgeGroup.Slug.ADMISSIONS)
        self.assertEqual(classify_group_slug(""), KnowledgeGroup.Slug.ADMISSIONS)
        self.assertEqual(classify_group_slug("random unrelated text"), KnowledgeGroup.Slug.ADMISSIONS)


class KnowledgeGroupResolutionTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(id="write_state", name="Write State")

    def test_no_groups_configured_returns_none(self):
        self.assertIsNone(resolve_group_for_question(self.university.id, "What is the tuition?"))

    def test_resolves_matching_group(self):
        ensure_default_groups(self.university)

        group = resolve_group_for_question(self.university.id, "What is the assistantship stipend?")

        self.assertEqual(group.slug, KnowledgeGroup.Slug.MONEY)

    def test_falls_back_to_first_configured_group_when_matching_slug_missing(self):
        KnowledgeGroup.objects.create(
            university=self.university,
            slug=KnowledgeGroup.Slug.CAMPUS_LIFE,
            escalation_contact_email="life@wsu.edu",
        )

        group = resolve_group_for_question(self.university.id, "What is the assistantship stipend?")

        self.assertEqual(group.slug, KnowledgeGroup.Slug.CAMPUS_LIFE)

    def test_ensure_default_groups_is_idempotent(self):
        created_first = ensure_default_groups(self.university)
        self.assertEqual(len(created_first), 4)

        created_second = ensure_default_groups(self.university)

        self.assertEqual(created_second, [])
        self.assertEqual(self.university.knowledge_groups.count(), 4)


class EscalationCountsTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            id="write_state_counts", name="Write State", contact_email="admissions@wsu.edu"
        )
        ensure_default_groups(self.university)

    def test_counts_by_group(self):
        money_group = self.university.knowledge_groups.get(slug=KnowledgeGroup.Slug.MONEY)
        PendingQuery.objects.create(university_id=self.university.id, question="stipend?", group=money_group)
        PendingQuery.objects.create(university_id=self.university.id, question="stipend again?", group=money_group)

        counts = escalation_counts_by_group(self.university.id)

        self.assertEqual(counts[KnowledgeGroup.Slug.MONEY], 2)
        self.assertEqual(counts[KnowledgeGroup.Slug.ADMISSIONS], 0)


class KnowledgeGroupAPITests(TestCase):
    def setUp(self):
        self.client = make_university_client(email="officer1@wsu.edu", university_id="write_state_api")

    def test_get_lazily_bootstraps_four_groups(self):
        resp = self.client.get("/api/university-admin/knowledge-groups/")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        slugs = {g["slug"] for g in resp.data["groups"]}
        self.assertEqual(slugs, {"admissions", "international", "money", "campus_life"})
        self.assertTrue(all(g["escalation_count"] == 0 for g in resp.data["groups"]))

    def test_patch_updates_contact(self):
        self.client.get("/api/university-admin/knowledge-groups/")

        resp = self.client.patch(
            "/api/university-admin/knowledge-groups/money/",
            {"escalation_contact_name": "Bursar Office", "escalation_contact_email": "bursar@wsu.edu"},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["escalation_contact_email"], "bursar@wsu.edu")
        self.assertEqual(resp.data["escalation_contact_name"], "Bursar Office")

    def test_patch_unknown_group_404s(self):
        resp = self.client.patch(
            "/api/university-admin/knowledge-groups/not-a-real-group/",
            {"escalation_contact_name": "x"},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class ManualKnowledgeFactGroupTaggingTests(TestCase):
    def setUp(self):
        self.client = make_university_client(email="officer2@wsu.edu", university_id="write_state_facts")
        self.university = University.objects.get(id="write_state_facts")
        ensure_default_groups(self.university)

    def test_post_tags_fact_with_group(self):
        resp = self.client.post(
            "/api/university-admin/knowledge/",
            {"topic": "Assistantship stipend", "content": "$20,000/year", "group": "money"},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["group"], "money")

    def test_post_rejects_unknown_group(self):
        resp = self.client.post(
            "/api/university-admin/knowledge/",
            {"topic": "x", "content": "y", "group": "not-a-real-group"},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_without_group_leaves_fact_ungrouped(self):
        resp = self.client.post(
            "/api/university-admin/knowledge/",
            {"topic": "x", "content": "y"},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(resp.data["group"])
