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
from personas.aria_constitution import build_aria_system_prompt
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

    PROFILE_DIR = "profiles"
    MEMORY_DIR = "memory"
    LOG_DIR = "logs"
    REPORT_DIR = "reports"

    VALID_RESPONSE_MODES = {"short", "detailed", "summary"}

    def __init__(self, student_profile: dict, profile=None, student_name: Optional[str] = None):
        """Create Aria.

        Args:
            student_profile: Normal dict used by Aria's prompt.
            profile: Optional StudentProfile object that persists insights,
                university assessments, and summary to disk.
            student_name: Optional explicit name fallback.
        """
        self.profile = profile
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

        self.conversation_history = []
        self.messages_exchanged = 0
        self.response_mode = self.student_profile.get("response_mode", "detailed")

        if self.response_mode not in self.VALID_RESPONSE_MODES:
            self.response_mode = "detailed"

        self.memory_file = os.path.join(
            self.MEMORY_DIR,
            f"{self.student_key}_memory.json"
        )

        self.memory: Dict[str, Any] = {}
        self.load_memory()

        self.system_prompt = build_aria_system_prompt(self.student_profile)

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
        for folder in [
            self.PROFILE_DIR,
            self.MEMORY_DIR,
            self.LOG_DIR,
            self.REPORT_DIR,
        ]:
            os.makedirs(folder, exist_ok=True)

    # ------------------------------------------------------------------
    # Student profile persistence
    # ------------------------------------------------------------------
    def save_student_profile(self) -> str:
        """Save through StudentProfile when available.

        This prevents duplicate files like Priya.json and priya_profile.json.
        The StudentProfile canonical JSON remains the single source of truth.
        """
        self._ensure_dirs()
        self.student_profile["response_mode"] = self.response_mode

        if self.profile is not None and hasattr(self.profile, "data"):
            self.profile.data.update(self.student_profile)
            saved_path = self.profile.save()
            return str(saved_path)

        filename = os.path.join(
            self.PROFILE_DIR,
            f"{self.student_key}.json"
        )

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.student_profile, f, indent=4, ensure_ascii=False)

        return filename

    # ------------------------------------------------------------------
    # Persistent memory
    # ------------------------------------------------------------------
    def load_memory(self):
        self._ensure_dirs()

        default_memory = {
            "student": self.student_name,
            "important_points": [],
            "universities_discussed": [],
            "github_profiles_analyzed": [],
            "last_updated": None,
        }

        if not os.path.exists(self.memory_file):
            self.memory = default_memory
            self.save_memory()
            return

        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            self.memory = {**default_memory, **loaded}
            self.memory.setdefault("important_points", [])
            self.memory.setdefault("universities_discussed", [])
            self.memory.setdefault("github_profiles_analyzed", [])
            self.memory.setdefault("last_updated", None)

        except Exception as exc:
            console.print(
                f"[yellow]Could not load memory file. Starting fresh: {exc}[/yellow]"
            )
            self.memory = default_memory
            self.save_memory()

    def save_memory(self):
        self._ensure_dirs()
        self.memory["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.memory, f, indent=4, ensure_ascii=False)

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
    # Logging
    # ------------------------------------------------------------------
    def log_conversation(self, user_message: str, aria_response: str):
        self._ensure_dirs()

        log_path = os.path.join(self.LOG_DIR, "aria_conversation_log.txt")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Student: {self.student_name}\n")
            f.write(f"User: {user_message}\n\n")
            f.write(f"Aria: {aria_response}\n")

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
        university_keywords = [
            "wright", "wright state", "raider",
            "franklin", "franklin university",
        ]
        return (
            any(keyword in lower_msg for keyword in fit_keywords)
            and any(keyword in lower_msg for keyword in university_keywords)
        )

    def _generate_fit_assessment_if_needed(self, university_id: str) -> Optional[dict]:
        """Lazily generate a fit assessment only when the student asks for it.

        If the assessment already exists in StudentProfile, reuse it without
        another Anthropic call.
        """
        if self.profile is None or not hasattr(self.profile, "data"):
            return None

        assessments = self.profile.data.setdefault("assessments", {})
        existing = assessments.get(university_id)
        if existing:
            self.student_profile = self.profile.data
            return existing

        agent = commons.get_agent(university_id)
        if not agent:
            return None

        try:
            console.print(
                f"[yellow]No saved fit assessment found for {university_id}. "
                "Generating it now...[/yellow]"
            )

            assessment = agent.assess_fit(self.profile.data)
            self.profile.add_assessment(university_id, assessment)
            self.profile.generate_summary()

            self.student_profile = self.profile.data

            console.print(
                f"[green]Fit assessment generated and saved for {university_id}.[/green]"
            )
            return assessment

        except Exception as exc:
            console.print(f"[yellow]Could not generate fit assessment for {university_id}.[/yellow]")
            console.print(f"[dim]{exc}[/dim]")
            return None

    def _saved_assessment_response(self, user_message: str) -> Optional[str]:
        """Answer simple questions from stored fit assessments without another LLM call."""
        lower_msg = user_message.lower()
        assessments = self.student_profile.get("assessments", {}) or {}

        university_map = {
            "wright_state_cs": {
                "keywords": ["wright", "wright state", "raider"],
                "display": "Wright State",
            },
            "franklin_cs": {
                "keywords": ["franklin", "franklin university"],
                "display": "Franklin University",
            },
        }

        selected_id = None
        selected_meta = None

        for university_id, meta in university_map.items():
            if any(keyword in lower_msg for keyword in meta["keywords"]):
                selected_id = university_id
                selected_meta = meta
                break

        if selected_id is None:
            # If the user asks a generic match question and only one assessment exists,
            # use that. Otherwise let Aria answer normally.
            if len(assessments) == 1:
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
        """Ask the relevant university agent directly for obvious university questions."""
        lower_msg = user_message.lower()

        wright_keywords = [
            "wright state",
            "wright",
            "raider",
            "ms computer science",
            "ms cs",
            "computer science",
            "fall 2026",
            "fall 2027",
            "indian students",
            "admitted",
            "admission numbers",
            "minimum gpa",
            "assistantship",
            "funding",
            "deadline",
            "faculty",
            "professor",
            "research lab",
            "tuition",
            "scholarship",
        ]

        franklin_keywords = [
            "franklin",
            "franklin university",
        ]

        university_id = None
        agent_label = None

        if any(keyword in lower_msg for keyword in franklin_keywords):
            university_id = "franklin_cs"
            agent_label = "Franklin"
        elif any(keyword in lower_msg for keyword in wright_keywords):
            university_id = "wright_state_cs"
            agent_label = "Raider"

        if university_id is None:
            return None

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
            "wright state agent",
            "university agent",
            "commons agent",
            "franklin agent",
            "franklin university",
            "franklin",
            "wright state",
            "raider"
        ]
        return any(trigger in message.lower() for trigger in triggers)

    def _enrich_with_university_knowledge(
        self,
        aria_response: str,
        user_message: str
    ) -> str:
        combined_text = f"{user_message}\n{aria_response}".lower()

        if "franklin" in combined_text:
            university_id = "franklin_cs"
            agent_label = "Franklin University (Franklin)"
        elif "wright state" in combined_text or "raider" in combined_text:
            university_id = "wright_state_cs"
            agent_label = "Wright State (Raider)"
        else:
            return aria_response

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
    # Main chat function
    # ------------------------------------------------------------------
    def _finalize_response(self, user_message: str, aria_response: str) -> str:
        self.conversation_history.append({
            "role": "assistant",
            "content": aria_response
        })

        self.log_conversation(user_message, aria_response)
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

        # Roadmap progress/status check.
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

        # Automatically update the persistent student profile when the user shares
        # clear profile information.
        if (
            self.profile is not None
            and hasattr(self.profile, "update_profile")
            and self._should_extract_profile_information(user_message)
        ):
            extracted = self._extract_profile_information(user_message)

            if extracted:
                old_score = self.profile.data.get("overall_profile_score", 0)
                updated_fields = self.profile.update_profile(extracted) or []

                if updated_fields:
                    if hasattr(self.profile, "build_ai_profile"):
                        self.profile.build_ai_profile()

                    self.student_profile = self.profile.data
                    new_score = self.profile.data.get("overall_profile_score", 0)

                    console.print("\n[bold green]🧠 AI profile updated[/bold green]")
                    for field in updated_fields:
                        console.print(f"[green]✓ Updated:[/green] {field.replace('_', ' ').title()}")

                    if new_score != old_score:
                        console.print(f"[green]Overall Score:[/green] {old_score} → {new_score}")

        saved_assessment = self._saved_assessment_response(user_message)
        if saved_assessment:
            return self._finalize_response(user_message, saved_assessment)

        profile_response = self._student_profile_response(user_message)
        if profile_response:
            return self._finalize_response(user_message, profile_response)

        if "probability" in lower_msg or "chance" in lower_msg:
            return self._finalize_response(
                user_message,
                self.admission_probability()
            )

        if "compare" in lower_msg or "cmu vs wright" in lower_msg:
            return self._finalize_response(
                user_message,
                self.university_comparison()
            )

        github_response = self._handle_github_profile_analysis(user_message)
        if github_response:
            return self._finalize_response(user_message, github_response)

        university_direct_answer = self._direct_university_answer_if_needed(user_message)
        if university_direct_answer:
            return self._finalize_response(user_message, university_direct_answer)

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
        console.print(Panel(
            f"[bold]Aria[/bold] is ready.\n"
            f"Advising: [cyan]{self.student_name}[/cyan]\n"
            f"University agents available in the Korgut Commons:\n"
            f"  • Raider (Wright State University CS)\n"
            f"  • Franklin (Franklin University MSCS, if registered)\n\n"
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

            console.print("\n[bold green]Aria:[/bold green]")
            console.print(Markdown(response or "No response generated."))
