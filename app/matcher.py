import logging

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


def parse_ingredient_name(
    display: str | None, note: str | None, food_name: str | None
) -> str:
    # Prefer Mealie's structured food name (already clean)
    if food_name:
        return food_name

    # Try ingredient-parser-nlp for unstructured text
    text = display or note or ""
    if text:
        try:
            from ingredient_parser import parse_ingredient

            result = parse_ingredient(text)
            if result.name and result.name.confidence > 0.5:
                return result.name.text
        except Exception:
            logger.debug("ingredient-parser failed for: %s", text)

    return text.strip() or "unknown"


def find_best_match(
    query: str, products: list[dict], threshold: int
) -> tuple[dict, float] | None:
    if not products:
        return None

    best_product = None
    best_score = 0.0

    for product in products:
        product_name = product.get("name", "")
        score = fuzz.token_set_ratio(query.lower(), product_name.lower())
        if score > best_score:
            best_score = score
            best_product = product

    if best_product and best_score >= threshold:
        logger.info(
            "Matched '%s' → '%s' (score: %.1f)",
            query,
            best_product.get("name"),
            best_score,
        )
        return best_product, best_score

    logger.info("No match for '%s' above threshold %d (best: %.1f)", query, threshold, best_score)
    return None
