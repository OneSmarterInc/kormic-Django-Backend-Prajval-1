from django.db import models


class StudentProfile(models.Model):
    """
    Persistent student profile, replacing the previous profiles/<student_id>.json file store.

    Fields that always have a well-known scalar shape are modeled as real
    columns. Nested/list structures produced by the various agents (resume
    parser, GitHub/LinkedIn analysis, fit assessments, roadmap planner, etc.)
    are modeled as dedicated JSON columns. `extra_data` is an overflow bucket
    for any additional keys those AI-driven agents may attach to a profile
    that aren't covered by an explicit column, so no data is ever dropped.
    """

    student_id = models.CharField(max_length=255, unique=True, db_index=True)

    # Display name for this student's personal agent -- the single entry
    # point the student talks to. Auto-assigned on first use (see
    # agents.agent_identity.generate_unique_agent_name) and student-editable
    # afterward. null=True (not "") so multiple not-yet-assigned rows don't
    # collide on the unique constraint.
    agent_name = models.CharField(max_length=100, unique=True, null=True, blank=True, db_index=True)

    name = models.CharField(max_length=255, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    country = models.CharField(max_length=255, blank=True, default="")
    institution = models.CharField(max_length=500, blank=True, default="")
    major = models.CharField(max_length=255, blank=True, default="")
    program = models.CharField(max_length=255, blank=True, default="")
    graduation_year = models.IntegerField(null=True, blank=True)

    gpa = models.FloatField(null=True, blank=True)
    gpa_scale = models.CharField(max_length=50, blank=True, default="")
    gpa_text = models.CharField(max_length=100, blank=True, default="")

    gre_quant = models.FloatField(null=True, blank=True)
    gre_verbal = models.FloatField(null=True, blank=True)
    toefl = models.FloatField(null=True, blank=True)
    ielts = models.FloatField(null=True, blank=True)
    english_score_text = models.CharField(max_length=100, blank=True, default="")

    budget = models.FloatField(null=True, blank=True)
    budget_text = models.CharField(max_length=100, blank=True, default="")
    work_months = models.FloatField(null=True, blank=True, default=0)

    github = models.CharField(max_length=500, blank=True, default="")
    github_assessment = models.JSONField(default=dict, blank=True)

    linkedin_url = models.CharField(max_length=500, blank=True, default="")
    linkedin_profile = models.JSONField(default=dict, blank=True)

    profile_image_path = models.CharField(max_length=1000, blank=True, default="")

    notes = models.TextField(blank=True, default="")
    source = models.CharField(max_length=100, blank=True, default="api")
    verified = models.BooleanField(default=False)

    skills = models.JSONField(default=list, blank=True)
    technical_skills = models.JSONField(default=list, blank=True)
    soft_skills = models.JSONField(default=list, blank=True)

    projects = models.JSONField(default=list, blank=True)

    research = models.TextField(blank=True, default="")
    research_interests = models.JSONField(default=list, blank=True)
    publications = models.JSONField(default=list, blank=True)
    publications_count = models.IntegerField(null=True, blank=True)

    career_goals = models.JSONField(default=list, blank=True)
    conversation_insights = models.JSONField(default=list, blank=True)
    assessments = models.JSONField(default=dict, blank=True)
    preferences = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=dict, blank=True)

    academic_intelligence = models.JSONField(default=dict, blank=True)
    technical_intelligence = models.JSONField(default=dict, blank=True)
    research_intelligence = models.JSONField(default=dict, blank=True)
    behaviour_intelligence = models.JSONField(default=dict, blank=True)

    overall_profile_score = models.IntegerField(default=0, blank=True)
    overall_profile = models.JSONField(default=dict, blank=True)
    profile_completeness = models.JSONField(default=dict, blank=True)

    strengths = models.JSONField(default=list, blank=True)
    weaknesses = models.JSONField(default=list, blank=True)
    recommendations = models.JSONField(default=list, blank=True)

    ai_summary = models.TextField(blank=True, default="")
    summary = models.TextField(blank=True, default="")

    roadmap = models.JSONField(default=dict, blank=True)

    disciplines = models.JSONField(default=list, blank=True)
    gaps = models.JSONField(default=list, blank=True)
    parser_status = models.CharField(max_length=100, blank=True, default="")
    parser_engine = models.CharField(max_length=100, blank=True, default="")
    response_mode = models.CharField(max_length=100, blank=True, default="")
    work_experience_summary = models.TextField(blank=True, default="")

    extra_data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"StudentProfile({self.student_id})"


