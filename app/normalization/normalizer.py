import re

STOP_WORDS = {
    "hotel", "the", "resort", "spa", "suites",
    "suite", "apartments", "apartment", "by"
}

def normalize_hotel_name(name: str) -> str:
    if not name:
        return ""

    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)

    words = [
        word for word in name.split()
        if word not in STOP_WORDS
    ]

    return " ".join(words)