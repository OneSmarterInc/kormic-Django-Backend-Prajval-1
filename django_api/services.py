from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from django_api.models import (
    GitHubAnalysis,
    LinkedInAnalysis,
    ResumeUpload,
    StudentProfile,
)


BASE_DIR = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BASE_DIR / "uploads"

UPLOADS_DIR.mkdir(exist_ok=True)


# Columns on StudentProfile that participate in the flat profile dict.
# Anything in a saved profile dict that isn't one of these keys (plus
# student_id/created_at/updated_at, handled specially) is preserved in
# `extra_data` so no data from the agents is ever dropped.
PROFILE_FIELDS: List[str] = [
    "name", "email", "country", "institution", "major", "program", "graduation_year",
    "gpa", "gpa_scale", "gpa_text",
    "gre_quant", "gre_verbal", "toefl", "ielts", "english_score_text",
    "budget", "budget_text", "work_months",
    "github", "github_assessment",
    "linkedin_url", "linkedin_profile",
    "notes", "source", "verified",
    "skills", "technical_skills", "soft_skills",
    "projects",
    "research", "research_interests", "publications", "publications_count",
    "career_goals", "conversation_insights", "assessments", "preferences", "evidence",
    "academic_intelligence", "technical_intelligence", "research_intelligence", "behaviour_intelligence",
    "overall_profile_score", "overall_profile", "profile_completeness",
    "strengths", "weaknesses", "recommendations",
    "ai_summary", "summary",
    "roadmap",
    "disciplines", "gaps", "parser_status", "parser_engine", "response_mode",
    "work_experience_summary",
]


def make_student_id(value: str) -> str:
    cleaned = str(value or "student").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned)
    cleaned = cleaned.strip("_")
    return cleaned or "student"


def _profile_to_dict(profile: StudentProfile) -> Dict[str, Any]:
    data: Dict[str, Any] = dict(profile.extra_data or {})

    for field in PROFILE_FIELDS:
        data[field] = getattr(profile, field)

    data["student_id"] = profile.student_id
    data["created_at"] = profile.created_at.strftime("%Y-%m-%d %H:%M:%S")
    data["updated_at"] = profile.updated_at.strftime("%Y-%m-%d %H:%M:%S")

    return data


def _apply_dict_to_profile(profile: StudentProfile, data: Dict[str, Any]) -> None:
    extra_data = dict(profile.extra_data or {})

    for key, value in (data or {}).items():
        if key in ("student_id", "created_at", "updated_at"):
            continue
        if key in PROFILE_FIELDS:
            if value is None:
                # Columns default to "", [] or {} rather than NULL; only the
                # genuinely nullable numeric columns keep None as None.
                existing = getattr(profile, key)
                if isinstance(existing, str):
                    value = ""
                elif isinstance(existing, list):
                    value = []
                elif isinstance(existing, dict):
                    value = {}
            setattr(profile, key, value)
        else:
            extra_data[key] = value

    profile.extra_data = extra_data


def get_profile_path(student_id: str) -> str:
    """Kept for backward-compatible informational responses; profiles are DB-backed now."""
    student_id = make_student_id(student_id)
    return f"db://student_profiles/{student_id}"


def load_profile_data(student_id: str) -> Dict[str, Any]:
    student_id = make_student_id(student_id)
    profile = StudentProfile.objects.filter(student_id=student_id).first()

    if profile is None:
        return {
            "student_id": student_id,
            "name": student_id,
            "conversation_insights": [],
            "assessments": {},
            "preferences": {},
            "evidence": {},
            "source": "api",
            "verified": False,
        }

    return _profile_to_dict(profile)


def save_profile_data(student_id: str, data: Dict[str, Any]) -> str:
    student_id = make_student_id(student_id)
    data = dict(data or {})
    data["student_id"] = student_id

    profile, _ = StudentProfile.objects.get_or_create(student_id=student_id)
    _apply_dict_to_profile(profile, data)
    profile.save()

    return get_profile_path(student_id)


