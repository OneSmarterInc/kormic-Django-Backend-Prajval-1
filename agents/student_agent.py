# agents/student_agent.py
# Aria — The Student Advocate
# Final integrated version:
# - Persistent student memory
# - Auto profile save
# - Conversation logging
# - Conversation summary
# - Admission probability estimate
# - University comparison
# - Export chat report
# - Response modes
# - GitHub Profile Intelligence
# - Raider trust-context support

from __future__ import annotations

from datetime import datetime
import json
import os
import re
from typing import Any, Dict, Optional

import anthropic
from agents import commons
from personas.aria_constitution import build_agent_system_prompt
from personas.university_personas import UNIVERSITY_PERSONAS
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

try:
    from profile_intelligence.profile_intelligence import ProfileIntelligenceService
except Exception:
    ProfileIntelligenceService = None


try:
    from roadmap.roadmap_planner import RoadmapPlanner
except Exception:
    RoadmapPlanner = None


console = Console()
client = anthropic.Anthropic()


class StudentAgent:
    """
    Aria — the student advocate in the Korgut Commons.

    Responsibilities:
    - Maintain chat history during the session.
    - Persist important student memory across sessions.
    - Save the student profile automatically.
    - Analyze public GitHub profiles when shared.
    - Query university agents through Commons when verified data is needed.
    - Export a clean student advising report.
    """

    REPORT_DIR = "reports"

    VALID_RESPONSE_MODES = {"short", "detailed", "summary"}

    def __init__(
        self,
        student_profile: dict,
        profile=None,
        student_name: Optional[str] = None,
        student_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ):
        """Create the student's personal agent.

        Args:
            student_profile: Normal dict used by the agent's prompt.
            profile: Optional StudentProfile object that persists insights,
                university assessments, and summary to disk.
            student_name: Optional explicit name fallback.
            student_id: Canonical student_id (as used by StudentProfile/ChatMessage)
                used to key the agent's persistent DB memory. Falls back to a
                name-derived key when not supplied.
            agent_name: This student's chosen (or auto-assigned) display name
                for their personal agent. Defaults to "Aria" only as a last
                resort -- callers going through agents.commons.get_student_agent()
                always pass the student's actual agent_name.
        """
        self.profile = profile
        self.agent_name = agent_name or "Aria"
        self.roadmap_planner = (
            RoadmapPlanner()
            if RoadmapPlanner is not None
            else None
        )

        if self.profile is not None and hasattr(self.profile, "data"):
            # Use the persistent StudentProfile data as the single source of truth.
            self.student_profile = self.profile.data or (student_profile or {})
        else:
            self.student_profile = student_profile or {}

        if student_name and not self.student_profile.get("name"):
            self.student_profile["name"] = student_name

        self.student_name = self.student_profile.get("name", "there")
        self.student_key = self._safe_name(self.student_name)
        self.canonical_student_id = student_id or self.student_key

        self.conversation_history = []
        self.messages_exchanged = 0
        self.response_mode = self.student_profile.get("response_mode", "detailed")

        if self.response_mode not in self.VALID_RESPONSE_MODES:
            self.response_mode = "detailed"

        self.memory: Dict[str, Any] = {}
        self.load_memory()

        # Tracks a verification item this agent just surfaced in chat and is
        # waiting on a confirm/ignore/clarify reply for. Process-local, same
        # lifecycle as the rest of this session's in-memory state.
        self._pending_verification_item_id: Optional[int] = None

        self.system_prompt = build_agent_system_prompt(self.student_profile, self.agent_name)

        self.profile_intelligence = (
            ProfileIntelligenceService()
            if ProfileIntelligenceService is not None
            else None
        )

        self.save_student_profile()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _safe_name(self, value: str) -> str:
        value = str(value or "student").strip().lower()
        value = re.sub(r"[^a-z0-9]+", "_", value)
        return value.strip("_") or "student"

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value in [None, ""]:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value in [None, ""]:
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _ensure_dirs(self):
        os.makedirs(self.REPORT_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Student profile persistence
    # ------------------------------------------------------------------
    def save_student_profile(self) -> str:
        """Save through StudentProfile when available.

        Under the Django API, the caller (django_api.views.aria_chat) persists
        self.student_profile to the StudentProfile DB row via
        services.save_profile_data() right after chat() returns, so there is
        nothing to do here in that case.
        """
        self.student_profile["response_mode"] = self.response_mode

        if self.profile is not None and hasattr(self.profile, "data"):
            self.profile.data.update(self.student_profile)
            saved_path = self.profile.save()
            return str(saved_path)

        return f"db://student_profiles/{self.canonical_student_id}"

    # ------------------------------------------------------------------
    # Persistent memory
    # ------------------------------------------------------------------
    def load_memory(self):
        from django_api.models import AriaMemory

        record, _ = AriaMemory.objects.get_or_create(student_id=self.canonical_student_id)

        self.memory = {
            "student": self.student_name,
            "important_points": list(record.important_points or []),
            "universities_discussed": list(record.universities_discussed or []),
            "github_profiles_analyzed": list(record.github_profiles_analyzed or []),
            "last_updated": record.updated_at.strftime("%Y-%m-%d %H:%M:%S") if record.updated_at else None,
        }

    def save_memory(self):
        from django_api.models import AriaMemory

        AriaMemory.objects.update_or_create(
            student_id=self.canonical_student_id,
            defaults={
                "important_points": self.memory.get("important_points", [])[-50:],
                "universities_discussed": self.memory.get("universities_discussed", []),
                "github_profiles_analyzed": self.memory.get("github_profiles_analyzed", []),
            },
        )
        self.memory["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def update_memory(self, user_message: str, aria_response: str):
        text = user_message.lower()

        important_keywords = [
            "gpa", "budget", "cmu", "mit", "wright state", "funding",
            "sop", "gre", "toefl", "ielts", "research", "work experience",
            "github", "linkedin", "ai", "ml", "data science", "cybersecurity",
            "software engineering", "deadline", "scholarship"
        ]

        if any(keyword in text for keyword in important_keywords):
            point = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user": user_message,
                "aria": aria_response[:500],
            }

            self.memory["important_points"].append(point)
            self.memory["important_points"] = self.memory["important_points"][-50:]

        university_aliases = {
            "cmu": "CMU",
            "carnegie mellon": "CMU",
            "mit": "MIT",
            "wright state": "Wright State",
            "msu": "Michigan State",
            "michigan state": "Michigan State",
            "uw": "University of Washington",
            "university of washington": "University of Washington",
            "rutgers": "Rutgers",
            "sdsu": "San Diego State",
            "franklin": "Franklin University",
            "franklin university": "Franklin University",
        }

        for alias, canonical_name in university_aliases.items():
            if alias in text and canonical_name not in self.memory["universities_discussed"]:
                self.memory["universities_discussed"].append(canonical_name)

        self.save_memory()

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------
    def _memory_context(self) -> str:
        recent_points = self.memory.get("important_points", [])[-5:]
        universities = self.memory.get("universities_discussed", [])
        github_info = self.student_profile.get("github_profile_intelligence")

        lines = ["\n\nPERSISTENT STUDENT MEMORY:"]

        if universities:
            lines.append("Universities discussed: " + ", ".join(universities))

        if recent_points:
            lines.append("Recent important points:")
            for point in recent_points:
                lines.append(f"- Student said: {point.get('user')}")
        else:
            lines.append("No important memory points stored yet.")

        if github_info:
            lines.append("\nGitHub Profile Intelligence:")
            lines.append(
                f"- Primary direction: {github_info.get('primary_direction', 'Unknown')}"
            )
            lines.append(
                f"- Summary: {github_info.get('human_summary', 'Not available')}"
            )

        return "\n".join(lines)

    def _response_mode_instruction(self) -> str:
        if self.response_mode == "short":
            return """

RESPONSE STYLE FOR THIS TURN:
Give a very short answer.
Maximum 1-2 sentences.
Answer directly without long explanations.
"""

        if self.response_mode == "summary":
            return """

RESPONSE STYLE FOR THIS TURN:
Give a normal explanation.

At the end add:

Summary:
• Point 1
• Point 2
• Point 3
"""

        return """

RESPONSE STYLE FOR THIS TURN:
Give a helpful detailed response with reasoning, but avoid unnecessary over-explaining.
Use short paragraphs.
"""

    def _runtime_system_prompt(self) -> str:
        return (
            self.system_prompt
            + self._memory_context()
            + self._response_mode_instruction()
        )

    def _is_fit_assessment_question(self, lower_msg: str) -> bool:
        """Return True when the student asks for personalized university fit data."""
        fit_keywords = [
            "match score", "match tier", "fit", "good fit", "profile",
            "think about me", "think about my profile", "strength", "gap",
            "improve", "recommendation", "should i apply", "should i choose",
            "am i suitable", "am i eligible", "chances", "chance",
        ]
        return (
            any(keyword in lower_msg for keyword in fit_keywords)
            and bool(commons.match_university_ids(lower_msg))
        )

    def _generate_fit_assessment_if_needed(self, university_id: str) -> Optional[dict]:
        """Lazily generate a fit assessment only when the student asks for it,
        via the orchestrator (agents.commons) -- the university agent never
        talks to the student directly. If the assessment already exists,
        reuse it without another Anthropic call.
        """
        assessments = self.student_profile.setdefault("assessments", {})
        existing = assessments.get(university_id)
        if existing:
            return existing

        try:
            console.print(
                f"[yellow]No saved fit assessment found for {university_id}. "
                "Generating it now...[/yellow]"
            )

            assessment = commons.generate_fit_assessment(self.canonical_student_id, university_id)
            assessments[university_id] = assessment

            console.print(
                f"[green]Fit assessment generated and saved for {university_id}.[/green]"
            )
            return assessment

        except Exception as exc:
            console.print(f"[yellow]Could not generate fit assessment for {university_id}.[/yellow]")
            console.print(f"[dim]{exc}[/dim]")
            return None

    def _is_broad_fit_question(self, lower_msg: str) -> bool:
        """Return True for a fit/comparison question that doesn't name one
        specific university -- e.g. 'which universities fit my profile?'."""
        broad_keywords = [
            "which universit", "where do i fit", "best fit for me",
            "university recommendation", "which school", "compare universities",
            "what are my options", "which program fits", "where should i apply",
        ]
        return any(keyword in lower_msg for keyword in broad_keywords)

    def _broad_fit_response(self, user_message: str) -> Optional[str]:
        """Fan out to every university agent in the background, generating
        or reusing each fit assessment, then synthesise one comparison
        answer. The student never sees the individual university agents."""
        university_ids = commons.list_university_ids()
        assessments = self.student_profile.setdefault("assessments", {})
        summary_lines = []

        for university_id in university_ids:
            assessment = assessments.get(university_id)
            if not assessment:
                try:
                    assessment = commons.generate_fit_assessment(self.canonical_student_id, university_id)
                    assessments[university_id] = assessment
                except Exception as exc:
                    console.print(f"[yellow]Fit assessment failed for {university_id}: {exc}[/yellow]")
                    continue

            summary_lines.append(
                f"- {assessment.get('university', university_id)}: "
                f"{assessment.get('match_tier', 'unknown')} fit "
                f"(score {assessment.get('match_score', 'n/a')}) -- {assessment.get('fit_summary', '')}"
            )

        if not summary_lines:
            return None

        prompt = (
            "You are the student's personal advising agent. You just checked in the "
            "background with every university program's agent and got back these fit "
            "assessments. Give the student one clear, personalised answer comparing "
            "their options -- be direct about which look strongest and why. Do not "
            "mention that you 'queried agents'; speak as the one advisor who checked.\n\n"
            + "\n".join(summary_lines)
            + f"\n\nSTUDENT QUESTION:\n{user_message}"
        )

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                system=self._runtime_system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as exc:
            console.print(f"[yellow]Broad fit synthesis failed: {exc}[/yellow]")
            return "\n".join(summary_lines)

    def _saved_assessment_response(self, user_message: str) -> Optional[str]:
        """Answer simple questions from stored fit assessments without another LLM call."""
        lower_msg = user_message.lower()
        assessments = self.student_profile.get("assessments", {}) or {}

        # A broad "which university fits me" question (no university named)
        # must always go to the multi-university fan-out in chat(), never to
        # a single cached assessment picked just because it's the only one
        # that happens to exist yet.
        if self._is_broad_fit_question(lower_msg) and not commons.match_university_ids(user_message):
            return None

        university_map = {
            university_id: {
                "keywords": persona.get("keywords", []),
                "display": persona.get("name", university_id),
            }
            for university_id, persona in UNIVERSITY_PERSONAS.items()
        }

        selected_id = None
        selected_meta = None

        for university_id, meta in university_map.items():
            if any(keyword in lower_msg for keyword in meta["keywords"]):
                selected_id = university_id
                selected_meta = meta
                break

        if selected_id is None:
            # Only fall back to "the one assessment that exists" for messages
            # that are unambiguously asking for a university verdict, not
            # generic profile words. "strength"/"gap"/"fit"/"profile" alone
            # are deliberately excluded here -- they're just as likely to be
            # general profile questions ("what are my strengths and
            # weaknesses?") as university-fit ones, and when no university is
            # named there's no way to tell which the student meant. Those
            # general-sounding questions now correctly fall through to
            # _student_profile_response / the general LLM call instead of
            # being answered from one arbitrarily-cached university verdict.
            decisive_fit_keywords = [
                "match score", "match tier", "think about me", "think about my profile",
                "recommendation", "should i apply", "should i choose",
                "am i suitable", "am i eligible", "chances", "chance",
            ]
            is_fit_ish = any(keyword in lower_msg for keyword in decisive_fit_keywords)

            if len(assessments) == 1 and is_fit_ish:
                selected_id = next(iter(assessments.keys()))
                selected_meta = university_map.get(selected_id, {"display": selected_id})
            else:
                return None

        assessment = assessments.get(selected_id)

        if not assessment and self._is_fit_assessment_question(lower_msg):
            assessment = self._generate_fit_assessment_if_needed(selected_id)
            assessments = self.student_profile.get("assessments", {}) or {}

        if not assessment:
            return None

        display = selected_meta.get("display", selected_id) if selected_meta else selected_id

        if "match score" in lower_msg:
            return f"Your {display} match score is {assessment.get('match_score', 'not available')}."

        if "match tier" in lower_msg:
            return f"Your {display} match tier is {assessment.get('match_tier', 'not available')}."

        if "strength" in lower_msg:
            strengths = assessment.get("strengths_for_program", []) or []
            if strengths:
                return f"Your main strengths for {display} are:\n" + "\n".join(f"- {item}" for item in strengths)

        if "gap" in lower_msg or "improve" in lower_msg:
            gaps = assessment.get("gaps_for_program", []) or []
            advice = assessment.get("specific_advice", "")
            if gaps or advice:
                response = f"For {display}, the main gaps to work on are:\n"
                if gaps:
                    response += "\n".join(f"- {item}" for item in gaps)
                if advice:
                    response += f"\n\nSpecific advice: {advice}"
                return response

        if (
            f"what does {display.lower()} think" in lower_msg
            or "what does franklin think" in lower_msg
            or "what does wright state think" in lower_msg
            or "think about my profile" in lower_msg
            or "think about me" in lower_msg
            or "profile" in lower_msg
            or "fit" in lower_msg
            or "recommendation" in lower_msg
            or "should i apply" in lower_msg
        ):
            summary = assessment.get("fit_summary", "No fit summary is available yet.")
            recommendation = assessment.get("recommendation")
            tier = assessment.get("match_tier")
            score = assessment.get("match_score")

            response = summary
            if tier or score is not None:
                response += f"\n\nMatch: {tier or 'not available'}"
                if score is not None:
                    response += f" | Score: {score}"
            if recommendation:
                response += f"\nRecommendation: {recommendation}"
            return response

        return None

    # ------------------------------------------------------------------
    # Commands / utility outputs
    # ------------------------------------------------------------------
    def _student_profile_response(self, user_message: str) -> Optional[str]:
        """Answer direct questions about the saved student profile."""
        lower = user_message.lower()

        academic = self.student_profile.get("academic_intelligence", {}) or {}
        technical = self.student_profile.get("technical_intelligence", {}) or {}
        overall = self.student_profile.get("overall_profile", {}) or {}

        if (
            "show me my profile" in lower
            or "student profile" in lower
            or "show profile" in lower
            or lower.strip() in {"my profile", "profile"}
        ):
            return f"""
### Student Profile

| Field | Value |
|---|---|
| Name | {self.student_profile.get("name", "Not provided")} |
| Program | {self.student_profile.get("program", "Not provided")} |
| Institution | {self.student_profile.get("institution", "Not provided")} |
| Major | {self.student_profile.get("major", "Not provided")} |
| GPA | {self.student_profile.get("gpa", "Not provided")} |
| GRE Quant | {self.student_profile.get("gre_quant", "Not provided")} |
| TOEFL | {self.student_profile.get("toefl", "Not provided")} |
| Budget | ${self.student_profile.get("budget", "Not provided")} |

**Research / Notes**

{self.student_profile.get("research", "Not provided")}

**Academic Score:** {academic.get("academic_score", "Not available")}  
**Readiness:** {academic.get("readiness", "Not available")}  
**Technical Score:** {technical.get("technical_score", "Not available")}  
**Technical Level:** {technical.get("technical_level", "Not available")}  
**Overall Score:** {overall.get("overall_score", self.student_profile.get("overall_profile_score", "Not available"))}
"""

        if (
            "academically prepared" in lower
            or "academic profile" in lower
            or "academic strength" in lower
            or "academic weakness" in lower
        ):
            strengths = "\n".join(f"• {item}" for item in academic.get("strengths", []) or [])
            weaknesses = "\n".join(f"• {item}" for item in academic.get("weaknesses", []) or [])

            return f"""
### Academic Profile

**Academic Score:** {academic.get("academic_score", "Not available")}/100  
**Readiness:** {academic.get("readiness", "Not available")}

**Strengths**
{strengths or "• No academic strengths stored yet."}

**Weaknesses / Gaps**
{weaknesses or "• No academic weaknesses stored yet."}
"""

        if (
            "technical profile" in lower
            or "technical strength" in lower
            or "technical weakness" in lower
            or "technical skills" in lower
        ):
            strengths = "\n".join(f"• {item}" for item in technical.get("strengths", []) or [])
            weaknesses = "\n".join(f"• {item}" for item in technical.get("weaknesses", []) or [])

            return f"""
### Technical Profile

**Technical Score:** {technical.get("technical_score", "Not available")}/100  
**Technical Level:** {technical.get("technical_level", "Not available")}

**Strengths**
{strengths or "• No technical strengths stored yet."}

**Weaknesses / Gaps**
{weaknesses or "• No technical weaknesses stored yet."}
"""

        if (
            "overall profile" in lower
            or "overall score" in lower
            or "overall performance" in lower
        ):
            return f"""
### Overall Profile

**Overall Score:** {overall.get("overall_score", self.student_profile.get("overall_profile_score", "Not available"))}/100
"""

        return None

    def conversation_summary(self) -> str:
        universities = ", ".join(
            self.memory.get("universities_discussed", [])
        ) or "None yet"

        github_info = self.student_profile.get("github_profile_intelligence", {})
        github_direction = github_info.get("primary_direction", "Not analyzed yet")

        return f"""
### Conversation Summary

**Student:** {self.student_name}

**GPA:** {self.student_profile.get("gpa", "Not provided")}

**GRE Quant:** {self.student_profile.get("gre_quant", "Not provided")}

**TOEFL:** {self.student_profile.get("toefl", "Not provided")}

**Budget:** ${self.student_profile.get("budget", "Not provided")}

**Universities Discussed:** {universities}

**GitHub Direction:** {github_direction}

**Messages Exchanged:** {self.messages_exchanged}
"""

    def admission_probability(self) -> str:
        gpa = self._safe_float(self.student_profile.get("gpa"), 0.0)
        gre = self._safe_int(self.student_profile.get("gre_quant"), 0)
        budget = self._safe_int(self.student_profile.get("budget"), 0)

        if gpa >= 3.7 and gre >= 167:
            cmu = "Reach"
        elif gpa >= 3.3 and gre >= 160:
            cmu = "High Reach"
        else:
            cmu = "Very High Reach"

        if gpa >= 3.2 and gre >= 158:
            wright = "Realistic"
        elif gpa >= 3.0:
            wright = "Moderate"
        else:
            wright = "Reach"

        if gpa >= 3.35 and gre >= 160:
            msu = "Moderate"
        else:
            msu = "Reach"

        if gpa >= 3.7 and gre >= 165:
            uw = "Reach"
        else:
            uw = "High Reach"

        # Franklin is evaluated differently because it is a flexible, career-focused MSCS pathway,
        # not a prestige/research-driven target in the same category as CMU/UW.
        if gpa >= 3.0:
            franklin = "Realistic / Practical Fit"
        elif gpa >= 2.7:
            franklin = "Possible, verify requirements"
        else:
            franklin = "Needs review"

        cmu_note = (
            "Cost is a major issue for CMU based on the current budget."
            if budget and budget < 40000
            else "Budget should still be reviewed carefully for CMU."
        )

        github_info = self.student_profile.get("github_profile_intelligence", {})
        github_note = ""
        if github_info:
            github_note = (
                f"\n**Profile signal:** GitHub analysis currently points toward "
                f"**{github_info.get('primary_direction', 'Unknown')}**, which should be used "
                f"to choose better-fit tracks and electives."
            )

        return f"""
### Admission Fit Estimate

| University | Fit / Chance Category |
|---|---|
| CMU | {cmu} |
| Wright State | {wright} |
| Franklin University | {franklin} |
| Michigan State | {msu} |
| University of Washington | {uw} |

**Note:** {cmu_note}
{github_note}

This is a rough advising estimate, not an official admission prediction. Exact admission chances require verified historical admission data and current program-specific criteria.
"""

    def university_comparison(self) -> str:
        github_info = self.student_profile.get("github_profile_intelligence", {})
        github_direction = github_info.get("primary_direction")

        github_row = ""
        if github_direction:
            github_row = (
                f"| Student GitHub Fit | Good if applied {github_direction} work connects to AFRL/research areas | "
                f"Good if {github_direction} connects to software systems, data analytics, or cybersecurity |\n"
            )

        return f"""
### Wright State vs Franklin University

| Factor | Wright State | Franklin University |
|---|---|---|
| Core identity | Traditional public university CS/CSE option | Flexible, online, career-focused MSCS pathway |
| Best fit | Students wanting campus-based university experience, applied research, AFRL/Dayton connection | Students wanting practical software skills, online flexibility, working-adult format, or non-CS pathway |
| Research orientation | Stronger fit for applied research and regional lab/defense ecosystem | Better framed as professional/career advancement rather than research-heavy CS |
| Flexibility | Traditional university structure | 100% online program family with focus areas |
| Focus areas | CS/CSE, AI, cybersecurity, systems, HCI, applied research | Cybersecurity, Data Analytics, Software Systems, general MSCS |
| Budget lens | Public university value and possible assistantships, verify details | Per-credit tuition model; verify latest cost and partner discounts |
| Prestige lens | Not top-10 but more traditional university signal | Not a prestige/research play; practical career pathway |
{github_row}

**Simple advice:** Wright State is better if the student wants a traditional university environment, applied research, and regional opportunities around Dayton/AFRL. Franklin is better if the student values flexibility, online study, practical software/system skills, or a bridge into CS from a non-traditional background.
"""

    def export_report(self) -> str:
        self._ensure_dirs()

        filename = os.path.join(
            self.REPORT_DIR,
            f"{self.student_key}_chat_report.txt"
        )

        with open(filename, "w", encoding="utf-8") as f:
            f.write("KORGUT ARIA CHAT REPORT\n")
            f.write("=" * 60 + "\n")
            f.write(f"Student: {self.student_name}\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            f.write("STUDENT PROFILE\n")
            f.write("-" * 60 + "\n")
            f.write(json.dumps(self.student_profile, indent=4, ensure_ascii=False))

            f.write("\n\nPERSISTENT MEMORY\n")
            f.write("-" * 60 + "\n")
            f.write(json.dumps(self.memory, indent=4, ensure_ascii=False))

            f.write("\n\nCONVERSATION SUMMARY\n")
            f.write("-" * 60 + "\n")
            f.write(self.conversation_summary())

            f.write("\n\nADMISSION FIT ESTIMATE\n")
            f.write("-" * 60 + "\n")
            f.write(self.admission_probability())

            f.write("\n\nUNIVERSITY COMPARISON\n")
            f.write("-" * 60 + "\n")
            f.write(self.university_comparison())

            f.write("\n\nCONVERSATION HISTORY\n")
            f.write("-" * 60 + "\n")

            for msg in self.conversation_history:
                f.write(f"\n{msg['role'].upper()}:\n{msg['content']}\n")

        return f"Report exported successfully: {filename}"

    def generate_student_report(self) -> str:
        return self.export_report()

    # ------------------------------------------------------------------
    # GitHub Profile Intelligence
    # ------------------------------------------------------------------
    def _extract_github_input(self, message: str) -> Optional[str]:
        message = message.strip()

        github_url_pattern = (
            r"(?:https?://)?(?:www\.)?github\.com/"
            r"([A-Za-z0-9-]{1,39})(?:[/?#\s]|$)"
        )
        url_match = re.search(github_url_pattern, message, re.IGNORECASE)

        if url_match:
            username = url_match.group(1)
            return f"https://github.com/{username}"

        if "github" not in message.lower():
            return None

        username_patterns = [
            r"github\s*(?:id|username|profile)?\s*(?:is|:|-)?\s*([A-Za-z0-9-]{1,39})",
            r"my\s+github\s*(?:id|username|profile)?\s*(?:is|:|-)?\s*([A-Za-z0-9-]{1,39})",
        ]

        invalid_words = {
            "is", "id", "username", "profile", "link", "url",
            "account", "github", "my", "this", "here"
        }

        for pattern in username_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                username = match.group(1).strip().strip("/")

                if username.lower() not in invalid_words:
                    return username

        return None

    def _handle_github_profile_analysis(self, user_message: str) -> Optional[str]:
        github_input = self._extract_github_input(user_message)

        if not github_input:
            return None

        if self.profile_intelligence is None:
            return (
                "I can analyze GitHub profiles, but the profile_intelligence module is not available "
                "in this environment yet. Please confirm the profile_intelligence folder exists and "
                "contains github_analyzer.py, course_mapper.py, and profile_intelligence.py."
            )

        try:
            analysis = self.profile_intelligence.analyze_github(
                github_input,
                student_name=self.student_name
            )

            course_recommendation = analysis.get("course_recommendation", {})
            github_analysis = analysis.get("github_analysis", {})

            self.student_profile["github_profile"] = github_input
            self.student_profile["github_profile_intelligence"] = {
                "generated_at": analysis.get("generated_at"),
                "human_summary": analysis.get("human_summary"),
                "primary_direction": course_recommendation.get("primary_direction"),
                "recommendations": course_recommendation.get("recommendations", []),
            }

            if github_input not in self.memory["github_profiles_analyzed"]:
                self.memory["github_profiles_analyzed"].append(github_input)

            self.save_student_profile()
            self.save_memory()

            top_languages = github_analysis.get("top_languages", [])[:5]
            top_keywords = github_analysis.get("top_keywords", [])[:15]
            inferred_interests = (
                github_analysis
                .get("inferred_interests", {})
                .get("ranked_interests", [])[:5]
            )

            github_prompt = f"""
You are Aria.

The student shared a GitHub profile. The system analyzed the student's public GitHub work.

STUDENT NAME:
{self.student_name}

GITHUB INPUT:
{github_input}

GITHUB HUMAN SUMMARY:
{analysis.get("human_summary")}

TOP VISIBLE LANGUAGES:
{top_languages}

TOP TECHNICAL KEYWORDS:
{top_keywords}

INFERRED INTEREST AREAS:
{inferred_interests}

COURSE RECOMMENDATION:
Primary direction: {course_recommendation.get("primary_direction")}

Recommended course areas:
{course_recommendation.get("recommendations")}

Now respond to the student naturally.

Important rules:
- Sound like a real graduate admissions advisor, not a bot.
- Do not show raw scores unless the student asks for detailed analysis.
- Explain what their GitHub suggests about their interests and work style.
- Recommend suitable course tracks based on the GitHub evidence.
- Mention that GitHub only shows public work, so private projects, internships, resume, and academic work may change the recommendation.
- Avoid saying the student "must" choose a course. Use advisor-style language like "I would consider", "this points toward", or "your profile seems aligned with".
- Keep the answer warm, honest, and practical.
"""

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=self._runtime_system_prompt(),
                messages=[{
                    "role": "user",
                    "content": github_prompt
                }]
            )

            return response.content[0].text

        except Exception as exc:
            console.print(f"[yellow]GitHub analysis failed: {exc}[/yellow]")

            return (
                "I tried checking that GitHub profile, but I couldn’t analyze it properly. "
                "Please make sure the GitHub username or link is correct, public, and reachable. "
                "If most of your work is in private repositories, you can also paste a short summary "
                "of your projects and I’ll still help you choose the right course direction."
            )

    # ------------------------------------------------------------------
    # University agent enrichment
    # ------------------------------------------------------------------
    def _should_extract_profile_information(self, user_message: str) -> bool:
        """Avoid spending an LLM call on every casual message."""
        lower = user_message.lower()
        profile_update_keywords = [
            "my gpa", "gpa is", "gre", "toefl", "ielts", "budget",
            "graduation", "graduate in", "graduated", "major", "institution",
            "college", "university", "skills", "project", "research",
            "work experience", "internship", "github", "i studied",
            "i have", "i am from", "my name is", "program"
        ]
        return any(keyword in lower for keyword in profile_update_keywords)

    def _clean_extracted_profile(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        """Remove empty values before calling StudentProfile.update_profile()."""
        cleaned: Dict[str, Any] = {}

        for key, value in (extracted or {}).items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, dict)) and not value:
                continue
            cleaned[key] = value

        return cleaned

    def _extract_profile_information(self, user_message: str) -> Dict[str, Any]:
        """Extract structured profile updates from a natural-language message."""
        prompt = f"""
You are an AI profile extractor.

Return ONLY valid JSON. Do not explain anything.

Extract profile information from the student's message. If a field is not present,
omit it or leave it empty.

Possible fields:
{{
  "name": "",
  "institution": "",
  "major": "",
  "program": "",
  "gpa": null,
  "gre_quant": null,
  "gre_verbal": null,
  "toefl": null,
  "ielts": null,
  "budget": null,
  "graduation_year": null,
  "work_months": null,
  "research": "",
  "skills": [],
  "projects": [],
  "github": {{}}
}}

Student message:
{user_message}
"""

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()

            if text.startswith("```"):
                text = text.replace("```json", "")
                text = text.replace("```", "").strip()

            data = json.loads(text)
            if not isinstance(data, dict):
                return {}

            return self._clean_extracted_profile(data)

        except Exception as exc:
            console.print(f"[yellow]Profile extraction skipped: {exc}[/yellow]")
            return {}

    def _direct_university_answer_if_needed(self, user_message: str) -> Optional[str]:
        """Ask the relevant university agent(s) in the background for obvious
        university questions, using the persona-defined keyword lists
        (agents.commons.match_university_ids) as the single source of truth
        instead of a hardcoded per-university keyword list here."""
        matched_ids = commons.match_university_ids(user_message)

        if not matched_ids:
            return None

        if len(matched_ids) > 1:
            responses = commons.query_all(user_message, self.student_profile, university_ids=matched_ids)
            return commons.synthesise(user_message, responses, self.student_profile)

        university_id = matched_ids[0]
        agent_label = UNIVERSITY_PERSONAS.get(university_id, {}).get("agent_name", university_id)

        result = commons.query(
            university_id,
            user_message,
            self.student_profile
        )

        if not result:
            return None

        answer = result.get("answer", "")

        if result.get("source") == "human_verified":
            return answer

        if result.get("pending"):
            pending_query = result.get("pending_query", {}) or {}
            query_id = pending_query.get("query_id", "unknown")

            return (
                f"I checked with {agent_label}, but this needs human verification.\n\n"
                f"Pending Query #{query_id} has been created for the university contact."
            )

        return answer or None

    def _contains_university_query(self, message: str) -> bool:
        triggers = [
            "let me check with",
            "let me ask the",
            "checking with",
            "i'll check",
            "i will check",
            "consulting the",
            "university agent",
            "commons agent",
        ]
        if any(trigger in message.lower() for trigger in triggers):
            return True
        return bool(commons.match_university_ids(message))

    def _enrich_with_university_knowledge(
        self,
        aria_response: str,
        user_message: str
    ) -> str:
        combined_text = f"{user_message}\n{aria_response}"
        matched_ids = commons.match_university_ids(combined_text)

        if not matched_ids:
            return aria_response

        university_id = matched_ids[0]
        persona = UNIVERSITY_PERSONAS.get(university_id, {})
        agent_label = f"{persona.get('name', university_id)} ({persona.get('agent_name', university_id)})"

        result = commons.query(
            university_id,
            user_message,
            self.student_profile
        )

        if not result:
            return aria_response

        if result.get("source") == "human_verified":
            return result.get("answer", aria_response)

        if result.get("pending"):
            pending_query = result.get("pending_query", {})
            query_id = pending_query.get("query_id", "unknown")
            return (
                f"I checked with {agent_label}, but this needs human verification.\n\n"
                f"Pending Query #{query_id} has been created for the university contact."
            )

        trust = result.get("trust", {})
        confidence = trust.get("confidence", {})
        confidence_level = confidence.get("level", "Unknown")
        needs_verification = confidence.get("needs_verification", True)
        confidence_reason = confidence.get("reason", "")

        enrichment_prompt = f"""You are Aria. You just got this answer
from the {agent_label} agent in the Korgut Commons.

UNIVERSITY AGENT ANSWER:
{result['answer']}

TRUST CONTEXT FOR YOU ONLY:
Confidence level: {confidence_level}
Needs verification: {needs_verification}
Reason: {confidence_reason}

Your previous response to the student was:
{aria_response}

Now give your FINAL response that incorporates the university agent's information.

Important style rules:
- Keep your warm, honest Aria voice.
- Be natural. Do not sound like a database.
- Do not expose raw labels like confidence_score, source_type, source_url, or internal metadata.
- If confidence is high, you may sound reasonably confident.
- If confidence is medium, use careful wording like "this looks like", "I would treat this as", or "I am reasonably confident".
- If confidence is low, clearly say you do not have enough verified information.
- If verification is needed, say it naturally, like: "I’d still verify the latest official page before you submit."
- Tell the student you checked with the university agent only if it feels natural."""

        enriched = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            system=self._runtime_system_prompt(),
            messages=[{
                "role": "user",
                "content": enrichment_prompt
            }]
        )

        return enriched.content[0].text

    # ------------------------------------------------------------------
    # Verification (chat-only -- no direct student-facing verification
    # action endpoint; the student's personal agent triggers reanalysis
    # and records confirm/ignore/clarify decisions via agents.commons)
    # ------------------------------------------------------------------
    def _resolve_pending_verification_reply(self, action: str, note: str = "") -> Optional[str]:
        """Record the student's confirm/ignore/clarify decision -- action is
        decided by _classify_intent, not by re-deriving it from keywords."""
        item_id = self._pending_verification_item_id

        try:
            result = commons.resolve_verification_item(
                student_id=self.canonical_student_id,
                item_id=item_id,
                action=action,
                note=note,
            )
        except Exception as exc:
            console.print(f"[yellow]Could not resolve verification item {item_id}: {exc}[/yellow]")
            self._pending_verification_item_id = None
            return None

        self._pending_verification_item_id = None

        open_items = [item for item in result["check"].get("items", []) if not item.get("is_resolved")]

        if open_items:
            next_item = open_items[0]
            self._pending_verification_item_id = next_item["id"]
            return (
                "Got it, thanks. One more thing worth checking: "
                f"{next_item.get('message')}\n\n"
                f"Expected: {next_item.get('expected_value') or 'not specified'}\n"
                f"Found: {next_item.get('found_value') or 'not specified'}\n\n"
                "Is this correct, should I ignore it, or would you like to clarify?"
            )

        return "Thanks -- that covers everything. Your profile is fully reviewed for now."

    def _run_verification_check(self) -> Optional[str]:
        """Trigger a fresh background verification pass and surface the
        first open item, if any. Called only when _classify_intent decided
        this is what the student wants -- no keyword trigger list."""
        try:
            result = commons.run_verification(self.canonical_student_id)
        except Exception as exc:
            console.print(f"[yellow]Verification check failed: {exc}[/yellow]")
            return None

        open_items = [item for item in result.get("items", []) if not item.get("is_resolved")]

        if not open_items:
            return (
                "I checked your profile across your resume, GitHub, and LinkedIn -- "
                "everything lines up, no mismatches to review right now."
            )

        item = open_items[0]
        self._pending_verification_item_id = item["id"]

        return (
            f"I found something worth a second look: {item.get('message')}\n\n"
            f"Expected: {item.get('expected_value') or 'not specified'}\n"
            f"Found: {item.get('found_value') or 'not specified'}\n\n"
            "Is this correct, should I ignore it, or would you like to clarify what's going on?"
        )

    # ------------------------------------------------------------------
    # Intent classification -- the single reasoning step that decides what
    # a message is actually asking for. Replaces the old fixed-priority
    # keyword chain, where two intents sharing a word (e.g. "profile" or
    # "fit" appearing in both a university-fit question and a generic
    # profile question) could silently misroute to the wrong handler.
    # ------------------------------------------------------------------
    INTENT_CATEGORIES = [
        "verification_reply",
        "verification_check",
        "university_fit_single",
        "university_fit_broad",
        "university_qa",
        "github_analysis",
        "profile_analysis",
        "comparison",
        "admission_probability",
        "general",
    ]

    def _classify_intent(self, user_message: str) -> Dict[str, Any]:
        """Ask the model what this message is actually asking for. Falls
        back to {"intent": "general"} on any failure (parse error, API
        error) so the agent still answers via the general LLM call instead
        of breaking the turn."""
        pending_context = ""
        if self._pending_verification_item_id is not None:
            pending_context = """
There is a verification item awaiting the student's reply from the previous
turn (a flagged profile mismatch you asked them to confirm/ignore/clarify).
If this message is answering that, use intent "verification_reply" and set
verification_reply_action to "confirm", "ignore", or "clarify". If this
message is clearly a new, unrelated request instead, classify it normally
and leave verification_reply_action null -- the pending item stays open and
will be re-surfaced later.
"""

        classification_prompt = f"""Classify what the student is asking for in this message.
Return ONLY valid JSON, no markdown, in exactly this shape:
{{"intent": "<one of: {', '.join(self.INTENT_CATEGORIES)}>", "verification_reply_action": "confirm" or "ignore" or "clarify" or null}}

Categories:
- verification_reply: replying to a flagged profile mismatch (see context below)
- verification_check: wants their profile checked for mismatches/inconsistencies across resume, GitHub, LinkedIn, profile
- university_fit_single: asks about their personal fit, chances, or match score at ONE specific named university
- university_fit_broad: asks which university fits them best, without naming a specific one
- university_qa: a factual question about a specific university (deadlines, GPA minimums, tuition, requirements), not a personal-fit judgment
- github_analysis: shares a GitHub link/username, or explicitly asks to analyze their GitHub profile
- profile_analysis: asks about their own profile, strengths, weaknesses, gaps, or overall score, with no specific university involved
- comparison: asks to compare universities generically
- admission_probability: asks generic admission chances/probability, not tied to their stored fit assessments
- general: anything else -- greetings, open-ended advice, follow-up conversation
{pending_context}
STUDENT MESSAGE:
{user_message}
"""

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                system="Return only valid JSON. No markdown, no explanation, no extra text.",
                messages=[{"role": "user", "content": classification_prompt}],
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()

            data = json.loads(raw)

            intent = str(data.get("intent", "general")).strip()
            if intent not in self.INTENT_CATEGORIES:
                intent = "general"

            action = data.get("verification_reply_action")
            if action not in {"confirm", "ignore", "clarify"}:
                action = None

            return {"intent": intent, "verification_reply_action": action}

        except Exception as exc:
            console.print(f"[yellow]Intent classification failed, defaulting to general: {exc}[/yellow]")
            return {"intent": "general", "verification_reply_action": None}

    # ------------------------------------------------------------------
    # Main chat function
    # ------------------------------------------------------------------
    def _finalize_response(self, user_message: str, aria_response: str) -> str:
        self.conversation_history.append({
            "role": "assistant",
            "content": aria_response
        })

        self.update_memory(user_message, aria_response)
        self.save_student_profile()

        if self.profile is not None and hasattr(self.profile, "data"):
            self.profile.data.update(self.student_profile)
            if hasattr(self.profile, "add_conversation_insight"):
                self.profile.add_conversation_insight(user_message)
            else:
                self.profile.save()

        return aria_response

    def chat(self, user_message: str) -> str:
        self.messages_exchanged += 1

        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        lower_msg = user_message.lower()

        # Roadmap progress/status check -- cheap and unambiguous, checked
        # before intent classification (no reason to spend a model call
        # deciding something a plain keyword-on-existing-roadmap check
        # already answers correctly).
        roadmap = self.student_profile.get("roadmap")
        if roadmap and "progress" in lower_msg:
            response = (
                f"You are currently on Month {roadmap.get('current_month', 'N/A')} "
                f"of your {str(roadmap.get('exam', 'application')).upper()} roadmap.\n\n"
                f"Status: {roadmap.get('status', 'Not available')}"
            )
            return self._finalize_response(user_message, response)

        # Roadmap generation.
        if (
            self.roadmap_planner is not None
            and hasattr(self.roadmap_planner, "is_roadmap_request")
            and self.roadmap_planner.is_roadmap_request(user_message)
        ):
            roadmap_response = self.roadmap_planner.generate_application_roadmap(
                self.student_profile,
                user_message
            )

            if isinstance(roadmap_response, dict):
                self.student_profile["roadmap"] = roadmap_response
                response_text = json.dumps(roadmap_response, indent=2, ensure_ascii=False)
            else:
                response_text = str(roadmap_response)

            return self._finalize_response(user_message, response_text)

        # Automatically update the persistent student profile when the
        # student shares clear profile information in chat. Operates on
        # self.student_profile directly (the dict the view actually
        # persists) -- previously this was gated on the legacy self.profile
        # object, which is always None on the Django path, making this
        # entire block a silent no-op (profile facts shared mid-chat were
        # never actually saved).
        if self._should_extract_profile_information(user_message):
            extracted = self._extract_profile_information(user_message)

            if extracted:
                updated_fields = [
                    field for field, value in extracted.items()
                    if self.student_profile.get(field) != value
                ]
                self.student_profile.update(extracted)

                if updated_fields:
                    console.print("\n[bold green]Profile updated from chat[/bold green]")
                    for field in updated_fields:
                        console.print(f"[green]-[/green] {field.replace('_', ' ').title()}")

        # Single reasoning step: decide what this message is actually
        # asking for, instead of matching it against every handler's
        # keyword list in a fixed priority order (that ordering is what
        # previously let "profile"/"fit" appearing in unrelated questions
        # silently misroute to the wrong handler).
        classification = self._classify_intent(user_message)
        intent = classification["intent"]

        if intent == "verification_reply" and self._pending_verification_item_id is not None:
            action = classification.get("verification_reply_action")
            if action:
                resolved = self._resolve_pending_verification_reply(action)
                if resolved is not None:
                    return self._finalize_response(user_message, resolved)

        elif intent == "verification_check":
            verification_response = self._run_verification_check()
            if verification_response:
                return self._finalize_response(user_message, verification_response)

        elif intent == "university_fit_single":
            saved_assessment = self._saved_assessment_response(user_message)
            if saved_assessment:
                return self._finalize_response(user_message, saved_assessment)
            # Named university but no cached-answer shortcut matched (e.g. a
            # freeform fit phrasing) -- ask that university agent directly
            # rather than falling straight to a generic answer.
            university_direct_answer = self._direct_university_answer_if_needed(user_message)
            if university_direct_answer:
                return self._finalize_response(user_message, university_direct_answer)

        elif intent == "university_fit_broad":
            broad_fit_response = self._broad_fit_response(user_message)
            if broad_fit_response:
                return self._finalize_response(user_message, broad_fit_response)

        elif intent == "university_qa":
            university_direct_answer = self._direct_university_answer_if_needed(user_message)
            if university_direct_answer:
                return self._finalize_response(user_message, university_direct_answer)

        elif intent == "github_analysis":
            github_response = self._handle_github_profile_analysis(user_message)
            if github_response:
                return self._finalize_response(user_message, github_response)

        elif intent == "profile_analysis":
            profile_response = self._student_profile_response(user_message)
            if profile_response:
                return self._finalize_response(user_message, profile_response)
            # No exact template matched (e.g. "how's my profile?") -- falls
            # through to the general call below, which already carries the
            # full profile in its system prompt and can answer naturally.

        elif intent == "admission_probability":
            return self._finalize_response(user_message, self.admission_probability())

        elif intent == "comparison":
            return self._finalize_response(user_message, self.university_comparison())

        # "general", or a more specific intent whose handler didn't produce
        # a response -- fall back to the model with full context.
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=self._runtime_system_prompt(),
                messages=self.conversation_history
            )

            aria_response = response.content[0].text

        except Exception as exc:
            console.print(f"[yellow]Aria response generation failed: {exc}[/yellow]")
            aria_response = (
                "I hit an error while generating the response. Please check your "
                "ANTHROPIC_API_KEY, network connection, and model access, then try again."
            )

        if self._contains_university_query(aria_response):
            aria_response = self._enrich_with_university_knowledge(
                aria_response,
                user_message
            )

        return self._finalize_response(user_message, aria_response)

    # ------------------------------------------------------------------
    # Terminal loop
    # ------------------------------------------------------------------
    def run_interactive(self):
        university_lines = "\n".join(
            f"  • {persona.get('agent_name', uid)} ({persona.get('name', uid)})"
            for uid, persona in UNIVERSITY_PERSONAS.items()
        )
        console.print(Panel(
            f"[bold]{self.agent_name}[/bold] is ready.\n"
            f"Advising: [cyan]{self.student_name}[/cyan]\n"
            f"University agents available in the Korgut Commons (background only):\n"
            f"{university_lines}\n\n"
            f"[dim]Commands:\n"
            f"  • quit / exit / bye - exit and auto-export report\n"
            f"  • status - see Commons status\n"
            f"  • history - show message count\n"
            f"  • summary - show conversation summary\n"
            f"  • export - export full chat report\n"
            f"  • mode short - short answers\n"
            f"  • mode detailed - detailed answers\n"
            f"  • mode summary - summary-style answers[/dim]",
            title="[bold blue]Korgut Commons[/bold blue]",
            border_style="blue"
        ))

        console.print(
            f"[yellow]Current Response Mode:[/yellow] {self.response_mode}"
        )

        while True:
            try:
                user_input = input(f"\n[{self.student_name}]: ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Session ended.[/dim]")
                break

            if not user_input:
                continue

            command = user_input.lower()

            if command in ["quit", "exit", "bye"]:
                export_message = self.export_report()
                console.print(f"\n[green]{export_message}[/green]")
                console.print(
                    "\n[bold blue]Aria:[/bold blue] Good luck with your "
                    "applications. I'll be here when you need me.\n"
                )
                break

            if command == "status":
                console.print(commons.status())
                continue

            if command == "history":
                console.print(
                    f"\n[dim]{self.messages_exchanged} messages exchanged.[/dim]\n"
                )
                continue

            if command == "summary":
                console.print(Markdown(self.conversation_summary()))
                continue

            if command == "export":
                console.print(f"[green]{self.export_report()}[/green]")
                continue

            if command == "mode short":
                self.response_mode = "short"
                self.save_student_profile()
                console.print("[green]Response mode set to SHORT[/green]")
                continue

            if command == "mode detailed":
                self.response_mode = "detailed"
                self.save_student_profile()
                console.print("[green]Response mode set to DETAILED[/green]")
                continue

            if command == "mode summary":
                self.response_mode = "summary"
                self.save_student_profile()
                console.print("[green]Response mode set to SUMMARY[/green]")
                continue

            response = self.chat(user_input)

            console.print(f"\n[bold green]{self.agent_name}:[/bold green]")
            console.print(Markdown(response or "No response generated."))
