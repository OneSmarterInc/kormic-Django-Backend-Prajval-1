# universities/migrations/0002_seed_legacy_universities.py
# Data migration: seeds University rows for the two universities that used
# to be hardcoded in personas/university_personas.py (wright_state_cs,
# franklin_cs), transcribing their persona-input fields and re-creating
# their key_facts_seed entries as UniversityKnowledgeEntry rows. This keeps
# every existing table that already stores these ids as a plain string
# (Account, FitAssessment, PendingQuery, VerifiedAnswer,
# UniversityKnowledgeEntry, ChatMessage, UniversityQuestionLog,
# PresenterAuditLog) resolving correctly with zero data loss once
# personas/university_personas.py is deleted.

from __future__ import annotations

from django.db import migrations

LEGACY_UNIVERSITIES = [
    {
        "id": "wright_state_cs",
        "name": "Wright State University — CS & Engineering",
        "agent_name": "Raider",
        "location": "Fairborn, Ohio",
        "tagline": "The AFRL connection. Real research, real value.",
        "description": (
            "Wright State University is located in Fairborn, Ohio, adjacent to "
            "Wright-Patterson Air Force Base. It offers graduate study through the "
            "Department of Computer Science and Engineering, including computer "
            "science and computer engineering pathways."
        ),
        "scrape_urls": [
            "https://engineering-computer-science.wright.edu/computer-science-and-engineering",
            "https://engineering-computer-science.wright.edu/computer-science-and-engineering/master-of-science-in-computer-science",
            "https://engineering-computer-science.wright.edu/computer-science-and-engineering/master-of-science-in-computer-engineering",
            "https://engineering-computer-science.wright.edu/computer-science-and-engineering/master-of-science-in-data-science",
            "https://engineering-computer-science.wright.edu/computer-science-and-engineering/areas-of-research",
            "https://engineering-computer-science.wright.edu/computer-science-and-engineering/forms-and-documents",
            "https://engineering-computer-science.wright.edu/degrees-and-programs",
            "https://www.wright.edu/admissions/international/graduate-programs",
            "https://www.wright.edu/admissions/international/graduate-admission-requirements",
            "https://www.wright.edu/admissions/international/graduate-application-checklist",
            "https://www.wright.edu/admissions/international/graduate-tuition-and-fees",
        ],
        "tone_descriptors": ["practical", "grounded", "value-conscious", "Dayton-aware"],
        "best_fit_notes": (
            "Students seeking a traditional public university environment, applied "
            "research, regional defense/aerospace opportunities (AFRL, Wright-Patterson "
            "AFB), and a value-conscious graduate pathway."
        ),
        "not_best_fit_notes": (
            "Students who mainly want elite research branding, a top-10 CS signal, or a "
            "heavily Silicon Valley-oriented network."
        ),
        "communication_style_notes": (
            "Acknowledge Wright State's ranking/prestige position honestly. Make the case "
            "for Wright State by describing fit, value, research, and outcomes, not prestige."
        ),
        "never_do_notes": "Discourage a student from applying to higher-ranked schools if their profile supports it.",
        "key_facts_seed": [
            {
                "topic": "Location",
                "content": (
                    "Wright State University is located in Fairborn, Ohio, adjacent to "
                    "Wright-Patterson Air Force Base."
                ),
            },
            {
                "topic": "AFRL Connection",
                "content": (
                    "Wright State's location near Wright-Patterson AFB and AFRL can create "
                    "valuable regional opportunities for students interested in applied "
                    "research, cybersecurity, aerospace, AI, human factors, and defense-related systems."
                ),
            },
            {
                "topic": "Program Overview",
                "content": (
                    "Wright State offers graduate study through the Department of Computer "
                    "Science and Engineering, including computer science and computer engineering pathways."
                ),
            },
            {
                "topic": "Research Areas",
                "content": (
                    "Relevant research areas include cybersecurity and information assurance, "
                    "AI and machine learning, human-computer interaction, bioinformatics, "
                    "computer vision, distributed systems, software engineering, and systems engineering."
                ),
            },
            {
                "topic": "Data Science",
                "content": (
                    "Wright State has graduate-level data science program information in its "
                    "Computer Science and Engineering pages. Students should verify current "
                    "degree structure and requirements on the official page."
                ),
            },
            {
                "topic": "Tuition",
                "content": (
                    "Wright State tuition and fees should be verified from the official graduate "
                    "tuition and fees page because rates can change by year, residency, program, "
                    "and student status."
                ),
            },
            {
                "topic": "Assistantships",
                "content": (
                    "Graduate assistantships may be available for qualified students, but exact "
                    "availability, stipend, tuition coverage, and eligibility must be verified "
                    "with the department or official funding pages."
                ),
            },
            {
                "topic": "Location Advantage",
                "content": (
                    "The Dayton region includes Wright-Patterson AFB, AFRL, the National Air "
                    "and Space Intelligence Center, CareSource, LexisNexis, Cargill technology "
                    "operations, and regional defense contractors."
                ),
            },
            {
                "topic": "Best-Fit Profile",
                "content": (
                    "Wright State may be a good fit for students seeking a traditional public "
                    "university environment, applied research, regional defense/aerospace "
                    "opportunities, and a value-conscious graduate pathway."
                ),
            },
            {
                "topic": "Not Best Fit",
                "content": (
                    "Wright State may not be the best primary fit for students who mainly want "
                    "elite research branding, a top-10 CS signal, or a heavily Silicon Valley-oriented network."
                ),
            },
        ],
    },
    {
        "id": "franklin_cs",
        "name": "Franklin University — M.S. Computer Science",
        "agent_name": "Franklin",
        "location": "Columbus, Ohio / Online",
        "tagline": "Flexible, career-focused MSCS pathways.",
        "description": (
            "Franklin University offers M.S. in Computer Science program pathways "
            "designed around practical software, computing, and technology leadership "
            "skills, built for working professionals and students who need a flexible, "
            "career-focused graduate pathway rather than a prestige-only recommendation."
        ),
        "scrape_urls": [
            "https://www.franklin.edu/degrees/masters/computer-science-programs",
            "https://www.franklin.edu/degrees/masters/computer-science-programs/computer-science",
            "https://www.franklin.edu/degrees/masters/computer-science-programs/cybersecurity",
            "https://www.franklin.edu/degrees/masters/computer-science-programs/data-analytics",
            "https://www.franklin.edu/degrees/masters/computer-science-programs/software-systems",
            "https://www.franklin.edu/degrees/masters/computer-science-programs/non-computer-science-background",
            "https://www.franklin.edu/admissions/international-students/study-in-the-us",
        ],
        "tone_descriptors": ["practical", "career-focused", "flexible but not casual", "honest about limitations"],
        "best_fit_notes": (
            "Students prioritizing flexibility, online study, practical software skills, "
            "career advancement, or transition into CS from a related/non-CS background."
        ),
        "not_best_fit_notes": (
            "Students seeking a research-heavy thesis program, elite AI lab placement, PhD "
            "pipeline, or prestige-ranked CS environment."
        ),
        "communication_style_notes": (
            "Explain Franklin as a fit option, not as a prestige substitute. Compare Franklin "
            "honestly against traditional public universities."
        ),
        "never_do_notes": "Treat online flexibility as a weakness or as a guarantee of easy completion.",
        "key_facts_seed": [
            {
                "topic": "Program Overview",
                "content": (
                    "Franklin University offers M.S. in Computer Science program pathways "
                    "designed around practical software, computing, and technology leadership skills."
                ),
            },
            {
                "topic": "Online Format",
                "content": (
                    "Franklin describes its M.S. in Computer Science program family as online "
                    "and designed to fit busy adult learners and working professionals."
                ),
            },
            {
                "topic": "Completion Time",
                "content": (
                    "Franklin states that the M.S. in Computer Science can be completed in as "
                    "few as 20 months, depending on program path and student circumstances."
                ),
            },
            {
                "topic": "Focus Areas",
                "content": (
                    "Franklin's M.S. in Computer Science program family includes optional focus "
                    "areas such as Cybersecurity, Data Analytics, and Software Systems."
                ),
            },
            {
                "topic": "Practical Tools",
                "content": (
                    "Franklin highlights hands-on exposure to tools and technologies such as "
                    "SQL/MariaDB, MongoDB, Java, Java EE, and Git in the M.S. Computer Science program."
                ),
            },
            {
                "topic": "Tuition",
                "content": (
                    "Franklin lists M.S. Computer Science tuition as a per-credit-hour rate; "
                    "students should verify the latest tuition rate on the official page before deciding."
                ),
            },
            {
                "topic": "Non-CS Pathway",
                "content": (
                    "Franklin offers a pathway for students without a computer science undergraduate "
                    "background, using foundational/corequisite courses before or alongside master's-level work."
                ),
            },
            {
                "topic": "GRE/GMAT",
                "content": (
                    "Franklin's M.S. in Computer Science page states GMAT/GRE is not required "
                    "for admission. Students should verify this on the current official page before applying."
                ),
            },
            {
                "topic": "International Admissions",
                "content": (
                    "Franklin's international admissions process includes an online application, "
                    "transcripts, English proficiency evidence when required, and other documentation. "
                    "International students should verify modality, visa eligibility, and current requirements directly with Franklin."
                ),
            },
            {
                "topic": "Best-Fit Profile",
                "content": (
                    "Franklin may be a good fit for students prioritizing flexibility, online study, "
                    "practical software skills, career advancement, or transition into CS from a related/non-CS background."
                ),
            },
            {
                "topic": "Not Best Fit",
                "content": (
                    "Franklin may not be the strongest primary option for students seeking a "
                    "research-heavy thesis program, elite AI lab placement, PhD pipeline, or prestige-ranked CS environment."
                ),
            },
        ],
    },
]


