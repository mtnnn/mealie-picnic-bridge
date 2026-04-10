from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from rapidfuzz import fuzz

from app.audit_models import (
    FixResult,
    FullAuditResult,
    IngredientFix,
    IngredientFixProposal,
    IngredientIssue,
    IngredientTranslation,
    RecipeAuditSummary,
    RecipeIngredientAudit,
    RecipeLanguageAudit,
    RecipeStepsAudit,
    TranslationFixProposal,
)
from app.mealie import MealieClient

logger = logging.getLogger(__name__)

FOOD_MATCH_THRESHOLD = 80


class AuditScanner:
    def __init__(
        self,
        mealie: MealieClient,
        mealie_host: str,
        language_auditor: object | None = None,
        target_language: str = "nl",
        parser: str = "nlp",
    ) -> None:
        self.mealie = mealie
        self.mealie_host = mealie_host.rstrip("/")
        self.language_auditor = language_auditor
        self.target_language = target_language
        self.parser = parser
        self._last_result: FullAuditResult | None = None
        self._batch_confirmations: dict[str, asyncio.Event] = {}
        self._batch_actions: dict[str, str] = {}

    @property
    def last_result(self) -> FullAuditResult | None:
        return self._last_result

    # ------------------------------------------------------------------
    # Full scan
    # ------------------------------------------------------------------

    async def scan_all(
        self, cancel: asyncio.Event | None = None
    ) -> AsyncGenerator[tuple[str, dict], None]:
        recipes_summary = await self.mealie.get_all_recipes()
        total = len(recipes_summary)
        yield "scan_start", {"total_recipes": total}

        all_summaries: list[RecipeAuditSummary] = []
        all_ingredient_issues: list[RecipeIngredientAudit] = []
        all_step_issues: list[RecipeStepsAudit] = []
        full_recipes: list[dict] = []

        for i, r_summary in enumerate(recipes_summary):
            if cancel and cancel.is_set():
                yield "scan_cancelled", {}
                return

            recipe = await self.mealie.get_recipe(r_summary["slug"])
            full_recipes.append(recipe)

            # Ingredient audit
            ing_issues = self._audit_ingredients(recipe)
            if ing_issues:
                all_ingredient_issues.append(RecipeIngredientAudit(
                    recipe_id=recipe["id"],
                    recipe_slug=recipe["slug"],
                    recipe_name=recipe["name"],
                    issues=ing_issues,
                ))

            # Steps audit
            steps_audit = self._audit_steps(recipe)
            if not steps_audit.has_instructions:
                all_step_issues.append(steps_audit)

            # Photo check
            has_photo = bool(r_summary.get("image"))
            image_url = (
                f"{self.mealie_host}/api/media/recipes/{recipe['id']}/images/original.webp"
                if has_photo else None
            )

            ingredients = recipe.get("recipeIngredient", [])
            summary = RecipeAuditSummary(
                recipe_id=recipe["id"],
                recipe_slug=recipe["slug"],
                recipe_name=recipe["name"],
                image_url=image_url,
                health_score=0,  # calculated after language detection
                has_photo=has_photo,
                has_instructions=steps_audit.has_instructions,
                instruction_count=steps_audit.instruction_count,
                ingredient_issue_count=len(ing_issues),
                total_ingredients=len(ingredients),
                is_correct_language=True,  # updated after language detection
                detected_language=None,
            )
            all_summaries.append(summary)

            yield "recipe_scanned", {
                "index": i,
                "slug": recipe["slug"],
                "name": recipe["name"],
                "has_photo": has_photo,
                "has_instructions": steps_audit.has_instructions,
                "ingredient_issues": len(ing_issues),
                "total_ingredients": len(ingredients),
            }

        # Batch language detection
        all_language_issues: list[RecipeLanguageAudit] = []
        if self.language_auditor and full_recipes:
            try:
                lang_results = await self.language_auditor.detect_batch(
                    full_recipes, self.target_language
                )
                for lang_audit, summary in zip(lang_results, all_summaries):
                    summary.is_correct_language = lang_audit.is_correct_language
                    summary.detected_language = lang_audit.detected_language
                    if not lang_audit.is_correct_language:
                        all_language_issues.append(lang_audit)
            except Exception:
                logger.warning("Language detection failed", exc_info=True)

        # Compute health scores
        photo_missing = 0
        for s in all_summaries:
            score = 0.0
            if s.has_photo:
                score += 25
            else:
                photo_missing += 1
            if s.has_instructions:
                score += 25
            if s.total_ingredients > 0:
                good = s.total_ingredients - s.ingredient_issue_count
                score += 25 * (good / s.total_ingredients)
            else:
                score += 25
            if s.is_correct_language:
                score += 25
            s.health_score = round(score, 1)

        overall = (
            round(sum(s.health_score for s in all_summaries) / len(all_summaries), 1)
            if all_summaries else 0.0
        )

        result = FullAuditResult(
            total_recipes=total,
            overall_health_score=overall,
            recipes=all_summaries,
            ingredient_issues=all_ingredient_issues,
            step_issues=all_step_issues,
            language_issues=all_language_issues,
            photo_missing_count=photo_missing,
            ingredient_issue_recipe_count=len(all_ingredient_issues),
            step_issue_recipe_count=len(all_step_issues),
            language_issue_recipe_count=len(all_language_issues),
        )
        self._last_result = result

        yield "scan_complete", {
            "total_recipes": total,
            "overall_health_score": overall,
            "photo_missing_count": photo_missing,
            "ingredient_issue_recipe_count": len(all_ingredient_issues),
            "step_issue_recipe_count": len(all_step_issues),
            "language_issue_recipe_count": len(all_language_issues),
        }

    # ------------------------------------------------------------------
    # Ingredient auditing
    # ------------------------------------------------------------------

    @staticmethod
    def _audit_ingredients(recipe: dict) -> list[IngredientIssue]:
        issues: list[IngredientIssue] = []
        for i, ing in enumerate(recipe.get("recipeIngredient", [])):
            # Skip section titles (title-only entries, no food expected)
            if ing.get("title") and not ing.get("food") and not ing.get("note"):
                continue

            display = ing.get("display") or ing.get("note") or ing.get("originalText") or ""
            food = ing.get("food")
            quantity = ing.get("quantity")
            unit = ing.get("unit")

            if not food or not food.get("id"):
                issues.append(IngredientIssue(
                    ingredient_index=i,
                    original_text=display,
                    issue_type="missing_food",
                    description="No linked food",
                ))
            if quantity is None or quantity == 0:
                issues.append(IngredientIssue(
                    ingredient_index=i,
                    original_text=display,
                    issue_type="missing_quantity",
                    description="Quantity is missing or zero",
                ))
            if not unit or not unit.get("id"):
                issues.append(IngredientIssue(
                    ingredient_index=i,
                    original_text=display,
                    issue_type="missing_unit",
                    description="No unit assigned",
                ))
        return issues

    @staticmethod
    def _audit_steps(recipe: dict) -> RecipeStepsAudit:
        instructions = recipe.get("recipeInstructions", [])
        return RecipeStepsAudit(
            recipe_id=recipe["id"],
            recipe_slug=recipe["slug"],
            recipe_name=recipe["name"],
            has_instructions=len(instructions) > 0,
            instruction_count=len(instructions),
        )

    # ------------------------------------------------------------------
    # Fix proposals
    # ------------------------------------------------------------------

    async def propose_ingredient_fix(
        self, recipe_slug: str, parser: str | None = None
    ) -> IngredientFixProposal:
        parser = parser or self.parser
        recipe = await self.mealie.get_recipe(recipe_slug)
        ingredients = recipe.get("recipeIngredient", [])

        # Collect raw texts for ingredients that have issues
        issues = self._audit_ingredients(recipe)
        if not issues:
            return IngredientFixProposal(
                recipe_slug=recipe_slug,
                recipe_name=recipe["name"],
                parser_used=parser,
                ingredients=[],
            )

        issue_indices = {iss.ingredient_index for iss in issues}
        raw_texts: list[str] = []
        index_map: list[int] = []  # maps position in raw_texts -> ingredient index
        for i, ing in enumerate(ingredients):
            if i in issue_indices:
                text = ing.get("display") or ing.get("note") or ing.get("originalText") or ""
                if text:
                    raw_texts.append(text)
                    index_map.append(i)

        if not raw_texts:
            return IngredientFixProposal(
                recipe_slug=recipe_slug,
                recipe_name=recipe["name"],
                parser_used=parser,
                ingredients=[],
            )

        parsed = await self.mealie.parse_ingredients(raw_texts, parser)

        fixes: list[IngredientFix] = []
        for j, parsed_item in enumerate(parsed):
            idx = index_map[j]
            pi = parsed_item.get("ingredient", {})
            conf = parsed_item.get("confidence", {})

            food = pi.get("food")
            unit = pi.get("unit")

            fixes.append(IngredientFix(
                ingredient_index=idx,
                original_text=raw_texts[j],
                proposed_quantity=pi.get("quantity"),
                proposed_unit=unit.get("name") if unit else None,
                proposed_unit_id=unit.get("id") if unit and unit.get("id") else None,
                proposed_food=food.get("name") if food else None,
                proposed_food_id=food.get("id") if food and food.get("id") else None,
                confidence=conf.get("average"),
            ))

        return IngredientFixProposal(
            recipe_slug=recipe_slug,
            recipe_name=recipe["name"],
            parser_used=parser,
            ingredients=fixes,
        )

    async def propose_language_fix(
        self, recipe_slug: str
    ) -> TranslationFixProposal:
        if not self.language_auditor:
            raise ValueError("Language auditor not configured")

        recipe = await self.mealie.get_recipe(recipe_slug)

        # Detect source language
        lang_results = await self.language_auditor.detect_batch(
            [recipe], self.target_language
        )
        source_lang = lang_results[0].detected_language if lang_results else "unknown"

        # Translate
        proposal = await self.language_auditor.translate_recipe(
            recipe, source_lang, self.target_language,
            mealie_client=self.mealie,
        )

        # Try to match translated ingredient names to existing foods
        for ing_trans in proposal.ingredient_translations:
            matches = await self.mealie.search_foods(ing_trans.translated_food_name)
            best_match = None
            best_score = 0.0
            for m in matches:
                score = fuzz.token_set_ratio(
                    ing_trans.translated_food_name.lower(),
                    m.get("name", "").lower(),
                )
                if score > best_score:
                    best_score = score
                    best_match = m
            if best_match and best_score >= FOOD_MATCH_THRESHOLD:
                ing_trans.matched_food_id = best_match.get("id")
                ing_trans.matched_food_name = best_match.get("name")
                ing_trans.needs_new_food = False
            else:
                ing_trans.needs_new_food = True

        return proposal

    # ------------------------------------------------------------------
    # Fix application
    # ------------------------------------------------------------------

    async def apply_ingredient_fix(
        self, recipe_slug: str, fixes: list[dict]
    ) -> FixResult:
        recipe = await self.mealie.get_recipe(recipe_slug)
        ingredients = recipe.get("recipeIngredient", [])

        for fix in fixes:
            idx = fix["ingredient_index"]
            if idx >= len(ingredients):
                continue
            ing = ingredients[idx]
            if fix.get("quantity") is not None:
                ing["quantity"] = fix["quantity"]
            if fix.get("unit_id"):
                ing["unit"] = {"id": fix["unit_id"]}
            elif fix.get("unit"):
                ing["unit"] = {"name": fix["unit"]}
            if fix.get("food_id"):
                ing["food"] = {"id": fix["food_id"]}
            elif fix.get("food"):
                ing["food"] = {"name": fix["food"]}

        recipe["recipeIngredient"] = ingredients
        await self.mealie.update_recipe(recipe_slug, recipe)

        return FixResult(
            recipe_slug=recipe_slug,
            fix_type="ingredient_parse",
            success=True,
            detail=f"Updated {len(fixes)} ingredients",
        )

    async def apply_language_fix(
        self, recipe_slug: str, data: dict
    ) -> FixResult:
        recipe = await self.mealie.get_recipe(recipe_slug)

        # TranslationFixProposal uses proposed_name/proposed_description/proposed_steps
        # Direct API calls use name/description/steps — support both
        new_name = data.get("proposed_name") or data.get("name")
        new_desc = data.get("proposed_description") if "proposed_description" in data else data.get("description")
        new_steps = data.get("proposed_steps") or data.get("steps")

        if new_name:
            recipe["name"] = new_name
        if new_desc is not None:
            recipe["description"] = new_desc
        if new_steps:
            instructions = recipe.get("recipeInstructions", [])
            for i, step_text in enumerate(new_steps):
                if i < len(instructions):
                    instructions[i]["text"] = step_text
            recipe["recipeInstructions"] = instructions

        # Relink ingredient foods — support both field names
        translations = data.get("ingredient_translations") or data.get("ingredient_foods") or []
        ingredients = recipe.get("recipeIngredient", [])
        for ing_item in translations:
            idx = ing_item.get("ingredient_index", -1)
            if not (0 <= idx < len(ingredients)):
                continue

            food_id = ing_item.get("matched_food_id") or ing_item.get("food_id")
            food_name = ing_item.get("translated_food_name") or ing_item.get("food_name")
            needs_new = ing_item.get("needs_new_food", False)

            if food_id:
                ingredients[idx]["food"] = {"id": food_id}
            elif needs_new and food_name:
                new_food = await self.mealie.create_food(food_name)
                ingredients[idx]["food"] = {"id": new_food["id"]}

        await self.mealie.update_recipe(recipe_slug, recipe)

        return FixResult(
            recipe_slug=recipe_slug,
            fix_type="translate",
            success=True,
            detail=f"Translated recipe to {self.target_language}",
        )

    # ------------------------------------------------------------------
    # Batch fix wizard
    # ------------------------------------------------------------------

    async def batch_fix_stream(
        self,
        fix_type: str,
        recipe_slugs: list[str],
        parser: str | None = None,
    ) -> AsyncGenerator[tuple[str, dict], None]:
        applied = 0
        skipped = 0
        errors = 0
        total = len(recipe_slugs)

        yield "batch_start", {"total": total, "fix_type": fix_type}

        for i, slug in enumerate(recipe_slugs):
            try:
                if fix_type == "ingredients":
                    proposal = await self.propose_ingredient_fix(slug, parser)
                    proposal_data = proposal.model_dump()
                elif fix_type == "language":
                    proposal = await self.propose_language_fix(slug)
                    proposal_data = proposal.model_dump()
                else:
                    continue
            except Exception as e:
                logger.exception("Failed to propose fix for %s", slug)
                yield "fix_error", {"recipe_slug": slug, "index": i, "error": str(e)}
                errors += 1
                continue

            event = asyncio.Event()
            self._batch_confirmations[slug] = event
            self._batch_actions[slug] = "pending"

            yield "fix_propose", {
                "recipe_slug": slug,
                "index": i,
                "total": total,
                "proposal": proposal_data,
            }

            try:
                await asyncio.wait_for(event.wait(), timeout=300.0)
            except asyncio.TimeoutError:
                yield "fix_skipped", {"recipe_slug": slug, "index": i, "reason": "timeout"}
                skipped += 1
                continue
            finally:
                self._batch_confirmations.pop(slug, None)

            action = self._batch_actions.pop(slug, "skip")

            if action == "apply":
                try:
                    if fix_type == "ingredients":
                        fix_data = [
                            {
                                "ingredient_index": f["ingredient_index"],
                                "quantity": f.get("proposed_quantity"),
                                "unit_id": f.get("proposed_unit_id"),
                                "unit": f.get("proposed_unit"),
                                "food_id": f.get("proposed_food_id"),
                                "food": f.get("proposed_food"),
                            }
                            for f in proposal_data.get("ingredients", [])
                        ]
                        result = await self.apply_ingredient_fix(slug, fix_data)
                    else:
                        result = await self.apply_language_fix(slug, proposal_data)
                    yield "fix_applied", {
                        "recipe_slug": slug,
                        "index": i,
                        "result": result.model_dump(),
                    }
                    applied += 1
                except Exception as e:
                    logger.exception("Failed to apply fix for %s", slug)
                    yield "fix_error", {"recipe_slug": slug, "index": i, "error": str(e)}
                    errors += 1
            else:
                yield "fix_skipped", {"recipe_slug": slug, "index": i}
                skipped += 1

        yield "batch_complete", {
            "applied": applied,
            "skipped": skipped,
            "errors": errors,
            "total": total,
        }

    def confirm_batch_fix(self, slug: str, action: str) -> bool:
        self._batch_actions[slug] = action
        event = self._batch_confirmations.get(slug)
        if event:
            event.set()
            return True
        return False
