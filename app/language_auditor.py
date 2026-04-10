from __future__ import annotations

import json
import logging

import anthropic
from openai import AsyncOpenAI

from app.audit_models import (
    IngredientTranslation,
    RecipeLanguageAudit,
    TranslationFixProposal,
)

logger = logging.getLogger(__name__)

DETECT_SYSTEM_PROMPT = """\
You are a language identification expert. For each recipe, identify the primary \
language of the text. Return the ISO 639-1 language code (e.g., "nl", "en", "de", \
"fr", "it", "es"). Analyze the recipe name and instruction steps to determine \
the language. If text is mixed-language, identify the dominant language.\
"""

DETECT_TOOL = {
    "name": "submit_languages",
    "description": "Submit the detected language for each recipe.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "language": {
                            "type": "string",
                            "description": "ISO 639-1 language code",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence 0.0 to 1.0",
                        },
                    },
                    "required": ["index", "language", "confidence"],
                },
            },
        },
        "required": ["results"],
    },
}

TRANSLATE_SYSTEM_PROMPT = """\
You are a professional recipe translator. Translate the recipe from {source_lang} \
to {target_lang}. Maintain cooking terminology accuracy.

RECIPE NAME & DESCRIPTION: Translate fully.
STEPS: Translate fully, keep exact measurements unchanged (e.g. "180°C" stays "180°C").
INGREDIENT FOOD NAMES: You receive ONLY the food name (e.g. "all-purpose flour", \
"salt", "Panko breadcrumbs"). Translate ONLY the food name to the target language \
(e.g. "bloem", "zout", "panko paneermeel"). Do NOT add quantities, units, or modifiers \
that were not in the original food name. Keep it short — just the food name.\
"""

TRANSLATE_TOOL = {
    "name": "submit_translation",
    "description": "Submit the translated recipe.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Translated recipe name"},
            "description": {
                "type": ["string", "null"],
                "description": "Translated description",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Translated instruction steps in order",
            },
            "ingredient_names": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "original": {"type": "string"},
                        "translated": {"type": "string"},
                    },
                    "required": ["index", "original", "translated"],
                },
            },
        },
        "required": ["name", "steps", "ingredient_names"],
    },
}

# OpenAI equivalents for tool schemas
DETECT_TOOL_OAI = {
    "type": "function",
    "function": {
        "name": DETECT_TOOL["name"],
        "description": DETECT_TOOL["description"],
        "parameters": DETECT_TOOL["input_schema"],
    },
}

TRANSLATE_TOOL_OAI = {
    "type": "function",
    "function": {
        "name": TRANSLATE_TOOL["name"],
        "description": TRANSLATE_TOOL["description"],
        "parameters": TRANSLATE_TOOL["input_schema"],
    },
}

LANG_BATCH_SIZE = 30