def generate_summary(profile: Dict[str, Any]) -> str:
    preferences = profile.get("preferences", {}) or {}
    summary = f"""
Student Profile Summary
-----------------------
Name: {profile.get('name', 'Unknown')}
Student ID: {profile.get('student_id', 'student')}
GPA: {profile.get('gpa', 'Not Provided')}
GRE Quant: {profile.get('gre_quant', 'Not Provided')}
TOEFL/IELTS: {profile.get('toefl') or profile.get('ielts') or 'Not Provided'}
Budget: {profile.get('budget', 'Not Provided')}
Target Country: {preferences.get('target_country', 'Not Provided')}
Preferred Specialization: {preferences.get('preferred_specialization', 'Not Provided')}

Conversation Insights:
{len(profile.get('conversation_insights', []))}

University Assessments:
{len(profile.get('assessments', {}))}
"""
    profile["summary"] = summary
    return summary


def create_or_update_profile(validated_data: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(validated_data)
    raw_student_id = data.get("student_id")
    name = data.get("name") or raw_student_id or "Student"
    student_id = make_student_id(raw_student_id or name)

    profile = load_profile_data(student_id)
    profile["student_id"] = student_id

    direct_fields = [
        "name", "email", "country", "institution", "major", "graduation_year",
        "gpa", "gpa_scale", "gre_quant", "gre_verbal", "toefl", "ielts",
        "budget", "github", "linkedin_url", "notes",
    ]

    for field in direct_fields:
        if field in data and data[field] not in [None, ""]:
            profile[field] = data[field]

    preference_fields = ["target_country", "target_degree", "preferred_specialization"]
    profile.setdefault("preferences", {})

    for field in preference_fields:
        if field in data and data[field] not in [None, ""]:
            profile["preferences"][field] = data[field]

    profile.setdefault("conversation_insights", [])
    profile.setdefault("assessments", {})
    profile.setdefault("evidence", {})
    profile["evidence"]["manual_profile_api"] = data

    generate_summary(profile)
    path = save_profile_data(student_id, profile)

    return {"student_id": student_id, "profile_file": str(path), "profile": profile}


def get_profile(student_id: str) -> Dict[str, Any]:
    student_id = make_student_id(student_id)

    if not StudentProfile.objects.filter(student_id=student_id).exists():
        raise FileNotFoundError(f"Profile not found for student_id: {student_id}")

    return load_profile_data(student_id)


def save_uploaded_file(student_id: str, uploaded_file, folder_name: str) -> Path:
    student_id = make_student_id(student_id)
    target_dir = UPLOADS_DIR / folder_name / student_id
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(uploaded_file.name).name
    # Suffix with a short uuid so repeated uploads of the same filename don't
    # clobber each other on disk -- needed so upload-history rows keep
    # pointing at distinct files.
    unique_name = f"{Path(safe_name).stem}__{uuid.uuid4().hex[:8]}{Path(safe_name).suffix}"
    file_path = target_dir / unique_name

    with open(file_path, "wb") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)

    return file_path


