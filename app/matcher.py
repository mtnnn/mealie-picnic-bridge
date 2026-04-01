import logging
import re

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Matches leading quantity patterns like "1 kg ", "310 g ", "2.5 l ", "100 ml "
_LEADING_QTY_RE = re.compile(
    r"^\d+(?:[.,]\d+)?\s*(?:kg|g|gr|gram|ml|l|liter|cl|dl|el|tl|stuks?|stuk|plakken|plakjes|sneetjes)\s+",
    re.IGNORECASE,
)


def parse_ingredient_name(
    display: str | None, note: str | None, food_name: str | None
) -> str:
    # Prefer Mealie's structured food name (already clean)
    if food_name:
        return _strip_leading_quantity(food_name)

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

    return _strip_leading_quantity(text.strip()) or "unknown"


def _strip_leading_quantity(name: str) -> str:
    """Remove leading quantity+unit from an ingredient name."""
    cleaned = _LEADING_QTY_RE.sub("", name).strip()
    if cleaned:
        return cleaned
    return name


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
