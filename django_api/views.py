from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from django.http import FileResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import (
    IsStudentOrUniversityRole,
    IsStudentRole,
    IsTOTPEnrolled,
    IsUniversityRole,
    ScopedToOwnStudentId,
    ScopedToOwnUniversityId,
    get_account,
)
from django_api.models import (
    ChatMessage,
    FitAssessment,
    GitHubAnalysis,
    IntakeSession,
    LinkedInAnalysis,
    PendingQuery,
    ResumeUpload,
    RoadmapVersion,
    StudentProfile,
    UniversityQuestionLog,
    VerifiedAnswer,
)
from django_api.serializers import (
    ProfileCreateUpdateSerializer,
    ResumeUploadSerializer,
    GitHubAnalyzeSerializer,
)
from django_api.services import (
    create_or_update_profile,
    delete_profile_image,
    format_profile_response,
    get_profile,
    get_profile_image_path,
    parse_resume,
    analyze_github,
    analyze_linkedin,
    load_profile_data,
    save_profile_data,
    make_student_id,
    upload_profile_image,
)

STUDENT_PERMISSIONS = [IsAuthenticated, IsTOTPEnrolled, IsStudentRole]
STUDENT_OWNER_PERMISSIONS = [IsAuthenticated, IsTOTPEnrolled, IsStudentRole, ScopedToOwnStudentId]
UNIVERSITY_OWNER_PERMISSIONS = [IsAuthenticated, IsTOTPEnrolled, IsUniversityRole, ScopedToOwnUniversityId]


def log_chat_turn(*, channel, student_id, university_id="", user_message="", assistant_message="", meta=None):
    ChatMessage.objects.create(
        channel=channel,
        student_id=student_id,
        university_id=university_id or "",
        sender=ChatMessage.Sender.USER,
        content=user_message,
    )
    ChatMessage.objects.create(
        channel=channel,
        student_id=student_id,
        university_id=university_id or "",
        sender=ChatMessage.Sender.ASSISTANT,
        content=assistant_message,
        meta=meta or {},
    )


# In-memory cache while Django server is running.
ARIA_SESSIONS: Dict[str, Any] = {}
UNIVERSITY_AGENTS: Dict[str, Any] = {}
PROFILE_PRESENTERS: Dict[str, Any] = {}


# ---------------------------------------------------------------------
# Home / health check
# ---------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([AllowAny])
def api_home(request):
    return Response({
        "message": "Korgut Commons Django REST API is running",
        "profile_apis": {
            "create_update_profile": "POST /api/profile/",
            "get_profile": "GET /api/profile/<student_id>/",
            "resume": "POST /api/profile/resume/",
            "resume_download": "GET /api/profile/resume/<resume_id>/",
            "resume_delete": "DELETE /api/profile/resume/<resume_id>/",
            "github": "POST /api/profile/github/",
            "linkedin": "POST /api/profile/linkedin/",
            "linkedin_history": "GET /api/profile/<student_id>/linkedin-history/",
            "linkedin_image": "GET /api/profile/linkedin/<analysis_id>/images/<index>/",
            "profile_image_upload": "POST /api/profile/image/",
            "profile_image": "GET /api/profile/<student_id>/image/",
            "profile_image_delete": "DELETE /api/profile/<student_id>/image/",
        },
        "chat_apis": {
            "profile_intake": "POST /api/chat/intake/",
            "aria_chat": "POST /api/chat/aria/",
            "university_chat": "POST /api/chat/university/<university_id>/",
        },
        "core_apis": {
            "fit_assessment": "POST /api/assessments/generate/<university_id>/",
            "roadmap": "GET /api/roadmap/<student_id>/",
            "pending_queries": "GET /api/queries/pending/",
            "answer_query": "POST /api/queries/answer/",
            "edit_query": "POST /api/queries/<query_id>/edit/",
            "profile_pdf": "GET /api/exports/pdf/<student_id>/",
        },
        "university_dashboard_apis": {
            "profiles": "GET /api/university/<university_id>/profiles/",
            "profile_presenter_chat": "POST /api/university/<university_id>/profile/<student_id>/chat/",
            "questions": "GET /api/university/<university_id>/questions/",
            "queries": "GET /api/university/<university_id>/queries/",
            "active_queries": "GET /api/university/<university_id>/queries/active/",
            "archive_queries": "GET /api/university/<university_id>/queries/archive/",
            "verified_knowledge": "GET /api/university/<university_id>/knowledge/verified/",
        },
        "university_ids": ["wright_state_cs", "franklin_cs"],
    })


# ---------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------

def api_error(message: str, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({"status": "error", "message": str(message)}, status=http_status)


def load_intake_session(student_key: str) -> Optional[Dict[str, Any]]:
    session = IntakeSession.objects.filter(student_key=student_key).first()

    if session is None:
        return None

    return {
        "student_id": session.student_id,
        "step": session.step,
        "answers": dict(session.answers or {}),
        "completed": session.completed,
    }


def save_intake_session(student_key: str, session: Dict[str, Any]) -> None:
    obj, _ = IntakeSession.objects.get_or_create(student_key=student_key)
    obj.student_id = session.get("student_id", student_key)
    obj.step = int(session.get("step", 0))
    obj.completed = bool(session.get("completed", False))
    obj.answers = dict(session.get("answers", {}) or {})
    obj.save()


def extract_number(value: str):
    match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))

    if not match:
        return None

    number = float(match.group(1))
    return int(number) if number.is_integer() else number


