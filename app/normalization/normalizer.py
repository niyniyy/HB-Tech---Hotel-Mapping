import re

from rapidfuzz import fuzz

# Only truly generic words are stripped. Words like "residency", "grand",
# "premier" and "palace" ARE distinguishing in Indian hotel names and are kept.
STOP_WORDS = {
    "hotel", "hotels", "the", "resort", "resorts", "spa", "suites",
    "suite", "apartments", "apartment", "by", "a", "an", "and", "of", "inn",
}

# Chain marketing boilerplate that carries no identifying information.
#
# The affiliation suffix is the single most common cause of one hotel splitting
# into two masters: one supplier writes "Fortune Inn Haveli", another writes
# "Fortune Inn Haveli, Gandhinagar - Member ITC's Hotel Group", and the extra
# words break both the name match and its length-sensitive strict score. So
# `member ...` and `part of ...` are stripped wholesale rather than matched
# variant by variant — a hotel name almost never contains those words except as
# chain boilerplate, and the earlier narrow `member itc hotels.*` missed the
# apostrophe-and-"Group" form that GRN actually sends.
MARKETING_PATTERNS = [
    r"a member of .*",
    r"\bmember\b.*",
    r"\bpart of\b.*",
    r"an? accor brand",
    r"an? ihg hotel",
    r"by ihg",
    r"ihcl seleqtions",
    r"a luxury collection hotel.*",
    r"series by marriott",
    r"a sarovar (hotel|portico).*",
    r"affiliated.*",
    r"previously .*",
    r"\(.*?\)",
]

_ORDINAL = re.compile(r"^\d+$")

# Words that name a *different product at the same address*.
#
# Chains routinely run two brands out of one building — Red Fox beside Lemon
# Tree, Blu Towers beside Blu, Golden Tulip Essential beside Golden Tulip. Every
# other signal says these are one hotel: same street, same coordinates, and one
# name is a clean superset of the other, which the check below deliberately
# tolerates because "Wow" and "Wow Crest" usually are the same place.
#
# Measured against the reference mapping, these accounted for every confirmed
# false merge in the run: 8 of 8. Geography cannot separate them and fuzzy
# matching actively rewards the overlap, so the distinguishing word has to be
# named explicitly.
#
# Kept deliberately short. Each entry means "X" and "X <word>" can never merge,
# so a word that suppliers use inconsistently for one hotel would cost recall.
# Add only with a measurement to justify it.
BRAND_TIER_TOKENS = {
    "fox",
    "towers",
    "tower",
    "regency",
    "essential",
    "premier",
    "collection",
    "select",
    "express",
    "signature",
}


def normalize_hotel_name(name: str) -> str:
    """Lowercase, strip punctuation and generic words."""
    if not name:
        return ""

    text = _strip_boilerplate(name.lower())

    words = [
        word for word in text.split()
        if word not in STOP_WORDS
    ]

    return " ".join(words)


def _strip_boilerplate(text: str) -> str:
    for pattern in MARKETING_PATTERNS:
        text = re.sub(pattern, " ", text)

    return re.sub(r"[^a-z0-9\s]", " ", text)


def core_hotel_name(name: str, *geo_values) -> str:
    """
    Reduce a hotel name to the part that actually identifies the property.

    Suppliers routinely embed the city in the hotel name, which inflates fuzzy
    similarity between unrelated hotels — "Vivanta Coimbatore" scores 71% against
    "WelcomHotel Coimbatore" purely on the shared city token. Because the city is
    already known from its own column, it is removed here so that only the
    distinguishing part of the name is compared.

    Never reduces a name to nothing: if stripping would empty it, the full
    normalized name is returned instead.

    Accepts any number of geo values. When comparing two records, pass the
    city and state of BOTH so the same tokens are removed from each side — see
    compare_hotel_names for why that matters.
    """
    if not name:
        return ""

    text = _strip_boilerplate(name.lower())

    dropped = set(STOP_WORDS)

    for geo_value in geo_values:
        if geo_value:
            geo_text = re.sub(r"[^a-z0-9\s]", " ", str(geo_value).lower())
            dropped.update(geo_text.split())

    words = [
        word for word in text.split()
        if word not in dropped and len(word) > 1
    ]

    if not words:
        words = [
            word for word in text.split()
            if len(word) > 1
        ]

    return " ".join(words)


