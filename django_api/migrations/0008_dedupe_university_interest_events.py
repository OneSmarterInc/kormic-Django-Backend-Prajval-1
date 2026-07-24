from django.db import migrations, models


def dedupe_interest_events(apps, schema_editor):
    """
    record_university_interest used to be a bare .create() with no
    uniqueness guard, so a student re-asking the same university a question
    (or re-running a fit check) piled up one row per repeat action instead
    of one row per (student, university, source) -- e.g. a student who
    searched the same school three times ended up with three "searched"
    rows. get_shortlisted_profiles' .distinct() on student_id already
    masked this from the officer-facing shortlist, but the raw duplicates
    are still real rows that the new unique constraint below would refuse
    to coexist with. Keep the earliest row per (student, university_id,
    source) -- it's the one whose timestamp genuinely reflects "when this
    student first showed interest" -- and drop the rest.
    """
    UniversityInterestEvent = apps.get_model("django_api", "UniversityInterestEvent")

    seen = set()
    for event in UniversityInterestEvent.objects.order_by("created_at").iterator():
        key = (event.student_id, event.university_id, event.source)
        if key in seen:
            event.delete()
            continue
        seen.add(key)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("django_api", "0007_backfill_university_interest_events"),
    ]

    operations = [
        migrations.RunPython(dedupe_interest_events, noop_reverse),
        migrations.AddConstraint(
            model_name="universityinterestevent",
            constraint=models.UniqueConstraint(
                fields=("student", "university_id", "source"),
                name="unique_university_interest_event_per_source",
            ),
        ),
    ]
