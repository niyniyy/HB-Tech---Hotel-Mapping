from rapidfuzz import fuzz
from normalizer import normalize_hotel_name

def name_similarity(name1: str, name2: str) -> float:
    normalized_1 = normalize_hotel_name(name1)
    normalized_2 = normalize_hotel_name(name2)

    return fuzz.ratio(normalized_1, normalized_2)

if __name__ == "__main__":
    hotel_1 = "The Leela Palace Chennai"
    hotel_2 = "Leela Palace Chennai"

    score = name_similarity(hotel_1, hotel_2)

    print("Hotel 1:", hotel_1)
    print("Hotel 2:", hotel_2)
    print("Similarity Score:", score)  


from rapidfuzz import fuzz


def calculate_name_score(master_normalized_name, supplier_normalized_name):
    """
    Calculate hotel name similarity using RapidFuzz token_sort_ratio.

    Returns:
        name_similarity: similarity percentage from 0 to 100
        name_score: project score mapped from 0 to 35
    """

    if not master_normalized_name or not supplier_normalized_name:
        return {
            "name_similarity": 0.0,
            "name_score": 0.0
        }

    name_similarity = fuzz.token_sort_ratio(
        str(master_normalized_name),
        str(supplier_normalized_name)
    )

    name_score = (name_similarity / 100) * 35

    return {
        "name_similarity": round(name_similarity, 2),
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
            "aditya park sarovar portico hyderabad",
            "aditya hometel hyderabad"
        )
    ]

    for master_name, supplier_name in examples:
        print("Master:", master_name)
        print("Supplier:", supplier_name)
        print(calculate_name_score(master_name, supplier_name))
        print("-" * 60)
