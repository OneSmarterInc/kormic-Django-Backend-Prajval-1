from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# The project's global CELERY_TASK_TIME_LIMIT is 30s, sized for quick tasks
# like push notifications. A discovery crawl legitimately runs for minutes,
# so it needs its own generous limit here -- otherwise Celery hard-kills
# (SIGKILL) the worker process partway through, which can't be caught in
# Python and leaves the DiscoveryJob permanently stuck in whatever status it
# had at that instant. soft_time_limit raises a catchable exception first so
# the job can still be marked 'failed' cleanly; time_limit is the SIGKILL
# backstop, set well above what even a full max_pages=400 crawl needs.
DISCOVERY_SOFT_TIME_LIMIT = 25 * 60
DISCOVERY_HARD_TIME_LIMIT = 30 * 60


@shared_task(
    bind=True,
    max_retries=0,
    soft_time_limit=DISCOVERY_SOFT_TIME_LIMIT,
    time_limit=DISCOVERY_HARD_TIME_LIMIT,
)
def run_discovery_job(self, job_id: int) -> None:
    """Runs a DiscoveryJob's crawl, then -- if the job is set to auto_apply
    and the crawl completed cleanly -- applies the top candidate URLs to
    University.scrape_urls and runs the existing scrape_now fact-extraction
    pipeline immediately, so a discovery run can end with a populated
    knowledge base with no further manual steps."""
    from url_discovery.crawler import DirectUniversityCrawler
    from url_discovery.models import DiscoveryJob

    try:
        DirectUniversityCrawler(job_id).run()
    except Exception:
        logger.exception("Discovery job %s failed to start", job_id)
        DiscoveryJob.objects.filter(id=job_id).update(
            status="failed",
            error_message="Unexpected error starting the crawler.",
            completed_at=timezone.now(),
        )
        return

    job = DiscoveryJob.objects.filter(id=job_id).select_related("university").first()
    if job and job.status == "completed" and job.auto_apply:
        from url_discovery.services import run_auto_apply_and_scrape

        run_auto_apply_and_scrape(job)
