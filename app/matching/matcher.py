import re

from rapidfuzz import fuzz

from app.normalization.normalizer import (
    _ordinal_tokens,
    compare_hotel_names,
    core_hotel_name,
    normalize_hotel_name,
)

# A core name that reduces to one token is usually a bare chain word — "Taj
# Agra" in Agra becomes just "taj", which matches every other Taj property in
# the city. Such a name is not distinguishing on its own, so it may only match
# when the two hotels are effectively co-located.
SINGLE_TOKEN_MAX_METERS = 100.0

# Postal code is used as a penalty only, never a bonus.
#
# Measured on this data: when two records are the same hotel their postal codes
# agree 87.7% of the time; when they are different hotels within 300 m the codes
# still agree 74.8% of the time — a pincode covers a whole locality, so
# agreement carries almost no information. Disagreement does: it is roughly
# twice as likely between different hotels as between records for the same one.
#
# It is not a veto. 12.3% of genuinely-matching pairs disagree because of
# supplier data errors, so a mismatch tightens the distance requirement rather
# than blocking the match outright.
POSTAL_MISMATCH_PENALTY = 6.0
POSTAL_MISMATCH_MAX_METERS = 300.0

# ── Scoring weights ─────────────────────────────────────────────────────────
# Identity evidence (name + address) carries 65 of 100 points. Geography is a
# decaying contribution that confirms a name match rather than substituting for
# one — the previous design paid a flat 85 points for mere proximity, which made
# name similarity nearly irrelevant inside 1 km.
WEIGHT_NAME = 45
WEIGHT_ADDRESS = 20
WEIGHT_GEO = 25

# Replaces star and chain, which were unscoreable on this data rather than
# merely weak: `chain_name` is null on all 8,432 supplier records and
# `star_rating` on 6,878 of them — and the 1,554 that have one all come from a
# single supplier, so a cross-supplier pair could never have both sides
# populated. Together they made 10 of the nominal 100 points unreachable and
# quietly rescaled every threshold: AUTO_MATCH_MIN_SCORE of 40 was really 40 of
# an attainable 90.
#
# The two replacements were chosen by measuring, on this database, 11,395
# positive pairs (records the pipeline put under one master) against 36,028 hard
# negatives (records in *different* masters within 1 km — the pairs the matcher
# actually has to separate, not random ones):
#
#   feature                       m       u      m/u     bits
#   building number agrees      0.884   0.073   12.1x   3.60
#   rarest shared token df<=20  0.685   0.060   11.4x   3.51
#   postal code agrees          0.877   0.748    1.2x   0.23   (for comparison)
#
# Both carry roughly fifteen times the information of the postal code the
# scorer already trusts enough to penalise on.
WEIGHT_BUILDING = 5
WEIGHT_NAME_RARITY = 5

GEO_DECAY_METERS = 1000.0

# ── Distance tiers ──────────────────────────────────────────────────────────
# Distance tolerance scales with name confidence: a strong, length-sensitive
# name match earns geographic latitude, a weak one demands near co-location.
# (max_distance_meters, min_loose_similarity, min_strict_similarity)
DISTANCE_TIERS = [
    (1000.0, 95.0, 90.0),
    (500.0, 90.0, 85.0),
    (200.0, 80.0, 0.0),
]

# Composite score required in addition to the tier gate. Deliberately low — the
# tier gate is the decision, this only screens out candidates with nothing else
# going for them.
AUTO_MATCH_MIN_SCORE = 40.0

# Manual review band, applied only to candidates that clear the tier gate.
MANUAL_REVIEW_MIN_SCORE = 30.0

# ── Exact-name escalation ───────────────────────────────────────────────────
# Every tier above tops out at 1 km, and the candidate query only looks 1 km
# out, so two records for one hotel whose coordinates disagree by more than
# that were never compared at all — they became two masters in silence. Two
# "Mementos By ITC Hotels Jaipur" records sat 1608 m apart for exactly this
# reason: geocoders resolve an outskirts address to the locality centroid, and
# a kilometre of disagreement between suppliers is ordinary.
#
# An identical name at 1.6 km is not weak evidence of a different hotel. It is
# strong evidence of one hotel with a bad coordinate, contradicted by geography
# — and conflicting evidence is what a human is for. Below, an exact name can
# only ever turn CREATE_NEW_MASTER into MANUAL_REVIEW. It never lowers the bar
# for an automatic match: inside 1 km the existing tiers still decide alone.
#
# Two name keys, because neither subsumes the other (see the migration):
#   STRICT     the names agree with the city still in them
#   CORE_ONLY  they agree only after the city was stripped, which can leave a
#              bare chain name — "lemon tree premier" is several real hotels in
#              one city, so this is materially weaker and never auto-anything
EXACT_NAME_REVIEW_METERS = 5000.0
CORE_ONLY_NAME_REVIEW_METERS = 2000.0