def safe_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def load_profile_or_404(student_id):
    try:
        profile_data = get_profile(student_id)
        return profile_data, None

    except FileNotFoundError:
        return None, Response(
            {
                "status": "failed",
                "message": f"Profile not found for student_id: {student_id}",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    except Exception as exc:
        return None, Response(
            {
                "status": "failed",
                "message": "Could not load student profile",
                "error": str(exc),
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def call_first_available_method(obj, method_names, *args):
    for method_name in method_names:
        if not hasattr(obj, method_name):
            continue

        method = getattr(obj, method_name)

        try:
            return method(*args)
        except TypeError:
            try:
                return method()
            except TypeError:
                continue

    raise AttributeError(
        f"No supported method found. Tried: {', '.join(method_names)}"
    )


# ---------------------------------------------------------------------
# APIs 1-5: Profile Management
# ---------------------------------------------------------------------

class ProfileCreateUpdateAPIView(APIView):
    """
    POST /api/profile/
    Create or update base student profile.
    """

    permission_classes = STUDENT_PERMISSIONS

    def post(self, request):
        serializer = ProfileCreateUpdateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = dict(serializer.validated_data)
            data["student_id"] = request.user.account.student_id
            result = create_or_update_profile(data)
            return Response(
                {
                    "status": "success",
                    "message": "Profile updated",
                    "student_id": result["student_id"],
                    "profile_file": result["profile_file"],
                    "profile": result["profile"],
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            return api_error(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProfileDetailAPIView(APIView):
    """
    GET /api/profile/<student_id>/
    Fetch saved student profile JSON.
    """

    permission_classes = STUDENT_OWNER_PERMISSIONS

    def get(self, request, student_id):
        try:
            profile = get_profile(student_id)
            profile["profile_image_url"] = (
                request.build_absolute_uri(f"/api/profile/{student_id}/image/")
                if get_profile_image_path(student_id)
                else None
            )
            return Response(format_profile_response(profile), status=status.HTTP_200_OK)
        except FileNotFoundError as exc:
            return api_error(str(exc), status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            return api_error(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProfileImageUploadAPIView(APIView):
    """
    POST /api/profile/image/
    Upload/replace the authenticated student's profile picture. Unlike
    resume/LinkedIn uploads, this is not a history -- each call replaces
    the previous image.
    """

    permission_classes = STUDENT_PERMISSIONS

    def post(self, request):
        student_id = request.user.account.student_id
        image = request.FILES.get("image")

        if not image:
            return api_error("An image file is required using key 'image'.")

        if image.content_type and not image.content_type.startswith("image/"):
            return api_error(f"Unsupported file type: {image.content_type}. Upload an image file.")

        try:
            upload_profile_image(student_id=student_id, uploaded_file=image)
            return Response(
                {
                    "status": "success",
                    "student_id": student_id,
                    "profile_image_url": request.build_absolute_uri(f"/api/profile/{student_id}/image/"),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            return api_error(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProfileImageDetailAPIView(APIView):
    """
    GET /api/profile/<student_id>/image/ — download the current profile picture.
    Readable by the owning student or any university officer (dashboard
    rosters list every student's picture), matching UniversityProfilesListView.
    DELETE /api/profile/<student_id>/image/ — remove it; owner-only.
    """

    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsStudentOrUniversityRole]

    def get(self, request, student_id):
        account = get_account(request)
        if account.role == "student" and account.student_id != student_id:
            return api_error("You may only access your own profile picture.", status.HTTP_403_FORBIDDEN)

        image_path = get_profile_image_path(student_id)
        if not image_path:
            return api_error("No profile picture uploaded for this student.", status.HTTP_404_NOT_FOUND)

        file_path = Path(image_path)
        if not file_path.exists():
            return api_error("Profile picture file is missing on the server.", status.HTTP_404_NOT_FOUND)

        content = file_path.read_bytes()
        return FileResponse(io.BytesIO(content), as_attachment=False, filename=file_path.name)

    def delete(self, request, student_id):
        account = get_account(request)
        if account.role != "student" or account.student_id != student_id:
            return api_error("You may only delete your own profile picture.", status.HTTP_403_FORBIDDEN)

        removed = delete_profile_image(student_id)
        if not removed:
            return api_error("No profile picture uploaded for this student.", status.HTTP_404_NOT_FOUND)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ResumeUploadAPIView(APIView):
    """
    POST /api/profile/resume/
    Upload and parse resume PDF/DOCX.
    """

    permission_classes = STUDENT_PERMISSIONS

    def post(self, request):
        data = request.data.copy()
        data["student_id"] = request.user.account.student_id
        serializer = ResumeUploadSerializer(data=data)

        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = parse_resume(
                student_id=serializer.validated_data["student_id"],
                uploaded_file=serializer.validated_data["file"],
            )
            return Response(
                {
                    "status": "success",
                    "student_id": result["student_id"],
                    "resume_id": result["resume_id"],
                    "resume_url": request.build_absolute_uri(f"/api/profile/resume/{result['resume_id']}/"),
                    "extracted_data": result["extracted_data"],
                    "profile": result["profile"],
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            return api_error(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


class ResumeDetailAPIView(APIView):
    """
    GET /api/profile/resume/<resume_id>/ — download the original resume file.
    DELETE /api/profile/resume/<resume_id>/ — remove a resume upload (file + row).
    """

    permission_classes = STUDENT_PERMISSIONS

    def _get_owned_resume(self, request, resume_id):
        try:
            resume = ResumeUpload.objects.select_related("student").get(pk=resume_id)
        except ResumeUpload.DoesNotExist:
            return None, api_error("Resume not found.", status.HTTP_404_NOT_FOUND)

        if resume.student.student_id != request.user.account.student_id:
            return None, api_error("You may only access your own resumes.", status.HTTP_403_FORBIDDEN)

        return resume, None

    def get(self, request, resume_id):
        resume, error_response = self._get_owned_resume(request, resume_id)
        if error_response:
            return error_response

        file_path = Path(resume.file_path)
        if not file_path.exists():
            return api_error("Resume file is missing on the server.", status.HTTP_404_NOT_FOUND)

        # Read fully into memory rather than handing FileResponse an open
        # file handle: on Windows an open handle blocks a same-request-cycle
        # delete/unlink of that file, and this keeps the handle short-lived
        # regardless of platform.
        content = file_path.read_bytes()
        return FileResponse(io.BytesIO(content), as_attachment=True, filename=resume.original_filename)

    def delete(self, request, resume_id):
        resume, error_response = self._get_owned_resume(request, resume_id)
        if error_response:
            return error_response

        file_path = Path(resume.file_path)
        if file_path.exists():
            file_path.unlink(missing_ok=True)

        resume.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GitHubAnalyzeAPIView(APIView):
    """
    POST /api/profile/github/
    Analyze GitHub and update profile skills/evidence.
    """

    permission_classes = STUDENT_PERMISSIONS

    def post(self, request):
        data = request.data.copy()
        data["student_id"] = request.user.account.student_id
        serializer = GitHubAnalyzeSerializer(data=data)

        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = analyze_github(
                student_id=serializer.validated_data["student_id"],
                github_url=serializer.validated_data["github_url"],
            )
            return Response(
                {
                    "status": "success",
                    "student_id": result["student_id"],
                    "skills_added": result["skills_added"],
                    "github_result": result["github_result"],
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            return api_error(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


def build_linkedin_images_payload(request, analysis_id: int, image_paths):
    """
    Turns the absolute on-disk paths stored on a LinkedInAnalysis row into a
    frontend-usable shape: a full, directly-fetchable URL to the image
    (raw MEDIA_URL static serving isn't used here since these screenshots
    are private -- serving must stay behind the same JWT+ownership check as
    the rest of the API, so the URL still requires an Authorization header
    when fetched -- it just isn't a bare relative path anymore).
    """
    payload = []

    for index, path in enumerate(image_paths or []):
        relative_url = f"/api/profile/linkedin/{analysis_id}/images/{index}/"

        payload.append({
            "index": index,
            "uploaded_image_url": request.build_absolute_uri(relative_url),
        })

    return payload


class LinkedInAnalyzeAPIView(APIView):
    """
    POST /api/profile/linkedin/
    Upload LinkedIn screenshots and update profile. Safe to call again later
    to add more screenshots -- each call is a new history entry (same
    pattern as repeated resume uploads), nothing is overwritten.
    """

    permission_classes = STUDENT_PERMISSIONS

    def post(self, request):
        student_id = request.user.account.student_id
        images = request.FILES.getlist("images")

        if not images:
            return api_error("At least one image is required using key 'images'.")

        try:
            result = analyze_linkedin(student_id=student_id, uploaded_images=images)
            return Response(
                {
                    "status": "success",
                    "student_id": result["student_id"],
                    "analysis_id": result["analysis_id"],
                    "images": build_linkedin_images_payload(request, result["analysis_id"], result["image_paths"]),
                    "skills_added": result["skills_added"],
                    "extracted": result["extracted"],
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            return api_error(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


class LinkedInImageDetailAPIView(APIView):
    """
    GET /api/profile/linkedin/<analysis_id>/images/<index>/
    Streams one uploaded LinkedIn screenshot, scoped to the owning student.
    """

    permission_classes = STUDENT_PERMISSIONS

    def get(self, request, analysis_id, index):
        try:
            analysis = LinkedInAnalysis.objects.select_related("student").get(pk=analysis_id)
        except LinkedInAnalysis.DoesNotExist:
            return api_error("LinkedIn analysis not found.", status.HTTP_404_NOT_FOUND)

        if analysis.student.student_id != request.user.account.student_id:
            return api_error("You may only access your own LinkedIn images.", status.HTTP_403_FORBIDDEN)

        image_paths = analysis.image_paths or []
        if index < 0 or index >= len(image_paths):
            return api_error("Image index out of range.", status.HTTP_404_NOT_FOUND)

        file_path = Path(image_paths[index])
        if not file_path.exists():
            return api_error("Image file is missing on the server.", status.HTTP_404_NOT_FOUND)

        content = file_path.read_bytes()
        return FileResponse(io.BytesIO(content), as_attachment=False, filename=file_path.name)


# ---------------------------------------------------------------------
# API 6: Profile Intake Chat
# ---------------------------------------------------------------------

INTAKE_FIELDS = [
    {"key": "name", "question": "What is your full name?"},
    {"key": "target_country", "question": "Which country are you targeting for graduate study?"},
    {"key": "program", "question": "Which program or degree are you targeting? Example: MS Computer Science."},
    {"key": "major", "question": "What was your undergraduate major?"},
    {"key": "institution", "question": "What is your undergraduate institution name?"},
    {"key": "gpa", "question": "What is your GPA and scale? Example: 8.2/10 or 3.4/4."},
    {"key": "budget", "question": "What is your annual budget in USD? Example: 40000."},
    {"key": "gre_quant", "question": "What is your GRE Quant score? If not taken, say not taken."},
    {"key": "toefl", "question": "What is your TOEFL score? If you have IELTS instead, mention IELTS."},
    {"key": "research", "question": "Briefly describe your research, internships, or important projects."},
]


def map_intake_answer(field_key: str, answer: str) -> Dict[str, Any]:
    answer = str(answer or "").strip()

    if field_key == "gpa":
        profile_update = {"gpa_text": answer}
        numbers = re.findall(r"\d+(?:\.\d+)?", answer)
        if numbers:
            profile_update["gpa"] = float(numbers[0])
        if len(numbers) >= 2:
            profile_update["gpa_scale"] = str(numbers[1])
        return profile_update

    if field_key == "budget":
        return {"budget": extract_number(answer), "budget_text": answer}

    if field_key == "toefl":
        lower = answer.lower()
        if "ielts" in lower:
            return {"ielts": extract_number(answer), "toefl": None, "english_score_text": answer}
        if "not" in lower or "no" in lower or lower == "na":
            return {"toefl": None}
        return {"toefl": extract_number(answer)}

    if field_key == "gre_quant":
        lower = answer.lower()
        if "not" in lower or "no" in lower or lower == "na":
            return {"gre_quant": None}
        return {"gre_quant": extract_number(answer)}

    return {field_key: answer}


@api_view(["POST"])
@permission_classes(STUDENT_PERMISSIONS)
def profile_intake_chat(request):
    student_id = request.user.account.student_id
    answer = request.data.get("answer", "")

    key = make_student_id(student_id)
    session = load_intake_session(key)

    if session is None or answer == "":
        session = {"student_id": student_id, "step": 0, "answers": {}, "completed": False}
        save_intake_session(key, session)
        log_chat_turn(
            channel=ChatMessage.Channel.INTAKE,
            student_id=student_id,
            user_message=answer,
            assistant_message=INTAKE_FIELDS[0]["question"],
        )
        return Response({
            "completed": False,
            "next_question": INTAKE_FIELDS[0]["question"],
            "step": 0,
            "total_steps": len(INTAKE_FIELDS),
        })

    if session.get("completed"):
        return Response({
            "completed": True,
            "message": "Profile intake is already completed.",
            "profile": load_profile_data(student_id),
        })

    step = int(session.get("step", 0))

    if step >= len(INTAKE_FIELDS):
        return Response({
            "completed": True,
            "message": "Profile intake already completed.",
            "profile": load_profile_data(student_id),
        })

    field = INTAKE_FIELDS[step]
    session["answers"].update(map_intake_answer(field["key"], answer))
    session["step"] = step + 1

    if session["step"] >= len(INTAKE_FIELDS):
        session["completed"] = True
        save_intake_session(key, session)
        profile = load_profile_data(student_id)
        profile.update(session["answers"])
        save_profile_data(student_id, profile)
        log_chat_turn(
            channel=ChatMessage.Channel.INTAKE,
            student_id=student_id,
            user_message=answer,
            assistant_message="Profile intake completed.",
        )
        return Response({
            "completed": True,
            "message": "Profile intake completed.",
            "profile": profile,
        })

    save_intake_session(key, session)
    log_chat_turn(
        channel=ChatMessage.Channel.INTAKE,
        student_id=student_id,
        user_message=answer,
        assistant_message=INTAKE_FIELDS[session["step"]]["question"],
    )
    return Response({
        "completed": False,
        "next_question": INTAKE_FIELDS[session["step"]]["question"],
        "step": session["step"],
        "total_steps": len(INTAKE_FIELDS),
    })


# ---------------------------------------------------------------------
# Agent loading
# ---------------------------------------------------------------------

def get_aria_agent(student_id: str):
    key = make_student_id(student_id)
    if key not in ARIA_SESSIONS:
        from agents.student_agent import StudentAgent
        profile = load_profile_data(student_id)
        try:
            ARIA_SESSIONS[key] = StudentAgent(profile, student_id=key)
        except TypeError:
            ARIA_SESSIONS[key] = StudentAgent(profile, student_name=profile.get("name"), student_id=key)
    return ARIA_SESSIONS[key]


def get_university_agent(university_id: str):
    if university_id in UNIVERSITY_AGENTS:
        return UNIVERSITY_AGENTS[university_id]

    from agents.university_agent import UniversityAgent
    from personas.university_personas import UNIVERSITY_PERSONAS

    if university_id not in UNIVERSITY_PERSONAS:
        raise ValueError(f"Unknown university_id: {university_id}")

    auto_scrape = os.getenv("KORGUT_AUTO_SCRAPE", "false").lower() == "true"

    try:
        agent = UniversityAgent(university_id, auto_scrape=auto_scrape)
    except TypeError:
        agent = UniversityAgent(university_id)

    UNIVERSITY_AGENTS[university_id] = agent

    try:
        from agents import commons
        commons.register(university_id, agent)
    except Exception:
        pass

    return agent


# ---------------------------------------------------------------------
# API 7: Aria Chat
# ---------------------------------------------------------------------

@api_view(["POST"])
@permission_classes(STUDENT_PERMISSIONS)
def aria_chat(request):
    student_id = request.user.account.student_id
    message = request.data.get("message")

    if not message:
        return api_error("message is required.")

    try:
        aria = get_aria_agent(student_id)
        reply = aria.chat(message)
        if hasattr(aria, "student_profile"):
            save_profile_data(student_id, aria.student_profile)
        log_chat_turn(
            channel=ChatMessage.Channel.ARIA,
            student_id=student_id,
            user_message=message,
            assistant_message=reply or "",
        )
        return Response({"agent": "Aria", "student_id": student_id, "reply": reply})
    except Exception as exc:
        return api_error(f"Aria chat failed: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes(STUDENT_PERMISSIONS)
def aria_chat_history(request):
    student_id = request.user.account.student_id
    messages = ChatMessage.objects.filter(channel=ChatMessage.Channel.ARIA, student_id=student_id)
    return Response({
        "count": messages.count(),
        "messages": [
            {"sender": m.sender, "content": m.content, "created_at": m.created_at, "meta": m.meta}
            for m in messages
        ],
    })


# ---------------------------------------------------------------------
# API 8: University Chat
# ---------------------------------------------------------------------

@api_view(["POST"])
@permission_classes(STUDENT_PERMISSIONS)
def university_chat(request, university_id: str):
    student_id = request.user.account.student_id
    message = request.data.get("message")

    if not message:
        return api_error("message is required.")

    try:
        profile = load_profile_data(student_id)
        agent = get_university_agent(university_id)
        result = agent.answer(message, profile)
        agent_name = result.get("agent_name") or university_id
        pending_query = result.get("pending_query") or {}
        reply = result.get("answer")
        log_chat_turn(
            channel=ChatMessage.Channel.UNIVERSITY,
            student_id=student_id,
            university_id=university_id,
            user_message=message,
            assistant_message=reply or "",
            meta={
                "pending": result.get("pending", False),
                "query_id": result.get("query_id") or pending_query.get("query_id"),
                "confidence": result.get("confidence"),
            },
        )
        return Response({
            "agent": agent_name,
            "university": result.get("university"),
            "student_id": student_id,
            "reply": reply,
            "pending": result.get("pending", False),
            "query_id": result.get("query_id") or pending_query.get("query_id"),
            "confidence": result.get("confidence"),
        })
    except ValueError as exc:
        return api_error(str(exc), status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        return api_error(f"University chat failed: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes(STUDENT_PERMISSIONS)
def university_chat_history(request, university_id: str):
    student_id = request.user.account.student_id
    messages = ChatMessage.objects.filter(
        channel=ChatMessage.Channel.UNIVERSITY, student_id=student_id, university_id=university_id
    )
    return Response({
        "count": messages.count(),
        "messages": [
            {"sender": m.sender, "content": m.content, "created_at": m.created_at, "meta": m.meta}
            for m in messages
        ],
    })


# ---------------------------------------------------------------------
# API 9: Fit Assessment
# ---------------------------------------------------------------------

def _fallback_fit_assessment(profile: Dict[str, Any], university_id: str, agent: Optional[Any] = None) -> Dict[str, Any]:
    gpa = safe_number(profile.get("gpa"), 0)
    gpa_scale = safe_number(profile.get("gpa_scale"), 10)
    gre_quant = safe_number(profile.get("gre_quant"), 0)
    toefl = safe_number(profile.get("toefl"), 0)
    ielts = safe_number(profile.get("ielts"), 0)
    budget = safe_number(profile.get("budget"), 0)
    major = str(profile.get("major", "")).lower()
    program = str(profile.get("program", "")).lower()
    research = str(profile.get("research", "")).lower()

    score = 45
    strengths = []
    gaps = []
    gpa_percent = gpa / gpa_scale if gpa_scale > 0 else 0

    if gpa_percent >= 0.80:
        score += 15
        strengths.append("Strong academic profile based on GPA.")
    elif gpa_percent >= 0.70:
        score += 8
        strengths.append("Decent academic profile.")
    else:
        gaps.append("GPA may need stronger support through projects, GRE, or experience.")

    if "computer" in major or "cs" in major or "computer" in program or "science" in program or "software" in program:
        score += 12
        strengths.append("Academic background aligns with Computer Science.")
    else:
        gaps.append("Program background alignment should be explained clearly.")

    if gre_quant >= 165:
        score += 10
        strengths.append("Strong GRE Quant score.")
    elif gre_quant >= 160:
        score += 6
        strengths.append("Good GRE Quant score.")
    elif gre_quant > 0:
        gaps.append("GRE Quant score may be moderate for CS programs.")
    else:
        gaps.append("GRE score is missing or not provided.")

    if toefl >= 90:
        score += 8
        strengths.append("TOEFL score looks acceptable for many graduate programs.")
    elif ielts >= 6.5:
        score += 8
        strengths.append("IELTS score looks acceptable for many graduate programs.")
    elif toefl > 0 or ielts > 0:
        gaps.append("English proficiency score should be verified against the university minimum.")
    else:
        gaps.append("English proficiency score is missing.")

    if any(word in research for word in ["ai", "ml", "web", "project", "research", "internship"]):
        score += 8
        strengths.append("Projects/research experience supports the application.")
    else:
        gaps.append("More project or research detail would improve the profile.")

    if budget >= 40000:
        score += 7
        strengths.append("Budget appears reasonable for many US graduate options, but tuition must be verified.")
    elif budget >= 25000:
        score += 3
        gaps.append("Budget may need careful planning depending on tuition and living cost.")
    else:
        gaps.append("Budget may be tight for US graduate study.")

    if university_id == "wright_state_cs":
        university_name = "Wright State University — CS & Engineering"
        agent_name = "Raider"
    elif university_id == "franklin_cs":
        university_name = "Franklin University — M.S. Computer Science"
        agent_name = "Franklin"
        score += 3
    else:
        university_name = university_id
        agent_name = getattr(agent, "agent_name", university_id) if agent else university_id

    score = max(0, min(100, int(score)))

    if score >= 80:
        match_tier = "strong"
        recommendation = "recommend"
        realistic = True
    elif score >= 65:
        match_tier = "target"
        recommendation = "recommend"
        realistic = True
    elif score >= 50:
        match_tier = "possible"
        recommendation = "consider"
        realistic = True
    else:
        match_tier = "reach"
        recommendation = "consider"
        realistic = False

    return {
        "match_tier": match_tier,
        "match_score": score,
        "fit_summary": f"Based on the available profile, this looks like a {match_tier} fit for {university_name}.",
        "strengths_for_program": strengths or ["Basic profile information is available for assessment."],
        "gaps_for_program": gaps or ["Verify official requirements on the university website."],
        "recommendation": recommendation,
        "realistic": realistic,
        "specific_advice": "Verify tuition, deadlines, GRE/TOEFL, and funding before final decision.",
        "university": university_name,
        "agent": agent_name,
        "assessment_source": "api_fallback",
    }


def _assessment_failed(assessment: Any) -> bool:
    if not isinstance(assessment, dict):
        return True
    if assessment.get("match_tier") == "unknown":
        return True
    if int(assessment.get("match_score") or 0) <= 0:
        return True
    return False


@api_view(["POST"])
@permission_classes(STUDENT_PERMISSIONS)
def generate_fit_assessment(request, university_id: str):
    student_id = request.user.account.student_id
    force = str(request.query_params.get("force", "")).strip().lower() in ("1", "true", "yes")

    try:
        student_key = make_student_id(student_id)

        if not force:
            cached = (
                FitAssessment.objects.filter(student__student_id=student_key, university_id=university_id)
                .order_by("-created_at")
                .first()
            )
            if cached:
                response_data = dict(cached.assessment)
                response_data["cached"] = True
                response_data["generated_at"] = cached.created_at
                return Response(response_data, status=status.HTTP_200_OK)

        profile = load_profile_data(student_id)
        agent = get_university_agent(university_id)
        try:
            assessment = agent.assess_fit(profile)
        except Exception:
            assessment = None

        if _assessment_failed(assessment):
            assessment = _fallback_fit_assessment(profile, university_id, agent)

        profile.setdefault("assessments", {})
        profile["assessments"][university_id] = assessment
        save_profile_data(student_id, profile)

        row = FitAssessment.objects.create(
            student=StudentProfile.objects.get(student_id=student_key),
            university_id=university_id,
            assessment=assessment,
        )

        response_data = dict(assessment)
        response_data["cached"] = False
        response_data["generated_at"] = row.created_at

        return Response(response_data, status=status.HTTP_200_OK)
    except ValueError as exc:
        return api_error(str(exc), status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        return api_error(f"Fit assessment failed: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


class AssessmentHistoryView(APIView):
    """
    GET /api/assessments/<student_id>/
    Dual-mode: the owning student sees history across every university;
    a university officer sees only their own university's history for
    that student.
    """

    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsStudentOrUniversityRole]

    def get(self, request, student_id):
        account = get_account(request)

        if account.role == "student":
            if account.student_id != student_id:
                return api_error("You may only access your own assessment history.", status.HTTP_403_FORBIDDEN)
            rows = FitAssessment.objects.filter(student__student_id=student_id)
        else:
            rows = FitAssessment.objects.filter(student__student_id=student_id, university_id=account.university_id)

        return Response({
            "student_id": student_id,
            "count": rows.count(),
            "assessments": [
                {"university_id": r.university_id, "assessment": r.assessment, "created_at": r.created_at}
                for r in rows
            ],
        })


class AssessmentDetailView(APIView):
    """
    GET /api/assessments/<university_id>/<student_id>/
    Returns only the latest fit assessment for one university+student pair,
    without having to filter the full cross-university history client-side.
    """

    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsStudentOrUniversityRole]

    def get(self, request, university_id, student_id):
        account = get_account(request)

        if account.role == "student" and account.student_id != student_id:
            return api_error("You may only access your own assessment history.", status.HTTP_403_FORBIDDEN)
        if account.role != "student" and account.university_id != university_id:
            return api_error("You may only access your own university's assessment history.", status.HTTP_403_FORBIDDEN)

        row = (
            FitAssessment.objects.filter(student__student_id=student_id, university_id=university_id)
            .order_by("-created_at")
            .first()
        )

        if not row:
            return api_error(
                f"No fit assessment found for student_id={student_id}, university_id={university_id}.",
                status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "student_id": student_id,
            "university_id": university_id,
            "assessment": row.assessment,
            "created_at": row.created_at,
        })


# ---------------------------------------------------------------------
# API 10: Roadmap
# ---------------------------------------------------------------------

class RoadmapView(APIView):
    permission_classes = STUDENT_OWNER_PERMISSIONS

    def get(self, request, student_id):
        profile, error_response = load_profile_or_404(student_id)
        if error_response:
            return error_response

        user_message = request.query_params.get("message", "").strip()
        if not user_message:
            user_message = "Generate a personalized roadmap for this student's application process or exam preparation based on the saved profile."

        try:
            try:
                from roadmap.roadmap_planner import RoadmapPlanner
            except ImportError:
                from roadmap_planner import RoadmapPlanner

            planner = RoadmapPlanner()
            if hasattr(planner, "generate_application_roadmap"):
                roadmap = planner.generate_application_roadmap(profile, user_message)
            else:
                roadmap = call_first_available_method(
                    planner,
                    ["generate", "generate_roadmap", "create_roadmap", "build_roadmap", "plan"],
                    profile,
                    user_message,
                )

            if isinstance(roadmap, dict):
                profile["roadmap"] = roadmap
                save_profile_data(student_id, profile)
                RoadmapVersion.objects.create(
                    student=StudentProfile.objects.get(student_id=student_id),
                    request_message=user_message,
                    roadmap=roadmap,
                )

            return Response({"status": "success", "student_id": student_id, "request": user_message, "roadmap": roadmap})
        except ImportError as exc:
            return Response(
                {
                    "status": "failed",
                    "message": "Roadmap planner file not found.",
                    "error": str(exc),
                    "expected_file": "roadmap_planner.py or roadmap/roadmap_planner.py",
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except Exception as exc:
            return Response(
                {"status": "failed", "message": "Roadmap generation failed.", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RoadmapHistoryView(APIView):
    """GET /api/roadmap/<student_id>/history/ — every roadmap generated for this student."""

    permission_classes = STUDENT_OWNER_PERMISSIONS

    def get(self, request, student_id):
        rows = RoadmapVersion.objects.filter(student__student_id=student_id)
        return Response({
            "student_id": student_id,
            "count": rows.count(),
            "versions": [
                {"request_message": r.request_message, "roadmap": r.roadmap, "created_at": r.created_at}
                for r in rows
            ],
        })


# ---------------------------------------------------------------------
# Persistent GET APIs for profile sub-resources (resume/GitHub/LinkedIn history)
# ---------------------------------------------------------------------

class ResumeHistoryView(APIView):
    """GET /api/profile/<student_id>/resumes/ — every resume uploaded by this student."""

    permission_classes = STUDENT_OWNER_PERMISSIONS

    def get(self, request, student_id):
        rows = ResumeUpload.objects.filter(student__student_id=student_id)
        return Response({
            "student_id": student_id,
            "count": rows.count(),
            "resumes": [
                {
                    "id": r.id,
                    "original_filename": r.original_filename,
                    "resume_url": request.build_absolute_uri(f"/api/profile/resume/{r.id}/"),
                    "extracted_data": r.extracted_data,
                    "created_at": r.created_at,
                }
                for r in rows
            ],
        })


class GitHubHistoryView(APIView):
    """GET /api/profile/<student_id>/github-history/ — every GitHub analysis run for this student."""

    permission_classes = STUDENT_OWNER_PERMISSIONS

    def get(self, request, student_id):
        rows = GitHubAnalysis.objects.filter(student__student_id=student_id)
        return Response({
            "student_id": student_id,
            "count": rows.count(),
            "analyses": [
                {"github_url": r.github_url, "result": r.result, "created_at": r.created_at}
                for r in rows
            ],
        })


class LinkedInHistoryView(APIView):
    """GET /api/profile/<student_id>/linkedin-history/ — every LinkedIn analysis run for this student."""

    permission_classes = STUDENT_OWNER_PERMISSIONS

    def get(self, request, student_id):
        rows = LinkedInAnalysis.objects.filter(student__student_id=student_id)
        return Response({
            "student_id": student_id,
            "count": rows.count(),
            "analyses": [
                {
                    "id": r.id,
                    "images": build_linkedin_images_payload(request, r.id, r.image_paths),
                    "extracted": r.extracted,
                    "created_at": r.created_at,
                }
                for r in rows
            ],
        })


# ---------------------------------------------------------------------
# API 11: Pending Queries
# ---------------------------------------------------------------------

class PendingQueriesView(APIView):
    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsUniversityRole]

    def get(self, request):
        own_university_id = request.user.account.university_id
        rows = PendingQuery.objects.filter(university_id=own_university_id).exclude(
            status=PendingQuery.Status.RESOLVED
        )

        pending_queries = [
            {
                "id": r.id,
                "query_id": r.id,
                "student_id": r.student_id,
                "student_name": r.student_name,
                "university_id": r.university_id,
                "agent_name": r.agent_name,
                "program": r.program,
                "question": r.question,
                "priority": r.priority,
                "urgency_reason": r.urgency_reason,
                "status": r.status,
                "timestamp": r.created_at,
            }
            for r in rows
        ]

        return Response({"pending_queries": pending_queries, "count": len(pending_queries)})


# ---------------------------------------------------------------------
# API 12: Answer Pending Query
# ---------------------------------------------------------------------

class AnswerPendingQueryView(APIView):
    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsUniversityRole]

    def post(self, request):
        query_id = request.data.get("query_id")
        answer = request.data.get("answer")
        answered_by = request.data.get("answered_by", "Admin")

        if query_id is None:
            return Response({"status": "failed", "message": "query_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not answer:
            return Response({"status": "failed", "message": "answer is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            query_id_int = int(query_id)
        except ValueError:
            return Response({"status": "failed", "message": "query_id must be a number"}, status=status.HTTP_400_BAD_REQUEST)

        selected_query = PendingQuery.objects.filter(id=query_id_int).first()

        if not selected_query:
            return Response(
                {"status": "failed", "message": f"Pending query not found for query_id: {query_id_int}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if selected_query.status == PendingQuery.Status.RESOLVED:
            return Response(
                {"status": "failed", "message": f"Query {query_id_int} is already resolved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        university_id = selected_query.university_id
        if university_id and university_id != request.user.account.university_id:
            return Response(
                {"status": "failed", "message": "You may only answer queries for your own university."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not university_id:
            return Response(
                {"status": "failed", "message": "university_id is missing in pending query record."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from agents.university_agent import UniversityAgent
            agent = UniversityAgent(university_id=university_id, auto_scrape=False)
            resolved = agent.resolve_pending_query(query_id=query_id_int, answer=answer, answered_by=answered_by)
            if not resolved:
                return Response({"status": "failed", "message": "Could not resolve pending query."}, status=status.HTTP_400_BAD_REQUEST)
            return Response(
                {"status": "success", "message": "Saved to Knowledge Base", "query_id": query_id_int, "university_id": university_id},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            return Response(
                {"status": "failed", "message": "Failed to save human-verified answer.", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---------------------------------------------------------------------
# API 13: Export PDF
# ---------------------------------------------------------------------

class ExportProfilePDFView(APIView):
    permission_classes = STUDENT_OWNER_PERMISSIONS

    def get(self, request, student_id):
        profile_data, error_response = load_profile_or_404(student_id)
        if error_response:
            return error_response

        try:
            pdf_path = None
            try:
                from student_profile.student_profile import StudentProfile
                profile_obj = StudentProfile.load(student_id)
                if hasattr(profile_obj, "export_pdf"):
                    pdf_path = profile_obj.export_pdf()
            except Exception:
                pass

            if not pdf_path:
                try:
                    from agents.profile_presenter import ProfilePresenter
                    presenter = ProfilePresenter()
                    pdf_path = call_first_available_method(
                        presenter,
                        ["export_pdf", "generate_pdf", "create_pdf", "build_pdf", "render_pdf"],
                        profile_data,
                    )
                except ImportError:
                    pass

            if not pdf_path:
                return Response(
                    {
                        "status": "failed",
                        "message": "PDF export feature is not available. Expected StudentProfile.export_pdf() or agents/profile_presenter.py.",
                    },
                    status=status.HTTP_501_NOT_IMPLEMENTED,
                )

            pdf_path = Path(pdf_path)
            if not pdf_path.exists():
                return Response(
                    {"status": "failed", "message": "PDF was generated but file was not found.", "pdf_path": str(pdf_path)},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return FileResponse(open(pdf_path, "rb"), as_attachment=True, filename=pdf_path.name, content_type="application/pdf")
        except Exception as exc:
            return Response(
                {"status": "failed", "message": "PDF export failed.", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---------------------------------------------------------------------
# University Dashboard APIs
#
# Ported from the legacy standalone FastAPI admin service that used to live
# at api/university_interface.py (removed — superseded by these routes).
# ---------------------------------------------------------------------

def get_profile_presenter(university_id: str):
    if university_id not in PROFILE_PRESENTERS:
        from agents.profile_presenter import ProfilePresenterAgent
        PROFILE_PRESENTERS[university_id] = ProfilePresenterAgent(university_id)
    return PROFILE_PRESENTERS[university_id]


def serialize_pending_query(query: "PendingQuery") -> Dict[str, Any]:
    return {
        "id": query.id,
        "query_id": query.id,
        "university_id": query.university_id,
        "university": query.university_name,
        "agent_name": query.agent_name,
        "student_id": query.student_id,
        "student_name": query.student_name,
        "program": query.program,
        "question": query.question,
        "status": query.status,
        "priority": query.priority,
        "urgency_reason": query.urgency_reason,
        "display_status": query.display_status,
        "escalation_chain": query.escalation_chain,
        "answer": query.answer,
        "answered_by": query.answered_by,
        "answered_at": query.answered_at,
        "timestamp": query.created_at,
    }


class UniversityProfilesListView(APIView):
    """
    GET /api/university/<university_id>/profiles/
    Dashboard listing of every student profile, with that university's fit
    assessment (if any) flattened in. Reads from the StudentProfile table.
    """

    permission_classes = UNIVERSITY_OWNER_PERMISSIONS

    def get(self, request, university_id: str):
        profiles = []

        for row in StudentProfile.objects.all():
            data = load_profile_data(row.student_id)
            assessment = (data.get("assessments") or {}).get(university_id, {})

            profiles.append({
                "profile_id": data.get("student_id"),
                "name": data.get("name"),
                "profile_image_url": (
                    request.build_absolute_uri(f"/api/profile/{row.student_id}/image/")
                    if row.profile_image_path
                    else None
                ),
                "institution": data.get("institution"),
                "major": data.get("major"),
                "gpa": data.get("gpa"),
                "gpa_scale": data.get("gpa_scale"),
                "gre_quant": data.get("gre_quant"),
                "toefl": data.get("toefl"),
                "budget": data.get("budget"),
                "work_months": data.get("work_months"),
                "academic_intelligence": data.get("academic_intelligence", {}),
                "technical_intelligence": data.get("technical_intelligence", {}),
                "research_intelligence": data.get("research_intelligence", {}),
                "behaviour_intelligence": data.get("behaviour_intelligence", {}),
                "overall_profile_score": data.get("overall_profile_score"),
                "overall_profile": data.get("overall_profile", {}),
                "profile_completeness": data.get("profile_completeness"),
                "strengths": data.get("strengths", []),
                "weaknesses": data.get("weaknesses", []),
                "recommendations": data.get("recommendations", []),
                "ai_summary": data.get("ai_summary", ""),
                "summary": data.get("summary", ""),
                "skills": data.get("skills", []),
                "technical_skills": data.get("technical_skills", []),
                "projects": data.get("projects", []),
                "research": data.get("research"),
                "research_interests": data.get("research_interests", []),
                "publications": data.get("publications", []),
                "match_tier": assessment.get("match_tier", "unassessed"),
                "match_score": assessment.get("match_score"),
                "fit_summary": assessment.get("fit_summary", data.get("summary", "")),
                "recommendation": assessment.get("recommendation", "review"),
            })

        return Response({"university_id": university_id, "profiles": profiles})


@api_view(["POST"])
@permission_classes(UNIVERSITY_OWNER_PERMISSIONS)
def university_profile_presenter_chat(request, university_id: str, student_id: str):
    """
    POST /api/university/<university_id>/profile/<student_id>/chat/
    University-officer-facing chat about one student (ProfilePresenterAgent) —
    distinct from /api/chat/aria/, which is the student-facing agent.
    student_id is intentionally unrestricted here (any student in the
    university's own dashboard may be asked about) — only university_id
    is scoped, via ScopedToOwnUniversityId in UNIVERSITY_OWNER_PERMISSIONS.
    """
    question = request.data.get("question")
    history = request.data.get("history", []) or []

    if not question:
        return api_error("question is required.")

    try:
        profile = get_profile(student_id)
    except FileNotFoundError:
        return Response({"answer": "Profile not found."})

    try:
        presenter = get_profile_presenter(university_id)
        answer = presenter.answer(question=question, profile=profile, conversation_history=history)
        log_chat_turn(
            channel=ChatMessage.Channel.PRESENTER,
            student_id=student_id,
            university_id=university_id,
            user_message=question,
            assistant_message=answer or "",
        )
        return Response({"answer": answer})
    except Exception as exc:
        return Response({
            "answer": "Profile Presenter failed.",
            "error": (
                "AI profile explanation failed. Check API key, model name, "
                f"credits, network, or profile data. Details: {exc}"
            ),
        })


@api_view(["GET"])
@permission_classes(UNIVERSITY_OWNER_PERMISSIONS)
def university_profile_presenter_chat_history(request, university_id: str, student_id: str):
    messages = ChatMessage.objects.filter(
        channel=ChatMessage.Channel.PRESENTER, student_id=student_id, university_id=university_id
    )
    return Response({
        "count": messages.count(),
        "messages": [
            {"sender": m.sender, "content": m.content, "created_at": m.created_at, "meta": m.meta}
            for m in messages
        ],
    })


class UniversityQuestionsView(APIView):
    """GET /api/university/<university_id>/questions/ — officer question log."""

    permission_classes = UNIVERSITY_OWNER_PERMISSIONS

    def get(self, request, university_id: str):
        rows = UniversityQuestionLog.objects.filter(university_id=university_id)
        questions = [
            {
                "university_id": r.university_id,
                "student_name": r.student_name,
                "question": r.question,
                "topic": r.topic,
                "created_at": r.created_at,
            }
            for r in rows
        ]
        return Response({"university_id": university_id, "questions": questions})


class UniversityQueriesView(APIView):
    """GET /api/university/<university_id>/queries/ — all escalated queries for one university."""

    permission_classes = UNIVERSITY_OWNER_PERMISSIONS

    def get(self, request, university_id: str):
        rows = PendingQuery.objects.filter(university_id=university_id)
        matched = [serialize_pending_query(r) for r in rows]
        return Response({"university_id": university_id, "queries": matched})


class UniversityActiveQueriesView(APIView):
    """GET /api/university/<university_id>/queries/active/ — pending + urgent only."""

    permission_classes = UNIVERSITY_OWNER_PERMISSIONS

    def get(self, request, university_id: str):
        rows = PendingQuery.objects.filter(university_id=university_id).exclude(status=PendingQuery.Status.RESOLVED)
        active = [serialize_pending_query(r) for r in rows]
        return Response({"university_id": university_id, "queries": active})


class UniversityArchiveQueriesView(APIView):
    """GET /api/university/<university_id>/queries/archive/ — resolved/answered only."""

    permission_classes = UNIVERSITY_OWNER_PERMISSIONS

    def get(self, request, university_id: str):
        rows = PendingQuery.objects.filter(university_id=university_id, status=PendingQuery.Status.RESOLVED)
        archive = [serialize_pending_query(r) for r in rows]
        return Response({"university_id": university_id, "queries": archive})


class VerifiedKnowledgeView(APIView):
    """GET /api/university/<university_id>/knowledge/verified/ — durable human-verified answers."""

    permission_classes = UNIVERSITY_OWNER_PERMISSIONS

    def get(self, request, university_id: str):
        rows = VerifiedAnswer.objects.filter(university_id=university_id)
        matched = [
            {
                "query_id": r.query_id,
                "university_id": r.university_id,
                "question": r.question,
                "answer": r.answer,
                "answered_by": r.answered_by,
                "source": r.source,
                "source_type": "human_verified",
                "confidence": r.confidence,
                "synced_at": r.created_at,
            }
            for r in rows
        ]

        return Response({"university_id": university_id, "verified_answers": matched})


class EditPendingQueryView(APIView):
    """
    POST /api/queries/<query_id>/edit/
    Updates the answer on a query, including one that's already resolved —
    unlike /api/queries/answer/, which refuses to touch an already-resolved query.
    """

    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsUniversityRole]

    def post(self, request, query_id: int):
        answer = request.data.get("answer")
        answered_by = request.data.get("answered_by", "Admin")

        if not answer:
            return Response({"status": "failed", "message": "answer is required"}, status=status.HTTP_400_BAD_REQUEST)

        selected_query = PendingQuery.objects.filter(id=query_id).first()

        if not selected_query:
            return Response(
                {"status": "failed", "message": f"Query not found for query_id: {query_id}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        university_id = selected_query.university_id

        if university_id and university_id != request.user.account.university_id:
            return Response(
                {"status": "failed", "message": "You may only edit queries for your own university."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not university_id:
            return Response(
                {"status": "failed", "message": "university_id is missing in query record."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from agents.university_agent import UniversityAgent
            agent = UniversityAgent(university_id=university_id, auto_scrape=False)
            resolved = agent.resolve_pending_query(query_id=query_id, answer=answer, answered_by=answered_by)
            if not resolved:
                return Response({"status": "failed", "message": "Could not edit query."}, status=status.HTTP_400_BAD_REQUEST)
            return Response(
                {"status": "success", "message": "Query answer updated", "query_id": query_id, "university_id": university_id},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            return Response(
                {"status": "failed", "message": "Failed to update query answer.", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
