from django.db import migrations


def backfill_interest_events(apps, schema_editor):
    """
    UniversityInterestEvent only started being written once this feature
    shipped, so any FitAssessment generated before that (i.e. every one that
    already existed) has no matching interest event -- which made the
    officer-facing shortlist (django_api.services.get_shortlisted_profiles)
    come back empty even for students who already have a real, on-file fit
    score for that university. A FitAssessment row is itself proof the
    student's agent ran a fit check for that university, so backfill one
    "fit_check" interest event per distinct (student, university) pair,
    stamped with that first assessment's own timestamp rather than "now".
    """
    FitAssessment = apps.get_model("django_api", "FitAssessment")
    UniversityInterestEvent = apps.get_model("django_api", "UniversityInterestEvent")

    seen = set()
    for assessment in FitAssessment.objects.order_by("created_at").iterator():
        key = (assessment.student_id, assessment.university_id)
        if key in seen:
            continue
        seen.add(key)

        if UniversityInterestEvent.objects.filter(
            student_id=assessment.student_id, university_id=assessment.university_id
        ).exists():
            continue

        event = UniversityInterestEvent.objects.create(
            student_id=assessment.student_id,
            university_id=assessment.university_id,
            source="fit_check",
        )
        # auto_now_add always stamps "now" on create (even from a data
        # migration) -- overwrite via update(), which bypasses that.
        UniversityInterestEvent.objects.filter(pk=event.pk).update(created_at=assessment.created_at)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("django_api", "0006_universityinterestevent"),
    ]

    operations = [
        migrations.RunPython(backfill_interest_events, noop_reverse),
    ]