EXACT_NAME_STRICT = "STRICT"
EXACT_NAME_CORE_ONLY = "CORE_ONLY"


def classify_exact_name(
    master_hotel_name,
    supplier_hotel_name,
    master_city=None,
    supplier_city=None,
    master_state=None,
    supplier_state=None,
):
    """
    How exactly do these two names agree? Returns STRICT, CORE_ONLY or None.

    Mirrors the two persisted blocking keys so the in-memory decision and the
    SQL that surfaced the candidate cannot disagree about what a name is.

    A conflicting number in the two names vetoes the whole path. "Leisure Valley
    1" and "Leisure Valley 2" are a statement that these are different
    properties; core_hotel_name drops single-character tokens, so both reduce to
    "leisure valley" and would otherwise look like an exact match. An escalation
    to review is not harmful here, but it wastes a person on a question the data
    already answered.
    """
    master_ordinals = _ordinal_tokens(master_hotel_name)
    supplier_ordinals = _ordinal_tokens(supplier_hotel_name)

    if master_ordinals and supplier_ordinals and master_ordinals != supplier_ordinals:
        return None

    master_strict = normalize_hotel_name(master_hotel_name)
    supplier_strict = normalize_hotel_name(supplier_hotel_name)

    if master_strict and master_strict == supplier_strict:
        return EXACT_NAME_STRICT

    geo_values = (master_city, supplier_city, master_state, supplier_state)

    master_core = core_hotel_name(master_hotel_name, *geo_values)
    supplier_core = core_hotel_name(supplier_hotel_name, *geo_values)

    if master_core and master_core == supplier_core:
        return EXACT_NAME_CORE_ONLY

    return None


def exact_name_review_limit(exact_name_class) -> float:
    """The distance beyond which an exact name stops being worth a human."""
    if exact_name_class == EXACT_NAME_STRICT:
        return EXACT_NAME_REVIEW_METERS

    if exact_name_class == EXACT_NAME_CORE_ONLY:
        return CORE_ONLY_NAME_REVIEW_METERS

    return 0.0


def calculate_address_score(master_address, supplier_address, max_score=WEIGHT_ADDRESS):
    if not master_address or not supplier_address:
        return {"address_similarity": 0.0, "address_score": 0.0}

    similarity = fuzz.token_set_ratio(
        str(master_address).lower(),
        str(supplier_address).lower()
    )

    return {
        "address_similarity": round(similarity, 2),
        "address_score": round((similarity / 100) * max_score, 2),
    }


_BUILDING_NUMBER = re.compile(r"\d+")


def building_number(address):
    """
    The first run of digits in an address — the house, plot or door number.

    Two hotels on one road differ in it; one hotel written five different ways
    usually does not. That asymmetry is what makes it worth scoring: measured
    here, it agrees on 88.4% of same-hotel pairs and only 7.3% of different
    hotels within a kilometre.

    Deliberately naive. "33/3 Avinashi Road" and "33, Avinashi Rd" both reduce
    to 33, which is the intent — the sub-unit rarely disagrees between suppliers
    describing one building, while the primary number rarely agrees between two
    different ones.
    """
    if not address:
        return None

    found = _BUILDING_NUMBER.search(str(address))

    if found is None:
        return None

    # "04" and "4" are the same door.
    return found.group(0).lstrip("0") or "0"


def calculate_building_score(master_address, supplier_address):
    """
    Agreement only. A missing number on either side scores nothing rather than
    penalising: 45% of pairs have no number to compare, and absence is a
    property of the supplier's formatting, not evidence about the hotel.

    Disagreement is in fact the stronger signal — it is ~12x more common between
    different hotels than the same one — but it is not made a penalty here.
    Indian addresses carry plot, survey and khasra numbers that legitimately
    differ between two renderings of one property, and a penalty would fire on
    those. Revisit once the ground-truth set can measure the cost.
    """
    master_number = building_number(master_address)
    supplier_number = building_number(supplier_address)

    if master_number is None or supplier_number is None:
        return 0.0

    return float(WEIGHT_BUILDING) if master_number == supplier_number else 0.0