class IntakeSession(models.Model):
    """
    Profile-intake chat session state, replacing data/api_intake_sessions.json.
    """

    student_key = models.CharField(max_length=255, unique=True, db_index=True)
    student_id = models.CharField(max_length=255)
    step = models.IntegerField(default=0)
    completed = models.BooleanField(default=False)
    answers = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"IntakeSession({self.student_key}, step={self.step}, completed={self.completed})"


class ChatMessage(models.Model):
    """
    Persistent per-turn chat transcript for Aria, university, profile-presenter,
    and intake chat, replacing the in-process-only agent-cache dicts (now in
    agents/commons.py) that were lost on every server restart.

    student_id/university_id are plain strings (not FKs) since a chat turn can
    happen before a StudentProfile row is ever saved.
    """

    class Channel(models.TextChoices):
        # The student's single persistent chat thread with their own agent.
        # Formerly "aria" -- renamed since the agent's display name is now
        # per-student and student-editable, not a fixed product name.
        AGENT = "agent", "Agent"
        UNIVERSITY = "university", "University"
        PRESENTER = "presenter", "Presenter"
        INTAKE = "intake", "Intake"

    class Sender(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    channel = models.CharField(max_length=20, choices=Channel.choices, db_index=True)
    student_id = models.CharField(max_length=255, db_index=True)
    university_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    sender = models.CharField(max_length=20, choices=Sender.choices)
    content = models.TextField()
    meta = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"ChatMessage({self.channel}, {self.student_id}, {self.sender})"


class ResumeUpload(models.Model):
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="resume_uploads")
    file_path = models.CharField(max_length=1000)
    original_filename = models.CharField(max_length=500)
    extracted_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"ResumeUpload({self.student.student_id}, {self.original_filename})"


class GitHubAnalysis(models.Model):
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="github_analyses")
    github_url = models.CharField(max_length=500)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"GitHubAnalysis({self.student.student_id}, {self.github_url})"


class LinkedInAnalysis(models.Model):
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="linkedin_analyses")
    image_paths = models.JSONField(default=list, blank=True)
    extracted = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"LinkedInAnalysis({self.student.student_id})"


class FitAssessment(models.Model):
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="fit_assessments")
    university_id = models.CharField(max_length=255, db_index=True)
    assessment = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"FitAssessment({self.student.student_id}, {self.university_id})"


class UniversityInterestEvent(models.Model):
    """
    Signal that a student has actually engaged with a specific university --
    asked it a question, or had a fit assessment run for it -- rather than
    the officer's own dashboard lookups (which must never write here, since
    that would make "interest" reflect officer curiosity instead of student
    intent). Feeds UniversityProfilesListView's shortlist: a student with no
    row here for a given university_id never appears in that university's
    officer-facing profile list, regardless of fit score.
    """

    class Source(models.TextChoices):
        SEARCHED = "searched", "Searched"
        FIT_CHECK = "fit_check", "Fit Check"

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="university_interest_events")
    university_id = models.CharField(max_length=255, db_index=True)
    source = models.CharField(max_length=20, choices=Source.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["university_id", "student"])]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "university_id", "source"],
                name="unique_university_interest_event_per_source",
            )
        ]

    def __str__(self) -> str:
        return f"UniversityInterestEvent({self.student.student_id}, {self.university_id}, {self.source})"


