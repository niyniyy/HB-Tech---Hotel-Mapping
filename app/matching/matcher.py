from rapidfuzz import fuzz
from app.normalization.normalizer import normalize_hotel_name


def calculate_name_score(master_normalized_name, supplier_normalized_name, max_score=35):
    """
    Name similarity uses RapidFuzz token_sort_ratio.
    Name score is mapped to max_score.
    """
    if not master_normalized_name or not supplier_normalized_name:
        return {
            "name_similarity": 0.0,
            "name_score": 0.0
        }

    similarity = fuzz.token_sort_ratio(
        str(master_normalized_name),
        str(supplier_normalized_name)
    )

    name_score = (similarity / 100) * max_score

    return {
        "name_similarity": round(similarity, 2),
        "name_score": round(name_score, 2)
    }


def calculate_address_score(master_address, supplier_address, max_score=15):
    """
    Address similarity uses RapidFuzz token_sort_ratio.
    Address score is mapped to max_score.
    """
    if not master_address or not supplier_address:
        return {
            "address_similarity": 0.0,
            "address_score": 0.0
        }

    similarity = fuzz.token_sort_ratio(
        str(master_address),
        str(supplier_address)
    )

    address_score = (similarity / 100) * max_score

    return {
        "address_similarity": round(similarity, 2),
        "address_score": round(address_score, 2)
    }


def calculate_star_score(master_star_rating, supplier_star_rating):
    """
    Star rating score is mapped to 0-5.
    Used only in fallback/default scoring.
    """
    if master_star_rating is None or supplier_star_rating is None:
        return 0.0

    try:
        master_star = float(master_star_rating)
        supplier_star = float(supplier_star_rating)
    except ValueError:
        return 0.0

    diff = abs(master_star - supplier_star)

    if diff == 0:
        return 5.0

    if diff <= 1:
        return 2.5

    return 0.0


def calculate_chain_score(master_chain_name=None, supplier_chain_name=None):
    """
    Chain score is mapped to 0-5.
    Used only in fallback/default scoring.
    """
    if not master_chain_name or not supplier_chain_name:
        return 0.0

    if str(master_chain_name).strip().lower() == str(supplier_chain_name).strip().lower():
        return 5.0

    return 0.0


def get_match_decision(final_score, geo_score=None, name_score=None):
    """
    Decide mapping action based on final score.
    """

    if final_score >= 90:
        return "AUTO_MATCH"

    if final_score >= 75:
        return "MANUAL_REVIEW"

    return "CREATE_NEW_MASTER"


def calculate_distance_based_geo_rule_score(
    distance_meters,
    master_normalized_name,
    supplier_normalized_name,
    master_address,
    supplier_address
):
    """
    New geo scoring logic based on distance.

    Case 1:
    If distance is exactly 0 meters:
    Geo Score = 95
    Hotel Name Similarity = 5
    Total possible = 100

    Case 2:
    If distance is <= 100 meters:
    Geo Score = 90
    Hotel Name Similarity = 7
    Address Similarity = 3
    Total possible = 100

    Case 3:
    If distance is <= 1000 meters:
    Geo Score = 85
    Hotel Name Similarity = 10
    Address Similarity = 5
    Total possible = 100

    If distance is above 1000 meters, return None and use fallback scoring.
    """

    if distance_meters is None:
        return None

    try:
        distance_meters = float(distance_meters)
    except (TypeError, ValueError):
        return None

    # Case 1: Exact latitude and longitude match / zero distance
    if distance_meters == 0:
        name_result = calculate_name_score(
            master_normalized_name,
            supplier_normalized_name,
            max_score=5
        )

        final_score = 95.0 + name_result["name_score"]
        final_score = min(100.0, round(final_score, 2))

        return {
            "geo_rule": "EXACT_DISTANCE_ZERO",
            "distance_meters": round(distance_meters, 2),
            "geo_score": 95.0,
            "name_similarity": name_result["name_similarity"],
            "name_score": name_result["name_score"],
            "address_similarity": 0.0,
            "address_score": 0.0,
            "star_score": 0.0,
            "chain_score": 0.0,
            "final_score": final_score,
            "decision": get_match_decision(
                final_score,
                95.0,
                name_result["name_score"]
            )
        }

    # Case 2: Equivalent to matching up to around 3 decimal places
    if distance_meters <= 100:
        name_result = calculate_name_score(
            master_normalized_name,
            supplier_normalized_name,
            max_score=7
        )

        address_result = calculate_address_score(
            master_address,
            supplier_address,
            max_score=3
        )

        final_score = (
            90.0
            + name_result["name_score"]
            + address_result["address_score"]
        )

        final_score = min(100.0, round(final_score, 2))

        return {
            "geo_rule": "WITHIN_100_METERS",
            "distance_meters": round(distance_meters, 2),
            "geo_score": 90.0,
            "name_similarity": name_result["name_similarity"],
            "name_score": name_result["name_score"],
            "address_similarity": address_result["address_similarity"],
            "address_score": address_result["address_score"],
            "star_score": 0.0,
            "chain_score": 0.0,
            "final_score": final_score,
            "decision": get_match_decision(
                final_score,
                90.0,
                name_result["name_score"]
            )
        }

    # Case 3: Equivalent to matching up to around 2 decimal places
    if distance_meters <= 1000:
        name_result = calculate_name_score(
            master_normalized_name,
            supplier_normalized_name,
            max_score=10
        )

        address_result = calculate_address_score(
            master_address,
            supplier_address,
            max_score=5
        )

        final_score = (
            85.0
            + name_result["name_score"]
            + address_result["address_score"]
        )

        final_score = min(100.0, round(final_score, 2))

        return {
            "geo_rule": "WITHIN_1000_METERS",
            "distance_meters": round(distance_meters, 2),
            "geo_score": 85.0,
            "name_similarity": name_result["name_similarity"],
            "name_score": name_result["name_score"],
            "address_similarity": address_result["address_similarity"],
            "address_score": address_result["address_score"],
            "star_score": 0.0,
            "chain_score": 0.0,
            "final_score": final_score,
            "decision": get_match_decision(
                final_score,
                85.0,
                name_result["name_score"]
            )
        }

    return None


