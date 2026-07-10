import os

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")

# Use smaller output limit by default to reduce cost
DEFAULT_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "450"))

FIT_ASSESSMENT_MAX_TOKENS = int(os.getenv("FIT_ASSESSMENT_MAX_TOKENS", "600"))
RESUME_PARSE_MAX_TOKENS = int(os.getenv("RESUME_PARSE_MAX_TOKENS", "800"))
GITHUB_ASSESSMENT_MAX_TOKENS = int(os.getenv("GITHUB_ASSESSMENT_MAX_TOKENS", "700"))