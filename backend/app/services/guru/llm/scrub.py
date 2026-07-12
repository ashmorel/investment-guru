import re

# Redact common API-key shapes from provider error text. Google's genai puts
# the FULL key in a ?key=... query param; OpenAI/Anthropic keys start with
# sk-; Google keys start with AIza. Mirrors app/api/admin.py's _KEY_PATTERNS.
_KEY_PATTERNS = [
    re.compile(r"key=[^\s&\"']+"),          # ?key=<value> query params (Google)
    re.compile(r"sk-[A-Za-z0-9_\-]+"),      # OpenAI / Anthropic secret keys
    re.compile(r"AIza[A-Za-z0-9_\-]+"),     # Google API keys
]


def scrub_secrets(text: str) -> str:
    """Redact API-key-shaped substrings from provider error text so a key
    can never reach logs or clients via an exception message."""
    for pat in _KEY_PATTERNS:
        text = pat.sub("***", text)
    return text
