from config import settings

# A mapping a human explicitly made or approved is published whatever its tier —
# the gate exists to hold decisions nobody has checked, and a reviewer's decision
# has been checked by definition.
HUMAN_MAPPING_TYPES = ("NEW_MASTER", "MANUAL_NEW_MASTER", "MANUAL")


def publish_tiers() -> tuple[str, ...]:
    return tuple(
        tier.strip().upper()
        for tier in settings.PUBLISH_TIERS.split(",")
        if tier.strip()
    )


def publishable_sql(alias: str = "hm") -> str:
    """
    SQL predicate for "this mapping may reach a consumer".

    Kept as one function rather than repeated in each query, because a gate that
    is enforced in the export but not the API is not a gate. Anything serving
    mappings outward composes this.

    Withheld does not mean wrong — it means unverified. TIER3/TIER4 auto-matches
    stay in the database and keep attracting later records, so holding them costs
    nothing in recall; they are simply not asserted to anyone until a person
    confirms them.
    """
    tiers = ", ".join(f"'{tier}'" for tier in publish_tiers())
    types = ", ".join(f"'{name}'" for name in HUMAN_MAPPING_TYPES)

    return (
        f"({alias}.mapping_type IN ({types})"
        f" OR {alias}.is_manual_verified = TRUE"
        f" OR {alias}.confidence_tier IN ({tiers}))"
    )
