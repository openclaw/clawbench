import re

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")


def normalize_title(text: str) -> str:
    cleaned = " ".join(text.split())
    cleaned = EMOJI_RE.sub("", cleaned)
    return cleaned.strip().title()


def normalize_tags(raw: str) -> list[str]:
    return [part.strip().lower() for part in raw.split(",") if part.strip()]

