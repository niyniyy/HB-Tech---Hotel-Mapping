from rapidfuzz import fuzz
from app.normalization.normalizer import normalize_hotel_name


def calculate_geo_score(distance_meters):
    """
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


def calculate_name_score(master_normalized_name, supplier_normalized_name):
    """
    Name similarity uses RapidFuzz token_sort_ratio.
    Name score is mapped to 0-35.
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

    name_score = (similarity / 100) * 35

    return {
        "name_similarity": round(similarity, 2),
        "name_score": round(name_score, 2)
    }


def calculate_address_score(master_address, supplier_address):
    """
    Address similarity uses RapidFuzz token_sort_ratio.
    Address score is mapped to 0-15.
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

    address_score = (similarity / 100) * 15

    return {
        "address_similarity": round(similarity, 2),
        "address_score": round(address_score, 2)
    }


def calculate_star_score(master_star_rating, supplier_star_rating):
    """
    Star rating score is mapped to 0-5.
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
    Currently returns 0 if chain data is missing.
    """
    if not master_chain_name or not supplier_chain_name:
        return 0.0

    if str(master_chain_name).strip().lower() == str(supplier_chain_name).strip().lower():
        return 5.0

    return 0.0


def get_match_decision(final_score, geo_score=None, name_score=None):
    """
    Decide mapping action based on final score.

    Additional safeguard:
    If geo match is very strong and name score is reasonably high,
    route to manual review instead of creating a new master hotel.
    """

    if final_score >= 90:
        return "AUTO_MATCH"

    if final_score >= 75:
        return "MANUAL_REVIEW"

    if geo_score == 40.0 and name_score is not None and name_score >= 25:
        return "MANUAL_REVIEW"

    return "CREATE_NEW_MASTER"


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
    Full rule-based score:
    Geo: 40
    Name: 35
    Address: 15
    Star: 5
    Chain: 5
    Total: 100
    """

    geo_score = calculate_geo_score(distance_meters)

    name_result = calculate_name_score(
        master_normalized_name,
        supplier_normalized_name
    )

    address_result = calculate_address_score(
        master_address,
        supplier_address
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

    print("\n--- Full Rule-Based Score Test ---")

    full_score_result = calculate_rule_based_score(
        distance_meters=85.2,
        master_normalized_name="aditya park sarovar portico hyderabad",
        supplier_normalized_name="aditya park a sarovar portico hotel",
        master_address="ameerpet hyderabad india",
        supplier_address="ameerpet hyderabad telangana india",
        master_star_rating=4,
        supplier_star_rating=4,
        master_chain_name=None,
        supplier_chain_name=None
    )

    print(full_score_result)