def _ordinal_tokens(name: str) -> set:
    """Numeric tokens such as the 1/2 in 'Lemon Tree Premier 1'."""
    if not name:
        return set()

    return {
        word for word in _strip_boilerplate(name.lower()).split()
        if _ORDINAL.match(word)
    }


def compare_hotel_names(
    master_name: str,
    supplier_name: str,
    master_city: str = None,
    supplier_city: str = None,
    master_state: str = None,
    supplier_state: str = None,
):
    """
    Compare two hotel names and return (loose_similarity, strict_similarity).

    loose  — token_set_ratio. Forgiving about extra words, but treats a subset as
             a perfect match: "The Residency" scores 100 against "The Residency
             Towers", which are two different Chennai hotels.
    strict — token_sort_ratio. Length sensitive, so those extra tokens count
             against the match. Required for matches at longer distances.

    Returns (0, 0) when the names carry conflicting numbers, e.g. "Leisure
    Valley 1" against "Leisure Valley 2".

    Both names are stripped of the SAME token set — the city and state of both
    records — because suppliers disagree about which is which. Two records for
    "Taj Kumarakom Resort & Spa, Kerala" filed one under city Kumarakom and the
    other under city Kottayam / state Kerala reduced to "taj kerala" against
    "taj kumarakom": byte-identical names scoring 0% similarity, because each
    side had a different token removed. Stripping the union leaves "taj" on
    both, and the distance tiers decide from there.
    """
    geo_values = (master_city, supplier_city, master_state, supplier_state)

    master_core = core_hotel_name(master_name, *geo_values)
    supplier_core = core_hotel_name(supplier_name, *geo_values)

    if not master_core or not supplier_core:
        return 0.0, 0.0

    master_ordinals = _ordinal_tokens(master_name)
    supplier_ordinals = _ordinal_tokens(supplier_name)

    if master_ordinals and supplier_ordinals and master_ordinals != supplier_ordinals:
        return 0.0, 0.0

    if not _distinguishing_parts_agree(master_core, supplier_core):
        return 0.0, 0.0

    strict = fuzz.token_sort_ratio(master_core, supplier_core)
    loose = max(fuzz.token_set_ratio(master_core, supplier_core), strict)

    return round(loose, 2), round(strict, 2)


def _distinguishing_parts_agree(master_core, supplier_core, min_similarity=50):
    """
    Compare what is left after removing the words the two names share.

    Shared locality words inflate similarity between unrelated hotels the same
    way city names do — "Namah Resort Jim Corbett" and "Voco Jim Corbett" agree
    on two of three tokens purely because both sit in Jim Corbett. Stripping the
    common words leaves "namah" against "voco", which plainly disagree.

    Only applies when BOTH names retain distinguishing words. When one name is
    simply a longer form of the other ("Wow" vs "Wow Crest") there is nothing to
    contradict, and the comparison is left to the caller's distance tiers.
    """
    master_tokens = set(master_core.split())
    supplier_tokens = set(supplier_core.split())

    common = master_tokens & supplier_tokens

    master_only = master_tokens - common
    supplier_only = supplier_tokens - common

    # Checked before the superset case below, which is precisely the hole these
    # fall through: "Golden Tulip" against "Golden Tulip Essential" leaves one
    # side with nothing to contradict, so it was treated as the same hotel
    # written at two lengths.
    if (master_only | supplier_only) & BRAND_TIER_TOKENS:
        return False

    if not master_only or not supplier_only:
        return True

    leftover_similarity = fuzz.token_sort_ratio(
        " ".join(sorted(master_only)),
        " ".join(sorted(supplier_only)),
    )

    return leftover_similarity >= min_similarity
