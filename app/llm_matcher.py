import json
import logging
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Je bent een assistent voor boodschappen bij de Nederlandse online supermarkt Picnic.
Je taak is om kookingrediënten te matchen met de juiste supermarktproducten EN te bepalen \
hoeveel stuks er in het winkelmandje moeten.

BELANGRIJKE REGELS:
- Selecteer ALLEEN voedsel- en kookproducten. Selecteer NOOIT schoonmaakmiddelen, \
huishoudelijke producten of non-food artikelen, ook al lijkt de naam op het ingrediënt.
- Als geen enkel product een redelijke match is voor het kookingrediënt, geef dan null \
als selected_id voor dat item.
- Denk na vanuit een Nederlandse kookcontext.
- Voorbeeld: "citroen" als kookingrediënt betekent de vrucht citroen, \
niet een schoonmaakmiddel met citroengeur.
- Kies bij twijfel het meest basale/generieke product.

HOEVEELHEID BEPALEN:
- Kijk naar de benodigde hoeveelheid (amount) en de verpakkingsgrootte (unit_quantity) \
van het gekozen product.
- Bereken hoeveel stuks van het product nodig zijn om de benodigde hoeveelheid te dekken.
- Voorbeeld: recept vraagt "10 g basilicum", product is "Basilicum 15g" → quantity = 1
- Voorbeeld: recept vraagt "8 ansjovis", product is "Ansjovisfilets blik 45g" → quantity = 1 \
(één blikje bevat genoeg ansjovisfilets)
- Voorbeeld: recept vraagt "6 uien", product is "Uien 1 stuk" → quantity = 6
- Voorbeeld: recept vraagt "1 kg aardappelen", product is "Aardappelen 500g" → quantity = 2
- KRITIEK: Als een product GEEN unit_quantity heeft (dus verpakkingsgrootte onbekend), \
gebruik dan altijd quantity = 1. Gokt NOOIT een hoeveelheid op basis van een onbekende \
verpakkingsgrootte.
- Als de amount in gram/ml/kg/liter is, is de quantity bijna altijd 1 \
(je koopt één verpakking, niet meerdere).
- Als er geen amount is opgegeven, gebruik quantity = 1.\
"""

MATCH_TOOL = {
    "name": "submit_matches",
    "description": "Submit the matched products for each ingredient.",
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "The index of the ingredient item.",
                        },
                        "selected_id": {
                            "type": ["string", "null"],
                            "description": "The product ID of the best match, or null if no suitable product.",
                        },
                        "recommended_quantity": {
                            "type": "integer",
                            "description": "How many units of this product to add to the cart, based on the recipe amount and the product's package size. Minimum 1.",
                        },
                    },
                    "required": ["index", "selected_id", "recommended_quantity"],
                },
            },
        },
        "required": ["matches"],
    },
}


@dataclass
class MatchRequest:
    ingredient_name: str
    products: list[dict]
    quantity: float | None = None
    unit: str | None = None


@dataclass
class MatchResult:
    ingredient_name: str
    selected_product: dict | None
    recommended_quantity: int = 1


class LLMMatcher:
    def __init__(
        self, api_key: str, model: str, max_products_per_item: int
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_products_per_item = max_products_per_item

    async def match_batch(
        self, items: list[MatchRequest]
    ) -> list[MatchResult]:
        if not items:
            return []

        user_items = []
        for i, item in enumerate(items):
            candidates = []
            for p in item.products[: self.max_products_per_item]:
                candidate = {
                    "id": str(p.get("id", "")),
                    "name": p.get("name", ""),
                }
                unit_qty = p.get("unit_quantity", "")
                if unit_qty:
                    candidate["unit_quantity"] = unit_qty
                candidates.append(candidate)

            entry: dict = {
                "index": i,
                "ingredient": item.ingredient_name,
                "candidates": candidates,
            }
            if item.quantity is not None and item.unit:
                entry["amount"] = f"{item.quantity} {item.unit}"
            elif item.quantity is not None:
                entry["amount"] = str(item.quantity)
            user_items.append(entry)

        user_message = (
            "Match elk ingrediënt met het beste supermarktproduct.\n\n"
            + json.dumps({"items": user_items}, ensure_ascii=False)
        )

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=[MATCH_TOOL],
                tool_choice={"type": "tool", "name": "submit_matches"},
                messages=[{"role": "user", "content": user_message}],
            )
        except (anthropic.APIError, anthropic.APIConnectionError) as exc:
            logger.warning("LLM API call failed: %s", exc)
            raise

        # Extract tool use result
        tool_input = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_matches":
                tool_input = block.input
                break

        if tool_input is None:
            logger.warning("LLM response did not contain tool use")
            raise ValueError("No tool use in LLM response")

        logger.debug("LLM raw response: %s", json.dumps(tool_input, ensure_ascii=False))

        # Build a lookup of index -> (selected_id, recommended_quantity)
        selections: dict[int, tuple[str | None, int]] = {}
        for match in tool_input.get("matches", []):
            qty = match.get("recommended_quantity", 1)
            qty = max(1, qty) if isinstance(qty, int) else 1
            selected_id = match.get("selected_id")
            if isinstance(selected_id, str):
                selected_id = selected_id.strip()
            selections[match["index"]] = (selected_id, qty)

        # Map selections back to products
        results: list[MatchResult] = []
        for i, item in enumerate(items):
            selected_id, rec_qty = selections.get(i, (None, 1))
            selected_product = None
            if selected_id is not None:
                # Build lookup of candidate IDs for debug logging
                candidate_ids = [str(p.get("id", "")) for p in item.products[:self.max_products_per_item]]
                for p in item.products:
                    if str(p.get("id", "")) == selected_id:
                        selected_product = p
                        break
                if selected_product is None:
                    logger.warning(
                        "LLM selected unknown product ID '%s' for '%s' (candidates: %s)",
                        selected_id,
                        item.ingredient_name,
                        candidate_ids[:5],
                    )

            if selected_product:
                logger.info(
                    "LLM matched '%s' → '%s' (qty: %d)",
                    item.ingredient_name,
                    selected_product.get("name"),
                    rec_qty,
                )
            else:
                logger.info(
                    "LLM found no match for '%s'", item.ingredient_name
                )

            results.append(
                MatchResult(
                    ingredient_name=item.ingredient_name,
                    selected_product=selected_product,
                    recommended_quantity=rec_qty,
                )
            )

        return results