def seed_legacy_universities(apps, schema_editor):
    University = apps.get_model("universities", "University")
    UniversityKnowledgeEntry = apps.get_model("django_api", "UniversityKnowledgeEntry")

    for entry in LEGACY_UNIVERSITIES:
        key_facts_seed = entry["key_facts_seed"]

        University.objects.get_or_create(
            id=entry["id"],
            defaults={
                "name": entry["name"],
                "agent_name": entry["agent_name"],
                "location": entry["location"],
                "tagline": entry["tagline"],
                "description": entry["description"],
                "scrape_urls": entry["scrape_urls"],
                "tone_descriptors": entry["tone_descriptors"],
                "best_fit_notes": entry["best_fit_notes"],
                "not_best_fit_notes": entry["not_best_fit_notes"],
                "communication_style_notes": entry["communication_style_notes"],
                "never_do_notes": entry["never_do_notes"],
            },
        )

        for fact in key_facts_seed:
            UniversityKnowledgeEntry.objects.get_or_create(
                university_id=entry["id"],
                topic=fact["topic"],
                content=fact["content"],
                defaults={"source_type": "seed", "confidence": 1.0},
            )


def reverse_seed_legacy_universities(apps, schema_editor):
    University = apps.get_model("universities", "University")
    University.objects.filter(id__in=[entry["id"] for entry in LEGACY_UNIVERSITIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("universities", "0001_initial"),
        ("django_api", "0005_agent_identity"),
    ]

    operations = [
        migrations.RunPython(seed_legacy_universities, reverse_seed_legacy_universities),
    ]