class RoadmapVersion(models.Model):
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="roadmap_versions")
    request_message = models.TextField(blank=True, default="")
    roadmap = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"RoadmapVersion({self.student.student_id})"


class AriaMemory(models.Model):
    """
    Persistent Aria long-term memory per student, replacing the previous
    memory/<student_key>_memory.json file store.
    """

    student_id = models.CharField(max_length=255, unique=True, db_index=True)
    important_points = models.JSONField(default=list, blank=True)
    universities_discussed = models.JSONField(default=list, blank=True)
    github_profiles_analyzed = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"AriaMemory({self.student_id})"


class UniversityKnowledgeEntry(models.Model):
    """
    Persistent knowledge-base fact for a university agent, replacing the
    previous in-memory-only UniversityKnowledgeBase that was rebuilt from
    seed data (and lost any scraped/learned facts) on every server restart.
    """

    university_id = models.CharField(max_length=255, db_index=True)
    topic = models.CharField(max_length=500)
    content = models.TextField()
    source_type = models.CharField(max_length=50, default="unknown")
    source_url = models.CharField(max_length=1000, blank=True, null=True)
    confidence = models.FloatField(default=1.0)
    times_used = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confidence", "-times_used"]

    def __str__(self) -> str:
        return f"UniversityKnowledgeEntry({self.university_id}, {self.topic[:40]})"


class PendingQuery(models.Model):
    """
    Escalated student question awaiting human/university verification,
    replacing the previous data/pending_queries.json file store.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"

    university_id = models.CharField(max_length=255, db_index=True)
    university_name = models.CharField(max_length=255, blank=True, default="")
    agent_name = models.CharField(max_length=255, blank=True, default="")
    student_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    student_name = models.CharField(max_length=255, blank=True, default="")
    program = models.CharField(max_length=255, blank=True, default="")
    question = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    urgency_reason = models.TextField(blank=True, default="")
    escalation_chain = models.JSONField(default=list, blank=True)
    answer = models.TextField(blank=True, default="")
    answered_by = models.CharField(max_length=255, blank=True, default="")
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def display_status(self) -> str:
        if self.status == self.Status.RESOLVED:
            return "answered"
        return "urgent" if self.priority == self.Priority.URGENT else "pending"

    def __str__(self) -> str:
        return f"PendingQuery(#{self.id}, {self.university_id}, {self.status})"


class VerifiedAnswer(models.Model):
    """
    Durable human-verified answer for a university agent, replacing the
    previous knowledge/human_verified_answers.json file store.
    """

    query = models.ForeignKey(
        PendingQuery, on_delete=models.SET_NULL, null=True, blank=True, related_name="verified_answers"
    )
    university_id = models.CharField(max_length=255, db_index=True)
    question = models.TextField()
    answer = models.TextField()
    answered_by = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=100, blank=True, default="")
    confidence = models.FloatField(default=1.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"VerifiedAnswer({self.university_id}, {self.question[:40]})"


class UniversityQuestionLog(models.Model):
    """
    Officer-facing question log for the profile presenter chat, replacing
    the previous knowledge/university_questions.json file store.
    """

    university_id = models.CharField(max_length=255, db_index=True)
    student_name = models.CharField(max_length=255, blank=True, default="")
    question = models.TextField()
    topic = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"UniversityQuestionLog({self.university_id}, {self.topic})"


class PresenterAuditLog(models.Model):
    """
    Error/audit trail for ProfilePresenterAgent, replacing the previous
    knowledge/profile_presenter_audit.json file store.
    """

    university_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    event = models.CharField(max_length=100)
    message = models.TextField(blank=True, default="")
    details = models.TextField(blank=True, default="")
    profile_name = models.CharField(max_length=255, blank=True, default="")
    question = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"PresenterAuditLog({self.event}, {self.created_at})"