class LanguageAuditor:
    def __init__(
        self,
        provider: str = "anthropic",
        anthropic_api_key: str = "",
        openai_api_key: str = "",
        model: str = "",
    ) -> None:
        self.provider = provider
        self._anthropic: anthropic.AsyncAnthropic | None = None
        self._openai: AsyncOpenAI | None = None

        if provider == "anthropic" and anthropic_api_key:
            self._anthropic = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
            self.model = model or "claude-haiku-4-5-20251001"
        elif provider == "openai" and openai_api_key:
            self._openai = AsyncOpenAI(api_key=openai_api_key)
            self.model = model or "gpt-4o-mini"
        else:
            raise ValueError(
                f"Provider '{provider}' requires a valid API key"
            )

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    async def detect_batch(
        self, recipes: list[dict], target_language: str
    ) -> list[RecipeLanguageAudit]:
        all_results: list[RecipeLanguageAudit] = []

        for batch_start in range(0, len(recipes), LANG_BATCH_SIZE):
            batch = recipes[batch_start : batch_start + LANG_BATCH_SIZE]
            items = []
            for i, recipe in enumerate(batch):
                steps = recipe.get("recipeInstructions", [])
                step_texts = [
                    s.get("text", "")
                    for s in steps[:2]
                    if s.get("text")
                ]
                items.append({
                    "index": i,
                    "name": recipe.get("name", ""),
                    "steps": step_texts,
                })

            user_message = (
                "Detect the language of each recipe.\n\n"
                + json.dumps({"recipes": items}, ensure_ascii=False)
            )

            tool_result = await self._call_tool(
                system=DETECT_SYSTEM_PROMPT,
                user_message=user_message,
                tool_name="submit_languages",
                tool_anthropic=DETECT_TOOL,
                tool_openai=DETECT_TOOL_OAI,
            )

            detections: dict[int, tuple[str, float]] = {}
            for r in tool_result.get("results", []):
                detections[r["index"]] = (
                    r.get("language", "unknown"),
                    r.get("confidence", 0.0),
                )

            for i, recipe in enumerate(batch):
                lang, conf = detections.get(i, ("unknown", 0.0))
                all_results.append(RecipeLanguageAudit(
                    recipe_id=recipe["id"],
                    recipe_slug=recipe["slug"],
                    recipe_name=recipe["name"],
                    detected_language=lang,
                    target_language=target_language,
                    is_correct_language=lang == target_language,
                    confidence=conf,
                ))

        return all_results

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    async def translate_recipe(
        self,
        recipe: dict,
        source_lang: str,
        target_lang: str,
        mealie_client: object | None = None,
    ) -> TranslationFixProposal:
        steps = recipe.get("recipeInstructions", [])
        step_texts = [s.get("text", "") for s in steps if s.get("text")]
        ingredients = recipe.get("recipeIngredient", [])

        # Extract just food names, parsing unstructured ingredients if needed
        food_names = []
        unstructured_texts = []
        unstructured_indices = []
        for i, ing in enumerate(ingredients):
            food = ing.get("food")
            if food and food.get("name"):
                food_names.append({"index": i, "name": food["name"]})
            else:
                raw = ing.get("note") or ing.get("display") or ""
                if raw:
                    unstructured_texts.append(raw)
                    unstructured_indices.append(i)

        # Parse unstructured ingredients via Mealie to extract just food names
        if unstructured_texts and mealie_client:
            try:
                parsed = await mealie_client.parse_ingredients(unstructured_texts)
                for j, parsed_item in enumerate(parsed):
                    pi = parsed_item.get("ingredient", {})
                    parsed_food = pi.get("food")
                    parsed_note = pi.get("note") or ""
                    idx = unstructured_indices[j]
                    if parsed_food and parsed_food.get("name"):
                        name = parsed_food["name"]
                        # Append note as separate info if present (e.g. "divided")
                        if parsed_note:
                            food_names.append({
                                "index": idx,
                                "name": name,
                                "note": parsed_note,
                            })
                        else:
                            food_names.append({"index": idx, "name": name})
                    else:
                        # Parser couldn't extract food — send raw text as fallback
                        food_names.append({
                            "index": idx,
                            "name": unstructured_texts[j],
                        })
            except Exception:
                logger.warning("Mealie parser failed, using raw texts", exc_info=True)
                for j, idx in enumerate(unstructured_indices):
                    food_names.append({
                        "index": idx,
                        "name": unstructured_texts[j],
                    })
        elif unstructured_texts:
            # No mealie client — send raw texts
            for j, idx in enumerate(unstructured_indices):
                food_names.append({"index": idx, "name": unstructured_texts[j]})

        # Sort by index for consistent ordering
        food_names.sort(key=lambda x: x["index"])

        recipe_data = {
            "name": recipe.get("name", ""),
            "description": recipe.get("description") or "",
            "steps": step_texts,
            "ingredient_foods": food_names,
        }

        system = TRANSLATE_SYSTEM_PROMPT.format(
            source_lang=source_lang, target_lang=target_lang
        )
        user_message = (
            "Translate this recipe.\n\n"
            + json.dumps(recipe_data, ensure_ascii=False)
        )

        tool_result = await self._call_tool(
            system=system,
            user_message=user_message,
            tool_name="submit_translation",
            tool_anthropic=TRANSLATE_TOOL,
            tool_openai=TRANSLATE_TOOL_OAI,
        )

        ingredient_translations = []
        for item in tool_result.get("ingredient_names", []):
            ingredient_translations.append(IngredientTranslation(
                ingredient_index=item["index"],
                original_food_name=item.get("original", ""),
                translated_food_name=item.get("translated", ""),
            ))

        return TranslationFixProposal(
            recipe_slug=recipe["slug"],
            recipe_name=recipe["name"],
            source_language=source_lang,
            proposed_name=tool_result.get("name", recipe["name"]),
            proposed_description=tool_result.get("description"),
            proposed_steps=tool_result.get("steps", step_texts),
            ingredient_translations=ingredient_translations,
        )

    # ------------------------------------------------------------------
    # Provider abstraction
    # ------------------------------------------------------------------

    async def _call_tool(
        self,
        system: str,
        user_message: str,
        tool_name: str,
        tool_anthropic: dict,
        tool_openai: dict,
    ) -> dict:
        if self._anthropic:
            return await self._call_anthropic(
                system, user_message, tool_name, tool_anthropic
            )
        elif self._openai:
            return await self._call_openai(
                system, user_message, tool_name, tool_openai
            )
        else:
            raise RuntimeError("No LLM client configured")

    async def _call_anthropic(
        self,
        system: str,
        user_message: str,
        tool_name: str,
        tool: dict,
    ) -> dict:
        response = await self._anthropic.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_message}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input

        raise ValueError(f"No {tool_name} tool use in LLM response")

    async def _call_openai(
        self,
        system: str,
        user_message: str,
        tool_name: str,
        tool: dict,
    ) -> dict:
        response = await self._openai.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
        )

        for choice in response.choices:
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    if tc.function.name == tool_name:
                        return json.loads(tc.function.arguments)

        raise ValueError(f"No {tool_name} tool use in LLM response")
