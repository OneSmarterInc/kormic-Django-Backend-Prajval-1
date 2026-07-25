# universities/migrations/0002_seed_legacy_universities.py
# Originally a data migration that seeded University rows for the two
# universities that used to be hardcoded in personas/university_personas.py
# (wright_state_cs, franklin_cs), so existing string references (Account,
# FitAssessment, PendingQuery, VerifiedAnswer, UniversityKnowledgeEntry,
# ChatMessage, UniversityQuestionLog, PresenterAuditLog) would keep
# resolving once that file was deleted. personas/university_personas.py is
# gone now and no environment still needs that backfill, so both directions
# are now no-ops -- this migration is kept only so its position in the
# dependency graph (django_api.0005_agent_identity) isn't disturbed. Any
# database that already had these rows seeded by the old version is
# unaffected; a fresh migrate no longer creates them.

from __future__ import annotations

from django.db import migrations


def seed_legacy_universities(apps, schema_editor):
    pass


def reverse_seed_legacy_universities(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("universities", "0001_initial"),
        ("django_api", "0005_agent_identity"),
    ]

    operations = [
        migrations.RunPython(seed_legacy_universities, reverse_seed_legacy_universities),
    ]
