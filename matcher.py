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