def merge_resume_data_into_profile(student_id: str, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
    profile = load_profile_data(student_id)

    field_map = {
        "undergraduate_institution": "institution",
        "undergraduate_major": "major",
        "work_experience_months": "work_months",
        "research_experience": "research",
        "technical_skills": "skills",
        "inferred_disciplines": "disciplines",
    }

    for key, value in (extracted_data or {}).items():
        if value in [None, "", [], {}]:
            continue
        mapped_key = field_map.get(key, key)
        profile[mapped_key] = value

    profile.setdefault("evidence", {})
    profile["evidence"]["resume"] = extracted_data
    generate_summary(profile)
    save_profile_data(student_id, profile)
    return profile


def parse_resume(student_id: str, uploaded_file) -> Dict[str, Any]:
    student_id = make_student_id(student_id)
    file_path = save_uploaded_file(student_id, uploaded_file, "resumes")

    from agents.resume_parser import ResumeParserAgent

    parser = ResumeParserAgent()
    extracted_data = parser.parse(str(file_path))
    updated_profile = merge_resume_data_into_profile(student_id, extracted_data)

    ResumeUpload.objects.create(
        student=StudentProfile.objects.get(student_id=student_id),
        file_path=str(file_path),
        original_filename=uploaded_file.name,
        extracted_data=extracted_data,
    )

    return {
        "student_id": student_id,
        "file_path": str(file_path),
        "extracted_data": extracted_data,
        "profile": updated_profile,
    }


def analyze_github(student_id: str, github_url: str) -> Dict[str, Any]:
    student_id = make_student_id(student_id)
    profile = load_profile_data(student_id)

    from agents.github_agent import GitHubSkillsAgent

    github_agent = GitHubSkillsAgent()

    if hasattr(github_agent, "analyse"):
        github_result = github_agent.analyse(github_url)
    else:
        github_result = github_agent.analyze(github_url)

    if isinstance(github_result, dict) and github_result.get("error"):
        raise ValueError(github_result.get("error"))

    profile["github"] = github_url
    profile["github_assessment"] = github_result

    existing_skills = list(profile.get("skills", []) or [])
    skills_added = []

    for item in github_result.get("languages", []) or []:
        skill = item.get("name") if isinstance(item, dict) else str(item)
        if skill and skill not in existing_skills:
            existing_skills.append(skill)
            skills_added.append(skill)

    for tool in github_result.get("frameworks_and_tools", []) or []:
        if tool and tool not in existing_skills:
            existing_skills.append(tool)
            skills_added.append(tool)

    profile["skills"] = existing_skills[:80]
    profile.setdefault("evidence", {})
    profile["evidence"]["github"] = {"github_url": github_url, "result": github_result}
    generate_summary(profile)
    save_profile_data(student_id, profile)

    GitHubAnalysis.objects.create(
        student=StudentProfile.objects.get(student_id=student_id),
        github_url=github_url,
        result=github_result,
    )

    return {
        "student_id": student_id,
        "github_result": github_result,
        "skills_added": skills_added,
        "profile": profile,
    }


def analyze_linkedin(student_id: str, uploaded_images: List[Any]) -> Dict[str, Any]:
    student_id = make_student_id(student_id)

    image_paths = []
    for image in uploaded_images:
        saved_path = save_uploaded_file(student_id, image, "linkedin")
        image_paths.append(str(saved_path))

    from agents.linkedin_agent import LinkedInAgent

    linkedin_agent = LinkedInAgent()
    extracted = linkedin_agent.extract(image_paths)

    profile = load_profile_data(student_id)
    profile["linkedin_profile"] = extracted

    if not profile.get("name") and isinstance(extracted, dict) and extracted.get("name"):
        profile["name"] = extracted["name"]

    if not profile.get("country") and isinstance(extracted, dict) and extracted.get("location"):
        profile["country"] = extracted["location"]

    existing_skills = list(profile.get("skills", []) or [])
    skills_added = []

    if isinstance(extracted, dict):
        for skill in extracted.get("skills", []) or []:
            if skill and skill not in existing_skills:
                existing_skills.append(skill)
                skills_added.append(skill)

    profile["skills"] = existing_skills[:80]
    profile.setdefault("evidence", {})
    profile["evidence"]["linkedin"] = {"image_paths": image_paths, "result": extracted}
    generate_summary(profile)
    save_profile_data(student_id, profile)

    LinkedInAnalysis.objects.create(
        student=StudentProfile.objects.get(student_id=student_id),
        image_paths=image_paths,
        extracted=extracted,
    )

    return {
        "student_id": student_id,
        "image_paths": image_paths,
        "extracted": extracted,
        "skills_added": skills_added,
        "profile": profile,
    }