# Fallback band edges for the rarity feature, used only when the caller supplies
# no corpus-derived cutoffs — a bare unit test, or a run before the token table
# has ever been built. In the live pipeline these are replaced by percentiles of
# the actual distribution (name_rarity_cutoffs), so a token counted as "rare"
# stays rare relative to the corpus however large it grows. Absolute counts
# here would silently misgrade at scale, which is the whole reason the cutoffs
# were moved into the database.
DEFAULT_RARITY_CUTOFFS = (5, 20, 100)


def calculate_name_rarity_score(rarest_shared_token_df, cutoffs=None):
    """
    How distinctive is the rarest word the two names share?

    Fuzzy similarity cannot tell "Coimbatore" from "Mamallaa" — both are just
    tokens that matched. But "hotel" appears in 2,138 records in this corpus and
    "radisson" in 486, while a hotel's actual identifying word appears in two or
    three. Sharing a rare word is near-proof; sharing only common ones is the
    exact trap that made "Lemon Tree Premier" match several different real
    hotels in one city.

    `rarest_shared_token_df` is the corpus document frequency of the rarest word
    the two names have in common, supplied by the caller because it needs the
    whole corpus. None means they share no word longer than two characters at
    all — true of 42% of different-hotel pairs and 0.1% of same-hotel pairs.

    `cutoffs` is (full, strong, weak) df edges, from the live distribution. A
    token at or below `full` is treated as fully distinctive, and so on. Passing
    None uses the fitting-corpus defaults.
    """
    if rarest_shared_token_df is None:
        return 0.0

    full, strong, weak = cutoffs or DEFAULT_RARITY_CUTOFFS

    if rarest_shared_token_df <= full:
        return float(WEIGHT_NAME_RARITY)

    if rarest_shared_token_df <= strong:
        return round(WEIGHT_NAME_RARITY * 0.6, 2)

    if rarest_shared_token_df <= weak:
        return round(WEIGHT_NAME_RARITY * 0.2, 2)

    return 0.0


def calculate_geo_score(distance_meters):
    """Decays linearly with distance. Never a flat constant."""
    if distance_meters is None:
        return 0.0

    try:
        distance_meters = float(distance_meters)
    except (TypeError, ValueError):
        return 0.0

    return round(WEIGHT_GEO * max(0.0, 1 - distance_meters / GEO_DECAY_METERS), 2)


def normalize_postal_code(value):
    """Digits only, so "110001.0" and "110001" compare equal."""
    if value is None:
        return None

    digits = "".join(character for character in str(value) if character.isdigit())

    return digits[:6] or None


def postal_codes_conflict(master_postal_code, supplier_postal_code) -> bool:
    """True only when both are present and differ."""
    master = normalize_postal_code(master_postal_code)
    supplier = normalize_postal_code(supplier_postal_code)

    if master is None or supplier is None:
        return False

    return master != supplier


def passes_distance_tier(
    distance_meters,
    loose_similarity,
    strict_similarity,
    min_core_tokens=2,
    postal_conflict=False,
):
    """
    The hard gate. A candidate must satisfy at least one tier to be eligible for
    AUTO_MATCH at all, regardless of its composite score.
    """
    if distance_meters is None:
        return False

    if min_core_tokens < 2 and distance_meters > SINGLE_TOKEN_MAX_METERS:
        return False

    # Conflicting postal codes do not block the match, but the hotels must then
    # be close enough that the conflict is explicable as a supplier error.
    if postal_conflict and distance_meters > POSTAL_MISMATCH_MAX_METERS:
        return False

    for max_distance, min_loose, min_strict in DISTANCE_TIERS:
        if (
            distance_meters <= max_distance
            and loose_similarity >= min_loose
            and strict_similarity >= min_strict
        ):
            return True

    return False


def confidence_tier(loose_similarity, strict_similarity, distance_meters):
    """
    Classify how much evidence backs a match, so downstream consumers can decide
    what to trust unattended.

    A false merge sends a guest to a property they did not book, so the tier is
    recorded on every mapping: TIER1/TIER2 are safe to publish automatically,
    TIER4 concentrates whatever residual risk exists and should be held for
    review before it reaches a booking path.
    """
    if distance_meters is None:
        return "TIER4"

    if loose_similarity >= 85 and strict_similarity >= 80 and distance_meters <= 100:
        return "TIER1"

    if loose_similarity >= 70 and distance_meters <= 200:
        return "TIER2"

    if loose_similarity >= 55 and distance_meters <= 500:
        return "TIER3"

    return "TIER4"


