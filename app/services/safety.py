import logging
import re

logger = logging.getLogger(__name__)

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+(?!patient)", re.IGNORECASE),
    re.compile(r"new\s+(role|persona|identity|instructions?)\s*[:=]", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a|an)\s+(?!patient|user)", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"\bDAN\b.*\bmode\b", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"bypass\s+(safety|content|filter|restrictions?)", re.IGNORECASE),
    re.compile(r"pretend\s+you\s+(are|have)\s+no\s+(restrictions?|limits?|rules?|filters?)", re.IGNORECASE),
    re.compile(r"(?:do\s+)?not\s+follow\s+(your|the|any)\s+(rules?|guidelines?|instructions?)", re.IGNORECASE),
    re.compile(r"override\s+(your|the|system)\s+(instructions?|prompt|rules?)", re.IGNORECASE),
    re.compile(r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions?|rules?)", re.IGNORECASE),
    re.compile(r"what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions?|rules?)", re.IGNORECASE),
]

# HTML/Markdown injection patterns
_MARKUP_PATTERNS: list[re.Pattern] = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"<iframe[\s>]", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"on\w+\s*=\s*[\"']", re.IGNORECASE),
    re.compile(r"<img[^>]+onerror", re.IGNORECASE),
]

INJECTION_REFUSAL = {
    "ru": "Я не могу выполнить этот запрос. Я — ассистент по здоровью. Пожалуйста, задайте вопрос, связанный со здоровьем.",
    "en": "I cannot process this request. I'm a health assistant. Please ask a health-related question.",
    "kk": "Мен бұл сұранысты орындай алмаймын. Мен денсаулық көмекшісімін. Денсаулыққа қатысты сұрақ қойыңыз.",
}


def detect_injection(message: str) -> bool:
    """Check if a message contains prompt injection patterns.

    Returns True if injection is detected.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(message):
            logger.warning("Prompt injection detected: pattern=%s", pattern.pattern)
            return True
    return False


def detect_markup_injection(message: str) -> bool:
    """Check for HTML/script injection in user input."""
    for pattern in _MARKUP_PATTERNS:
        if pattern.search(message):
            logger.warning("Markup injection detected: pattern=%s", pattern.pattern)
            return True
    return False


def sanitize_input(message: str) -> str:
    """Sanitize user input by removing dangerous markup while preserving medical content.

    Does NOT remove prompt injection text (that's handled by detect_injection).
    Focuses on removing executable markup that could affect rendering.
    """
    # Strip null bytes
    message = message.replace("\0", "")

    # Remove <script> and <iframe> tags with content
    message = re.sub(r"<script[^>]*>.*?</script>", "", message, flags=re.IGNORECASE | re.DOTALL)
    message = re.sub(r"<iframe[^>]*>.*?</iframe>", "", message, flags=re.IGNORECASE | re.DOTALL)

    # Remove standalone dangerous tags
    message = re.sub(r"<(script|iframe|object|embed|form|input|button)[^>]*>", "", message, flags=re.IGNORECASE)

    # Remove javascript: protocol
    message = re.sub(r"javascript\s*:", "", message, flags=re.IGNORECASE)

    # Collapse excessive whitespace (but preserve single newlines for readability)
    message = re.sub(r"[ \t]{10,}", " ", message)
    message = re.sub(r"\n{5,}", "\n\n\n", message)

    return message.strip()
