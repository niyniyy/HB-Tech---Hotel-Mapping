from rapidfuzz import fuzz
from normalizer import normalize_hotel_name


def name_similarity(name1: str, name2: str) -> float:
    normalized_1 = normalize_hotel_name(name1)
    normalized_2 = normalize_hotel_name(name2)

    return fuzz.token_sort_ratio(normalized_1, normalized_2)


def calculate_name_score(master_normalized_name: str, supplier_normalized_name: str) -> dict:
    """
    Calculates hotel name similarity using RapidFuzz token_sort_ratio.
    Similarity is returned on a 0-100 scale.
    Name score is mapped to the project score range of 0-35.
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


if __name__ == "__main__":
    examples = [
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

    for master_name, supplier_name in examples:
        result = calculate_name_score(master_name, supplier_name)

        print("Master:", master_name)
        print("Supplier:", supplier_name)
        print("Result:", result)
        print("-" * 60)