def get_match_decision(
    final_score,
    tier_passed,
    exact_name_class=None,
    distance_meters=None,
):
    """
    A candidate that fails the tier gate can never be auto-matched, no matter how
    high its composite score. This is what prevents proximity alone from
    producing a match.

    Failing the tier gate used to mean CREATE_NEW_MASTER outright. That conflates
    two different situations: evidence that is uniformly weak (genuinely a
    different hotel) and evidence that conflicts (an identical name, contradicted
    by distance). The first should become its own master. The second is the
    fragmentation case and goes to a human.
    """
    if not tier_passed:
        if (
            exact_name_class is not None
            and distance_meters is not None
            and distance_meters <= exact_name_review_limit(exact_name_class)
        ):
            return "MANUAL_REVIEW"

        return "CREATE_NEW_MASTER"

    if final_score >= AUTO_MATCH_MIN_SCORE:
        return "AUTO_MATCH"

    if final_score >= MANUAL_REVIEW_MIN_SCORE:
        return "MANUAL_REVIEW"

    return "CREATE_NEW_MASTER"


def calculate_rule_based_score(
    distance_meters,
    master_hotel_name,
    supplier_hotel_name,
    master_address,
    supplier_address,
    master_city=None,
    supplier_city=None,
    master_state=None,
    supplier_state=None,
    master_postal_code=None,
    supplier_postal_code=None,
    rarest_shared_token_df=None,
    rarity_cutoffs=None,
):
    """
    Score one master/supplier pair.

    Names are compared on their *core* form with the city stripped out, so that
    two unrelated hotels do not score highly merely because both names end in the
    same city name.

    `rarest_shared_token_df` is optional. Callers without the corpus statistics
    simply forgo that component rather than being unable to score at all, so the
    matcher stays runnable offline and in tests.
    """
    loose_similarity, strict_similarity = compare_hotel_names(
        master_hotel_name,
        supplier_hotel_name,
        master_city,
        supplier_city,
        master_state,
        supplier_state,
    )

    name_score = round((loose_similarity / 100) * WEIGHT_NAME, 2)

    address_result = calculate_address_score(master_address, supplier_address)
    geo_score = calculate_geo_score(distance_meters)
    building_score = calculate_building_score(master_address, supplier_address)
    name_rarity_score = calculate_name_rarity_score(
        rarest_shared_token_df, rarity_cutoffs
    )

    postal_conflict = postal_codes_conflict(
        master_postal_code,
        supplier_postal_code,
    )

    final_score = round(
        name_score
        + address_result["address_score"]
        + geo_score
        + building_score
        + name_rarity_score
        - (POSTAL_MISMATCH_PENALTY if postal_conflict else 0.0),
        2,
    )

    # Same symmetric stripping compare_hotel_names uses, so the token count and
    # the similarity score are talking about the same two strings.
    geo_values = (master_city, supplier_city, master_state, supplier_state)

    master_core = core_hotel_name(master_hotel_name, *geo_values)
    supplier_core = core_hotel_name(supplier_hotel_name, *geo_values)
    min_core_tokens = min(len(master_core.split()), len(supplier_core.split()))

    tier_passed = passes_distance_tier(
        distance_meters,
        loose_similarity,
        strict_similarity,
        min_core_tokens,
        postal_conflict,
    )

    exact_name_class = classify_exact_name(
        master_hotel_name,
        supplier_hotel_name,
        master_city,
        supplier_city,
        master_state,
        supplier_state,
    )

    return {
        "distance_meters": round(float(distance_meters), 2) if distance_meters is not None else None,
        "geo_score": geo_score,
        "name_similarity": loose_similarity,
        "name_similarity_strict": strict_similarity,
        "name_score": name_score,
        "address_similarity": address_result["address_similarity"],
        "address_score": address_result["address_score"],
        "building_score": building_score,
        "name_rarity_score": name_rarity_score,
        "tier_passed": tier_passed,
        "exact_name_class": exact_name_class,
        "confidence_tier": confidence_tier(
            loose_similarity,
            strict_similarity,
            distance_meters,
        ),
        "final_score": final_score,
        "decision": get_match_decision(
            final_score,
            tier_passed,
            exact_name_class,
            distance_meters,
        ),
    }
