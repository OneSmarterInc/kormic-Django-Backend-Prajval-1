# agents/student_profile.py

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet


class StudentProfile:
    """
    Persistent Student Profile
    Stores one living JSON profile per student.
    """

    PROFILES_DIR = Path("profiles")
    EXPORT_DIR = Path("exports")
    AUDIT_FILE = Path("profiles/student_profile_audit.json")

    DEFAULT_DATA = {
        "name": "Unknown",
        "email": None,
        "institution": None,
        "major": None,
        "graduation_year": None,

        "gpa": None,
        "gpa_scale": "4.0",
        "gre_quant": None,
        "gre_verbal": None,
        "toefl": None,

        "budget": None,
        "work_months": 0,
        "github": None,

        "skills": [],
        "technical_skills": [],
        "soft_skills": [],

        "projects": [],
        "research": None,
        "research_interests": [],
        "publications": [],

        "career_goals": [],
        "conversation_insights": [],
        "assessments": {},

        "academic_intelligence": {},
        "technical_intelligence": {},
        "research_intelligence": {},
        "behaviour_intelligence": {},

        "overall_profile_score": 0,
        "overall_profile": {},
        "profile_completeness": {},

        "strengths": [],
        "weaknesses": [],
        "recommendations": [],

        "ai_summary": "",
        "summary": "",

        "created_at": None,
        "updated_at": None
    }

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self.EXPORT_DIR.mkdir(parents=True, exist_ok=True)

        self.data = self.DEFAULT_DATA.copy()

        if data:
            self.data.update(data)

        now = self._now()
        if not self.data.get("created_at"):
            self.data["created_at"] = now

        self.data["updated_at"] = now
        self._normalize_profile_schema()

    # --------------------------------------------------
    # Utility Methods
    # --------------------------------------------------

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _safe_filename(self, name: str) -> str:
        name = name or "student"
        name = name.strip().lower()
        name = re.sub(r"[^\w\s-]", "", name)
        name = re.sub(r"\s+", "_", name)
        return name or "student"

    def _profile_path(self) -> Path:
        safe_name = self._safe_filename(self.data.get("name", "student"))
        return self.PROFILES_DIR / f"{safe_name}.json"

    def _audit_failure(self, event: str, message: str, details: str = ""):
        audit_data = []

        try:
            if self.AUDIT_FILE.exists():
                with open(self.AUDIT_FILE, "r", encoding="utf-8") as f:
                    audit_data = json.load(f)
        except Exception:
            audit_data = []

        audit_data.append({
            "event": event,
            "message": message,
            "details": details,
            "profile_name": self.data.get("name", "Unknown"),
            "created_at": self._now()
        })

        try:
            with open(self.AUDIT_FILE, "w", encoding="utf-8") as f:
                json.dump(audit_data, f, indent=4, ensure_ascii=False)
        except Exception:
            print(f"[AUDIT FAILURE] {event}: {message} {details}")

    def _normalize_profile_schema(self):
        """
        Keeps old and new profile formats compatible.
        """

        if "skills" not in self.data or not isinstance(self.data.get("skills"), list):
            self.data["skills"] = []

        if "technical_skills" not in self.data or not isinstance(self.data.get("technical_skills"), list):
            self.data["technical_skills"] = []

        # Convert technical_skills dict format into simple skills list
        for item in self.data.get("technical_skills", []):
            if isinstance(item, dict) and item.get("skill"):
                if item["skill"] not in self.data["skills"]:
                    self.data["skills"].append(item["skill"])
            elif isinstance(item, str):
                if item not in self.data["skills"]:
                    self.data["skills"].append(item)

        if "projects" not in self.data or not isinstance(self.data.get("projects"), list):
            self.data["projects"] = []

        if "assessments" not in self.data or not isinstance(self.data.get("assessments"), dict):
            self.data["assessments"] = {}

        if "conversation_insights" not in self.data:
            self.data["conversation_insights"] = []

        if "profile_completeness" not in self.data or not isinstance(self.data.get("profile_completeness"), dict):
            self.data["profile_completeness"] = {}

    def _to_number(self, value):
        try:
            if value in [None, "", [], {}]:
                return None
            return float(value)
        except Exception:
            return None

    # --------------------------------------------------
    # Save / Load
    # --------------------------------------------------

    def save(self) -> Path:
        self.data["updated_at"] = self._now()
        self._normalize_profile_schema()

        filename = self._profile_path()

        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
            return filename

        except UnicodeEncodeError as e:
            self._audit_failure(
                "unicode_write_error",
                "UTF-8 writing failed while saving student profile.",
                str(e)
            )
            raise

        except Exception as e:
            self._audit_failure(
                "profile_save_error",
                "Failed to save student profile.",
                str(e)
            )
            raise

    @classmethod
    def load(cls, student_name: str):
        safe_name = re.sub(r"[^\w\s-]", "", student_name.strip().lower())
        safe_name = re.sub(r"\s+", "_", safe_name)

        filename = Path("profiles") / f"{safe_name}.json"

        if not filename.exists():
            raise FileNotFoundError(f"Profile not found: {filename}")

        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(data)

        except UnicodeDecodeError as e:
            raise UnicodeDecodeError(
                e.encoding,
                e.object,
                e.start,
                e.end,
                f"UTF-8 decoding failed for profile file {filename}: {e.reason}"
            )

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in profile file {filename}: {e}")

    # --------------------------------------------------
    # Profile Updates
    # --------------------------------------------------

    def update_profile(self, extracted_data: Dict[str, Any]) -> List[str]:
        updated_fields = []

        if not isinstance(extracted_data, dict):
            self._audit_failure(
                "invalid_profile_update",
                "Extracted data must be a dictionary.",
                str(type(extracted_data))
            )
            return updated_fields

        for key, value in extracted_data.items():
            if value in [None, "", [], {}]:
                continue

            if key in ["skills", "technical_skills"]:
                incoming_skills = []

                for skill in value:
                    if isinstance(skill, dict) and skill.get("skill"):
                        incoming_skills.append(skill["skill"])
                    elif isinstance(skill, str):
                        incoming_skills.append(skill)

                existing = self.data.get("skills", [])
                merged = sorted(set(existing + incoming_skills))

                self.data["skills"] = merged
                self.data["technical_skills"] = value

                updated_fields.append("skills")

            elif key == "projects":
                existing_projects = self.data.get("projects", [])
                if isinstance(value, list):
                    existing_projects.extend(value)
                else:
                    existing_projects.append(value)

                self.data["projects"] = existing_projects
                updated_fields.append("projects")

            elif key == "research_interests":
                existing = self.data.get("research_interests", [])
                if isinstance(value, list):
                    self.data["research_interests"] = sorted(set(existing + value))
                else:
                    self.data["research_interests"] = sorted(set(existing + [value]))

                updated_fields.append("research_interests")

            else:
                self.data[key] = value
                updated_fields.append(key)

        self.save()
        return updated_fields

    def add_conversation_insight(self, insight: str):
        self.data.setdefault("conversation_insights", [])
        self.data["conversation_insights"].append({
            "insight": insight,
            "created_at": self._now()
        })
        self.save()

    def add_assessment(self, university_id: str, assessment: Dict[str, Any]):
        self.data.setdefault("assessments", {})
        self.data["assessments"][university_id] = assessment
        self.save()

    def update_preference(self, key: str, value: Any):
        self.data[key] = value
        self.save()

    # --------------------------------------------------
    # Ratings
    # --------------------------------------------------

    def get_star_rating(self, score):
        score = score or 0

        if score >= 90:
            return "★★★★★"
        elif score >= 80:
            return "★★★★☆"
        elif score >= 70:
            return "★★★☆☆"
        elif score >= 60:
            return "★★☆☆☆"
        elif score >= 40:
            return "★☆☆☆☆"

        return "☆☆☆☆☆"

    # --------------------------------------------------
    # AI Profile Analysis
    # --------------------------------------------------

    def analyze_academics(self):
        gpa = self._to_number(self.data.get("gpa"))
        gre_quant = self._to_number(self.data.get("gre_quant"))
        toefl = self._to_number(self.data.get("toefl"))

        academic_score = 0
        strengths = []
        weaknesses = []
        recommendations = []
        evidence = []

        if gpa is not None:
            if gpa >= 3.8:
                academic_score += 40
                strengths.append("Outstanding academic performance")
                evidence.append(f"GPA of {gpa} demonstrates excellent academic consistency.")
            elif gpa >= 3.5:
                academic_score += 35
                strengths.append("Strong GPA")
                evidence.append(f"GPA of {gpa} is above the admission requirement for many universities.")
            elif gpa >= 3.0:
                academic_score += 30
                strengths.append("Meets graduate admission GPA")
                evidence.append(f"GPA of {gpa} satisfies the minimum GPA requirement for most MS programs.")
            else:
                academic_score += 15
                weaknesses.append("Undergraduate GPA needs improvement.")
                recommendations.append("Improve your academic profile through certifications or additional coursework.")
                evidence.append(f"GPA of {gpa} is below the preferred range.")
        else:
            weaknesses.append("GPA information is unavailable.")

        if gre_quant is not None:
            if gre_quant >= 165:
                academic_score += 30
                strengths.append("Outstanding quantitative ability")
                evidence.append(f"GRE Quant score of {gre_quant} is exceptionally competitive.")
            elif gre_quant >= 160:
                academic_score += 25
                strengths.append("Strong quantitative reasoning")
                evidence.append(f"GRE Quant score of {gre_quant} is competitive for Computer Science.")
            elif gre_quant >= 155:
                academic_score += 20
                evidence.append(f"GRE Quant score of {gre_quant} is acceptable.")
            else:
                weaknesses.append("GRE Quant score could be improved.")
        else:
            weaknesses.append("GRE score not available.")
            recommendations.append("Consider taking the GRE if the target universities recommend it.")

        if toefl is not None:
            if toefl >= 105:
                academic_score += 20
                strengths.append("Excellent English proficiency")
                evidence.append(f"TOEFL score of {toefl} demonstrates excellent communication skills.")
            elif toefl >= 95:
                academic_score += 15
                strengths.append("Good English proficiency")
            elif toefl >= 80:
                academic_score += 10
            else:
                weaknesses.append("English proficiency should be improved.")
        else:
            weaknesses.append("TOEFL score unavailable.")
            recommendations.append("Complete an English proficiency test before applying.")

        academic_score = min(academic_score, 100)

        if academic_score >= 80:
            readiness = "High"
        elif academic_score >= 60:
            readiness = "Moderate"
        else:
            readiness = "Needs Improvement"

        self.data["academic_intelligence"] = {
            "academic_score": academic_score,
            "readiness": readiness,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations,
            "evidence": evidence
        }

        self.save()
        return self.data["academic_intelligence"]

    def analyze_technical_profile(self):
        self._normalize_profile_schema()

        skills = self.data.get("skills", [])
        projects = self.data.get("projects", [])
        research = self.data.get("research")
        work_months = self._to_number(self.data.get("work_months")) or 0
        github = self.data.get("github")

        skill_names = []

        for skill in skills:
            if isinstance(skill, dict) and skill.get("skill"):
                skill_names.append(skill["skill"].lower())
            elif isinstance(skill, str):
                skill_names.append(skill.lower())

        project_count = len(projects)
        technical_score = 0
        strengths = []
        weaknesses = []
        recommendations = []
        evidence = []

        ai_skills = {
            "python", "tensorflow", "pytorch", "keras", "opencv",
            "scikit-learn", "machine learning", "deep learning", "nlp",
            "artificial intelligence"
        }

        web_skills = {
            "html", "css", "javascript", "react", "angular", "node",
            "node.js", "django", "flask", "fastapi", "bootstrap"
        }

        database_skills = {
            "mysql", "postgresql", "mongodb", "sql", "supabase"
        }

        ai_count = sum(1 for skill in skill_names if skill in ai_skills)
        web_count = sum(1 for skill in skill_names if skill in web_skills)
        db_count = sum(1 for skill in skill_names if skill in database_skills)

        if ai_count >= 3:
            technical_score += 30
            strengths.append("Strong AI / Machine Learning skillset")
            evidence.append(f"{ai_count} AI/ML related technologies identified.")
        elif ai_count > 0:
            technical_score += 15
            strengths.append("Basic AI / Machine Learning knowledge")
            evidence.append(f"{ai_count} AI-related technologies identified.")
        else:
            weaknesses.append("Limited AI / Machine Learning exposure.")
            recommendations.append("Learn PyTorch, TensorFlow, or Scikit-Learn.")

        if web_count >= 3:
            technical_score += 20
            strengths.append("Strong Web Development skills")
            evidence.append(f"{web_count} Web Development technologies identified.")
        elif web_count > 0:
            technical_score += 10
            strengths.append("Basic Web Development knowledge")
        else:
            weaknesses.append("Limited Web Development experience.")

        if db_count >= 2:
            technical_score += 15
            strengths.append("Strong Database knowledge")
            evidence.append(f"{db_count} database technologies identified.")
        elif db_count > 0:
            technical_score += 8
            strengths.append("Basic Database knowledge")
        else:
            weaknesses.append("No database experience.")

        if project_count >= 4:
            technical_score += 25
            strengths.append("Excellent project portfolio")
            evidence.append(f"{project_count} projects completed.")
        elif project_count >= 2:
            technical_score += 15
            strengths.append("Good practical project experience")
            evidence.append(f"{project_count} projects completed.")
        else:
            weaknesses.append("Needs more practical projects.")
            recommendations.append("Build more end-to-end software projects.")

        if work_months >= 6:
            technical_score += 20
            strengths.append("Strong industry experience")
            evidence.append(f"{work_months} months of professional experience.")
        elif work_months > 0:
            technical_score += 10
            strengths.append("Some industry exposure")
            evidence.append(f"{work_months} months of professional experience.")
        else:
            weaknesses.append("No internship or industry experience.")
            recommendations.append("Complete at least one internship.")

        if research:
            technical_score += 15
            strengths.append("Research experience available")
            evidence.append("Student has research experience.")
        else:
            weaknesses.append("No research experience.")
            recommendations.append("Participate in research or publish a paper.")

        if github:
            technical_score += 10
            strengths.append("GitHub profile available")
            evidence.append("Technical portfolio available on GitHub.")
        else:
            recommendations.append("Create and maintain an active GitHub profile.")

        technical_score = min(technical_score, 100)

        if technical_score >= 90:
            level = "Excellent"
        elif technical_score >= 75:
            level = "Very Strong"
        elif technical_score >= 60:
            level = "Strong"
        elif technical_score >= 40:
            level = "Intermediate"
        else:
            level = "Beginner"

        self.data["technical_intelligence"] = {
            "technical_score": technical_score,
            "technical_level": level,
            "skill_matrix": {
                "AI / Machine Learning": ai_count,
                "Web Development": web_count,
                "Databases": db_count,
                "Projects": project_count,
                "Industry Experience Months": work_months
            },
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations,
            "evidence": evidence
        }

        self.save()
        return self.data["technical_intelligence"]

    def analyze_research(self):
        research = self.data.get("research")
        publications = self.data.get("publications", [])
        interests = self.data.get("research_interests", [])

        score = 0
        strengths = []
        weaknesses = []
        recommendations = []

        if research:
            score += 40
            strengths.append("Research experience mentioned.")
        else:
            weaknesses.append("No direct research experience found.")
            recommendations.append("Add research projects, paper work, or academic exploration.")

        if publications:
            score += 40
            strengths.append("Publication record available.")
        else:
            recommendations.append("Try to publish or document research-based work.")

        if interests:
            score += 20
            strengths.append("Research interests are clearly listed.")
        else:
            weaknesses.append("Research interests are missing.")

        score = min(score, 100)

        self.data["research_intelligence"] = {
            "research_score": score,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations
        }

        self.save()
        return self.data["research_intelligence"]

    def analyze_behaviour(self):
        insights = self.data.get("conversation_insights", [])

        score = min(len(insights) * 10, 100)

        self.data["behaviour_intelligence"] = {
            "behaviour_score": score,
            "evidence_count": len(insights),
            "summary": "Behaviour analysis is based on stored conversation insights."
        }

        self.save()
        return self.data["behaviour_intelligence"]

    def calculate_profile_score(self):
        academic = self.data.get("academic_intelligence", {})
        technical = self.data.get("technical_intelligence", {})
        research = self.data.get("research_intelligence", {})

        academic_score = academic.get("academic_score", 0)
        technical_score = technical.get("technical_score", 0)
        research_score = research.get("research_score", 0)

        overall_score = round(
            (academic_score * 0.35) +
            (technical_score * 0.45) +
            (research_score * 0.20)
        )

        overall_score = min(overall_score, 100)

        if overall_score >= 90:
            profile_level = "Excellent"
        elif overall_score >= 80:
            profile_level = "Very Strong"
        elif overall_score >= 70:
            profile_level = "Strong"
        elif overall_score >= 60:
            profile_level = "Moderate"
        else:
            profile_level = "Needs Improvement"

        if overall_score >= 80:
            recommendation = "Highly recommended for ambitious and target universities."
        elif overall_score >= 65:
            recommendation = "Suitable for target universities with a balanced application strategy."
        else:
            recommendation = "Strengthen the profile before applying to competitive universities."

        self.data["overall_profile_score"] = overall_score
        self.data["overall_profile"] = {
            "overall_score": overall_score,
            "profile_level": profile_level,
            "recommendation": recommendation
        }

        self.save()
        return self.data["overall_profile"]

    def calculate_profile_completeness(self):
        required_fields = {
            "name": self.data.get("name"),
            "institution": self.data.get("institution"),
            "major": self.data.get("major"),
            "gpa": self.data.get("gpa"),
            "gre_quant": self.data.get("gre_quant"),
            "toefl": self.data.get("toefl"),
            "budget": self.data.get("budget"),
            "research": self.data.get("research"),
            "skills": self.data.get("skills"),
            "projects": self.data.get("projects"),
            "github": self.data.get("github"),
            "work_months": self.data.get("work_months"),
        }

        completed = 0
        missing = []

        for field, value in required_fields.items():
            if value not in [None, "", [], {}]:
                completed += 1
            else:
                missing.append(field)

        total = len(required_fields)
        percentage = round((completed / total) * 100)

        self.data["profile_completeness"] = {
            "completed": completed,
            "total": total,
            "percentage": percentage,
            "missing": missing
        }

        self.save()
        return self.data["profile_completeness"]

    def generate_ai_summary(self):
        academic = self.data.get("academic_intelligence", {})
        technical = self.data.get("technical_intelligence", {})
        overall = self.data.get("overall_profile", {})

        summary = f"""
AI PROFILE SUMMARY
==================================

Overall Profile Score : {self.data.get("overall_profile_score", 0)}/100
Profile Level         : {overall.get("profile_level", "Unknown")}

Academic Readiness    : {academic.get("readiness", "Unknown")}
Technical Level       : {technical.get("technical_level", "Unknown")}

Academic Strengths:
{chr(10).join("- " + s for s in academic.get("strengths", [])) or "- Not available"}

Technical Strengths:
{chr(10).join("- " + s for s in technical.get("strengths", [])) or "- Not available"}

Recommendations:
{chr(10).join("- " + r for r in technical.get("recommendations", [])) or "- No recommendations"}
"""

        self.data["ai_summary"] = summary
        self.save()
        return summary

    def build_ai_profile(self):
        self.analyze_academics()
        self.analyze_technical_profile()
        self.analyze_research()
        self.analyze_behaviour()
        self.calculate_profile_score()
        self.calculate_profile_completeness()
        self.generate_ai_summary()

        self.save()

        return {
            "academic": self.data.get("academic_intelligence"),
            "technical": self.data.get("technical_intelligence"),
            "research": self.data.get("research_intelligence"),
            "behaviour": self.data.get("behaviour_intelligence"),
            "overall": self.data.get("overall_profile"),
            "summary": self.data.get("ai_summary")
        }

    # --------------------------------------------------
    # Context / Summary
    # --------------------------------------------------

    def to_aria_context(self):
        context = []

        fields = [
            ("Name", "name"),
            ("Institution", "institution"),
            ("Major", "major"),
            ("GPA", "gpa"),
            ("GRE Quant", "gre_quant"),
            ("TOEFL", "toefl"),
            ("Budget", "budget"),
            ("Skills", "skills"),
            ("Projects", "projects")
        ]

        for label, key in fields:
            value = self.data.get(key)
            if value not in [None, "", [], {}]:
                context.append(f"{label}: {value}")

        return "\n".join(context)

    def generate_summary(self):
        self.build_ai_profile()

        academic = self.data.get("academic_intelligence", {})
        technical = self.data.get("technical_intelligence", {})
        completeness = self.data.get("profile_completeness", {})

        summary = f"""
============================================================
                 AI STUDENT PROFILE SUMMARY
============================================================

PERSONAL INFORMATION
------------------------------------------------------------
Name                 : {self.data.get("name", "Unknown")}
Institution          : {self.data.get("institution", "Not Provided")}
Major                : {self.data.get("major", "Not Provided")}
Graduation Year      : {self.data.get("graduation_year", "Not Provided")}

ACADEMIC PROFILE
------------------------------------------------------------
GPA                  : {self.data.get("gpa", "Not Provided")}
GRE Quant            : {self.data.get("gre_quant", "Not Provided")}
TOEFL                : {self.data.get("toefl", "Not Provided")}
Budget               : USD {self.data.get("budget", "Not Provided")}

Academic Score       : {academic.get("academic_score", "N/A")}
Academic Readiness   : {academic.get("readiness", "N/A")}

TECHNICAL PROFILE
------------------------------------------------------------
Technical Score      : {technical.get("technical_score", "N/A")}
Technical Level      : {technical.get("technical_level", "N/A")}

OVERALL PROFILE
------------------------------------------------------------
Overall AI Score     : {self.data.get("overall_profile_score", 0)}/100
Profile Completeness : {completeness.get("percentage", 0)}%

Conversation Insights: {len(self.data.get("conversation_insights", []))}
University Reviews   : {len(self.data.get("assessments", {}))}

============================================================
"""

        self.data["summary"] = summary
        self.save()
        return summary

    # --------------------------------------------------
    # Display / Export
    # --------------------------------------------------

    def print_status(self):
        self.build_ai_profile()

        print("\n" + "=" * 60)
        print("          STUDENT PROFILE STATUS")
        print("=" * 60)

        print(f"Name               : {self.data.get('name', 'Unknown')}")
        print(f"Institution        : {self.data.get('institution', 'Not Available')}")
        print(f"Major              : {self.data.get('major', 'Not Available')}")
        print(f"GPA                : {self.data.get('gpa', 'N/A')}")
        print(f"GRE Quant          : {self.data.get('gre_quant', 'N/A')}")
        print(f"TOEFL              : {self.data.get('toefl', 'N/A')}")
        print(f"Budget             : ${self.data.get('budget', 'N/A')}")

        print("-" * 60)

        academic = self.data.get("academic_intelligence", {})
        technical = self.data.get("technical_intelligence", {})
        overall = self.data.get("overall_profile_score", 0)
        completeness = self.data.get("profile_completeness", {})

        print("\nACADEMIC INTELLIGENCE")
        print("-" * 60)
        print(f"Rating             : {self.get_star_rating(academic.get('academic_score', 0))}")
        print(f"Academic Score     : {academic.get('academic_score', 0)}/100")
        print(f"Readiness          : {academic.get('readiness', 'Unknown')}")

        print("\nTECHNICAL INTELLIGENCE")
        print("-" * 60)
        print(f"Rating             : {self.get_star_rating(technical.get('technical_score', 0))}")
        print(f"Technical Score    : {technical.get('technical_score', 0)}/100")
        print(f"Technical Level    : {technical.get('technical_level', 'Unknown')}")

        print("\nOVERALL PROFILE")
        print("-" * 60)
        print(f"Rating             : {self.get_star_rating(overall)}")
        print(f"Overall Score      : {overall}/100")
        print(f"Completeness       : {completeness.get('percentage', 0)}%")

        missing = completeness.get("missing", [])
        if missing:
            print("\nMissing Information")
            for item in missing:
                print(f"  • {item.replace('_', ' ').title()}")
        else:
            print("\n✓ Profile is complete.")

        print("-" * 60)

    def export_pdf(self):
        self.build_ai_profile()

        safe_name = self._safe_filename(self.data.get("name", "Student"))
        filename = self.EXPORT_DIR / f"{safe_name}_AI_Profile.pdf"

        doc = SimpleDocTemplate(str(filename))
        styles = getSampleStyleSheet()
        story = []

        story.append(
            Paragraph(
                "<b><font size=18>AI Student Profile Report</font></b>",
                styles["Title"]
            )
        )

        story.append(Spacer(1, 20))

        fields = [
            ("Name", self.data.get("name", "Unknown")),
            ("Institution", self.data.get("institution", "Not Available")),
            ("Major", self.data.get("major", "Not Available")),
            ("GPA", self.data.get("gpa", "Not Available")),
            ("GRE Quant", self.data.get("gre_quant", "Not Available")),
            ("TOEFL", self.data.get("toefl", "Not Available")),
            ("Budget", f"${self.data.get('budget', 'Not Available')}")
        ]

        for label, value in fields:
            story.append(
                Paragraph(
                    f"<b>{label}:</b> {value}",
                    styles["Normal"]
                )
            )

        story.append(Spacer(1, 20))

        academic = self.data.get("academic_intelligence", {})
        technical = self.data.get("technical_intelligence", {})
        overall = self.data.get("overall_profile", {})

        story.append(Paragraph("<b>Academic Intelligence</b>", styles["Heading2"]))
        story.append(Paragraph(f"Academic Score: {academic.get('academic_score', 0)}/100", styles["Normal"]))
        story.append(Paragraph(f"Readiness: {academic.get('readiness', 'Unknown')}", styles["Normal"]))

        story.append(Spacer(1, 15))

        story.append(Paragraph("<b>Technical Intelligence</b>", styles["Heading2"]))
        story.append(Paragraph(f"Technical Score: {technical.get('technical_score', 0)}/100", styles["Normal"]))
        story.append(Paragraph(f"Technical Level: {technical.get('technical_level', 'Unknown')}", styles["Normal"]))

        story.append(Spacer(1, 15))

        story.append(Paragraph("<b>Overall Profile</b>", styles["Heading2"]))
        story.append(Paragraph(f"Overall Score: {self.data.get('overall_profile_score', 0)}/100", styles["Normal"]))
        story.append(Paragraph(f"Profile Level: {overall.get('profile_level', 'Unknown')}", styles["Normal"]))

        doc.build(story)

        print("\n✅ PDF exported successfully:")
        print(filename)

        return filename