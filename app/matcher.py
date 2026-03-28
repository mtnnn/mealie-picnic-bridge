import json
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


def llm_match(
    ingredient_name: str,
    quantity: float,
    unit: str | None,
    products: list[dict],
    api_key: str,
) -> tuple[dict, float] | None:
    """Use an LLM to pick the best Picnic product, considering both name and amount."""
    if not products:
        return None

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    amount_str = f"{quantity} {unit}".strip() if unit else str(quantity)
    product_list = [
        {
            "index": i,
            "name": p.get("name", ""),
            "unit_quantity": p.get("unit_quantity", ""),
        }
        for i, p in enumerate(products)
    ]

    prompt = (
        f"You are helping match a grocery ingredient to a product in an online supermarket.\n\n"
        f"Ingredient needed: {ingredient_name}\n"
        f"Amount needed: {amount_str}\n\n"
        f"Available products:\n{json.dumps(product_list, ensure_ascii=False, indent=2)}\n\n"
        f"Pick the product index that best matches the ingredient. Consider:\n"
        f"1. The ingredient name (most important)\n"
        f"2. The package size/amount closest to what is needed\n\n"
        f'Respond with ONLY a JSON object: {{"index": <number>, "reason": "<brief reason>"}}\n'
        f'If no product is a good match, respond: {{"index": null, "reason": "<why>"}}'
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        result = json.loads(response.content[0].text)
        idx = result.get("index")
        if idx is None or not (0 <= int(idx) < len(products)):
            logger.info("LLM returned no match for '%s': %s", ingredient_name, result.get("reason"))
            return None
        chosen = products[int(idx)]
        logger.info(
            "LLM matched '%s' (%s) → '%s': %s",
            ingredient_name,
            amount_str,
            chosen.get("name"),
            result.get("reason"),
        )
        return chosen, 100.0
    except Exception:
        logger.warning("LLM match failed for '%s', falling back to fuzzy", ingredient_name, exc_info=True)
        return None