def calculate_geo_score(distance_meters):
    """
    Fallback old geo score.
    Used only when distance is above 1000 meters.
    Geo score is mapped to 0-40.
    """
    if distance_meters is None:
        return 0.0

    distance_meters = float(distance_meters)

    if distance_meters <= 100:
        return 40.0

    if distance_meters <= 300:
        return 25.0

    return 10.0


def calculate_rule_based_score(
    distance_meters,
    master_normalized_name,
    supplier_normalized_name,
    master_address,
    supplier_address,
    master_star_rating=None,
    supplier_star_rating=None,
    master_chain_name=None,
    supplier_chain_name=None
):
    """
    Full rule-based score.

    New logic first:
    1. distance = 0 meters:
       Geo 95 + Name 5

    2. distance <= 100 meters:
       Geo 90 + Name 7 + Address 3

    3. distance <= 1000 meters:
       Geo 85 + Name 10 + Address 5

    If distance > 1000 meters:
    Use old fallback scoring:
    Geo 40 + Name 35 + Address 15 + Star 5 + Chain 5
    """

    new_geo_result = calculate_distance_based_geo_rule_score(
        distance_meters=distance_meters,
        master_normalized_name=master_normalized_name,
        supplier_normalized_name=supplier_normalized_name,
        master_address=master_address,
        supplier_address=supplier_address
    )

    if new_geo_result is not None:
        return new_geo_result

    # Fallback old scoring for hotels farther than 1000 meters
    geo_score = calculate_geo_score(distance_meters)

    name_result = calculate_name_score(
        master_normalized_name,
        supplier_normalized_name,
        max_score=35
    )

    address_result = calculate_address_score(
        master_address,
        supplier_address,
        max_score=15
    )

    star_score = calculate_star_score(
        master_star_rating,
        supplier_star_rating
    )

    chain_score = calculate_chain_score(
        master_chain_name,
        supplier_chain_name
    )

    final_score = (
        geo_score
        + name_result["name_score"]
        + address_result["address_score"]
        + star_score
        + chain_score
    )

    final_score = round(final_score, 2)

    return {
        "geo_rule": "FALLBACK_DISTANCE_BASED",
        "distance_meters": round(float(distance_meters), 2) if distance_meters is not None else None,
        "geo_score": geo_score,
        "name_similarity": name_result["name_similarity"],
        "name_score": name_result["name_score"],
        "address_similarity": address_result["address_similarity"],
        "address_score": address_result["address_score"],
        "star_score": star_score,
        "chain_score": chain_score,
        "final_score": final_score,
        "decision": get_match_decision(
            final_score,
            geo_score,
            name_result["name_score"]
        )
    }


if __name__ == "__main__":

    print("\n--- Name Similarity Tests ---")

    name_examples = [
        (
            "aditya park sarovar portico hyderabad",
            "aditya park hyderabad"
        ),
        (
            "aditya park sarovar portico hyderabad",
            "aditya park a sarovar portico hotel"
        ),
        (
            "the leela palace chennai",
            "leela palace chennai"
        )
    ]

    for master_name, supplier_name in name_examples:
        result = calculate_name_score(master_name, supplier_name)

        print("Master:", master_name)
        print("Supplier:", supplier_name)
        print("Result:", result)
        print("-" * 60)

    print("\n--- New Distance-Based Rule Tests ---")

    test_cases = [
        {
            "label": "Exact distance zero",
            "distance_meters": 0
        },
        {
            "label": "Within 100 meters",
            "distance_meters": 85.2
        },
        {
            "label": "Within 1000 meters",
            "distance_meters": 750
        },
        {
            "label": "Fallback above 1000 meters",
            "distance_meters": 1500
        }
    ]

    for case in test_cases:
        print(case["label"])

        result = calculate_rule_based_score(
            distance_meters=case["distance_meters"],
            master_normalized_name="aditya park sarovar portico hyderabad",
            supplier_normalized_name="aditya park a sarovar portico hotel",
            master_address="ameerpet hyderabad india",
            supplier_address="ameerpet hyderabad telangana india",
            master_star_rating=4,
            supplier_star_rating=4,
            master_chain_name=None,
            supplier_chain_name=None
        )

        print(result)
        print("-" * 60)