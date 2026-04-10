# Comprehensive Recipe Audit System

## Context

The mealie-picnic-bridge app syncs shopping lists from Mealie to Picnic. The sync relies on recipes having well-structured data: proper ingredient links, quantities, units, and consistent language. Currently, the only audit feature checks for missing photos. Recipes imported from various sources often have incomplete ingredients (free-text instead of structured food links), missing steps, or are in the wrong language. This leads to poor sync quality.

This design expands the audit page into a full recipe quality system that detects and fixes these issues before sync, using Mealie's built-in ML ingredient parser and LLM-based translation.

## Architecture Overview

Three new backend modules compose around existing code without modifying working components:

```
AuditScanner (new)
  |-- MealieClient (existing, extended with 4 new methods)
  |-- RecipeAuditor (existing, unchanged -- photo auditing)
  |-- LanguageAuditor (new -- LLM detection + translation)
```

The frontend replaces the current photo-only audit page with a unified tabbed interface.

## Files

**New:**
- `app/audit_models.py` -- Pydantic models for audit results, issues, fix proposals
- `app/audit_scanner.py` -- Core scan logic + batch fix orchestration
- `app/language_auditor.py` -- LLM language detection and translation

**Modified:**
- `app/config.py` -- Add `AUDIT_TARGET_LANGUAGE`, `AUDIT_PARSER`
- `app/mealie.py` -- Add `parse_ingredients()`, `update_recipe()`, `create_food()`, `search_foods()`
- `app/main.py` -- Add ~8 new audit endpoints, wire `AuditScanner`
- `app/templates/audit.html` -- Rewrite with tab structure
- `app/static/js/audit.js` -- Rewrite with tab/modal/SSE logic

**Unchanged:**
- `app/recipe_auditor.py` -- Photo auditing stays as-is
- `app/llm_matcher.py` -- Product matching stays as-is
- `app/matcher.py`, `app/picnic_client.py` -- Not affected

## Config Additions

```python
# In app/config.py Settings class:
AUDIT_TARGET_LANGUAGE: str = "nl"          # ISO 639-1 target language
AUDIT_PARSER: str = "nlp"                 # Mealie parser: "nlp", "brute", or "openai"
AUDIT_LLM_PROVIDER: str = "anthropic"     # "anthropic" or "openai" for language tasks
```

## Audit Categories

### Overview Tab
Dashboard showing:
- Total recipe count
- Issue counts per category (ingredients, steps, language, photos)
- Overall health score (0-100, average of per-recipe scores)
- Recipe cards with color-coded health indicators

**Health score formula** (per recipe, 25 points each):
- Has photo: 25 pts
- Has instructions (>0 steps): 25 pts
- Ingredients complete (% with food + quantity + unit): 0-25 pts proportional
- Correct language: 25 pts

### Ingredients Tab
**Checks per ingredient:**
- `missing_food` -- No linked Food object (free-text only)
- `missing_quantity` -- Quantity is null or 0
- `missing_unit` -- No unit assigned

**Auto-fix:** Call Mealie's built-in parser API (`POST /api/parser/ingredients`) with the raw ingredient texts. The parser returns structured data with confidence scores. Three parser backends available: `nlp` (default, ML-based), `brute` (rule-based), `openai` (uses Mealie's own OpenAI integration).

### Steps Tab
**Check:** Recipe has zero instructions (`recipeInstructions` is empty).
**No auto-fix** -- flagging only. Too risky to auto-generate cooking instructions.

### Language Tab
**Check:** LLM-based language detection. Send recipe name + first 2 instruction steps to Anthropic. Compare detected language against `AUDIT_TARGET_LANGUAGE`.

**Auto-fix:** LLM translates name, description, and all steps. For ingredient food names:
1. LLM translates the food name to target language
2. Search existing Mealie foods via `GET /api/foods?search=<translated_name>`
3. If exact match found -> relink to existing food
4. If no exact match, fuzzy match using `rapidfuzz.fuzz.token_set_ratio` (reuse from `app/matcher.py`) with threshold >= 80
5. If fuzzy match found -> relink to existing food
6. If no match -> create new food via `POST /api/foods`

### Photos Tab
Existing functionality moved into the tab structure. No changes to photo audit logic.

## Data Models (`app/audit_models.py`)

```python
class AuditCategory(str, Enum):
    ingredients = "ingredients"
    steps = "steps"
    language = "language"
    photos = "photos"

class IngredientIssue(BaseModel):
    ingredient_index: int
    original_text: str
    issue_type: str            # "missing_food", "missing_quantity", "missing_unit"
    description: str

class RecipeIngredientAudit(BaseModel):
    recipe_id: str
    recipe_slug: str
    recipe_name: str
    issues: list[IngredientIssue]

class RecipeStepsAudit(BaseModel):
    recipe_id: str
    recipe_slug: str
    recipe_name: str
    has_instructions: bool
    instruction_count: int

class RecipeLanguageAudit(BaseModel):
    recipe_id: str
    recipe_slug: str
    recipe_name: str
    detected_language: str     # ISO 639-1 code
    target_language: str
    is_correct_language: bool
    confidence: float

class RecipeAuditSummary(BaseModel):
    recipe_id: str
    recipe_slug: str
    recipe_name: str
    image_url: str | None = None
    health_score: float
    has_photo: bool
    has_instructions: bool
    ingredient_issue_count: int
    is_correct_language: bool
    detected_language: str | None = None

class FullAuditResult(BaseModel):
    total_recipes: int
    overall_health_score: float
    recipes: list[RecipeAuditSummary]
    ingredient_issues: list[RecipeIngredientAudit]
    step_issues: list[RecipeStepsAudit]
    language_issues: list[RecipeLanguageAudit]
    photo_missing_count: int
    ingredient_issue_recipe_count: int
    step_issue_recipe_count: int
    language_issue_recipe_count: int
```

### Fix Proposals

```python
class IngredientFixProposal(BaseModel):
    recipe_slug: str
    recipe_name: str
    ingredients: list[IngredientFix]

class IngredientFix(BaseModel):
    ingredient_index: int
    original_text: str
    proposed_quantity: float | None = None
    proposed_unit: str | None = None
    proposed_unit_id: str | None = None
    proposed_food: str | None = None
    proposed_food_id: str | None = None
    confidence: float | None = None

class TranslationFixProposal(BaseModel):
    recipe_slug: str
    recipe_name: str
    source_language: str
    proposed_name: str
    proposed_description: str | None = None
    proposed_steps: list[str]
    ingredient_translations: list[IngredientTranslation]

class IngredientTranslation(BaseModel):
    ingredient_index: int
    original_food_name: str
    translated_food_name: str
    matched_food_id: str | None = None   # existing food found via search
    matched_food_name: str | None = None
    needs_new_food: bool = False

class FixResult(BaseModel):
    recipe_slug: str
    fix_type: str
    success: bool
    detail: str
```

## API Endpoints

### Scan

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/audit/scan/stream` | SSE stream. Events: `scan_start`, `recipe_scanned`, `scan_complete` |
| `GET` | `/audit/results` | Return cached `FullAuditResult` from last scan (404 if no scan) |

### Fix Proposals (per recipe)

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `POST` | `/audit/fix/ingredients/propose` | `{recipe_slug, parser?}` | `IngredientFixProposal` |
| `POST` | `/audit/fix/language/propose` | `{recipe_slug}` | `TranslationFixProposal` |

### Fix Application (per recipe)

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `POST` | `/audit/fix/ingredients/apply` | `{recipe_slug, fixes: [...]}` | `FixResult` |
| `POST` | `/audit/fix/language/apply` | `{recipe_slug, name, description, steps, ingredient_foods}` | `FixResult` |

### Batch Fix (SSE wizard)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/audit/fix/batch/stream` | SSE stream iterating recipes. Events: `batch_start`, `fix_propose`, `fix_applied`, `fix_skipped`, `fix_error`, `batch_complete` |
| `POST` | `/audit/fix/batch/confirm` | `{recipe_slug, action: "apply"\|"skip"}` -- unblocks the stream generator |

### Existing (unchanged)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/audit/recipes` | Photo statuses (used by Photos tab) |
| `GET` | `/audit/prompt-template` | DALL-E prompt template |
| `POST` | `/audit/recipes/{slug}/generate` | Generate photo candidates |
| `POST` | `/audit/recipes/{slug}/apply` | Apply selected photo |

## MealieClient Additions (`app/mealie.py`)

```python
async def parse_ingredients(self, ingredients: list[str], parser: str = "nlp") -> list[dict]:
    """POST /api/parser/ingredients -> list[ParsedIngredient]"""

async def update_recipe(self, slug: str, data: dict) -> dict:
    """PUT /api/recipes/{slug} -> updated recipe"""

async def create_food(self, name: str) -> dict:
    """POST /api/foods -> created food with id"""

async def search_foods(self, query: str) -> list[dict]:
    """GET /api/foods?search=query&perPage=10 -> list[Food]"""
```

## Language Auditor (`app/language_auditor.py`)

Separate from `llm_matcher.py` because it serves a fundamentally different purpose with different prompts and tool schemas.

**Provider support:** Anthropic (primary, reuses existing `ANTHROPIC_API_KEY`) and OpenAI (if `OPENAI_API_KEY` configured). Config setting `AUDIT_LLM_PROVIDER: str = "anthropic"` selects which.

**Language detection:**
- Batch up to 30 recipes per LLM call
- Send recipe name + first 2 steps per recipe
- Tool-use schema returns `[{index, language, confidence}]`
- System prompt: identify ISO 639-1 language code

**Translation:**
- One LLM call per recipe
- Tool-use schema returns `{name, description, steps[], ingredient_names[{index, original, translated}]}`
- System prompt: professional recipe translator, maintain cooking terminology accuracy, keep measurements unchanged

## Batch Fix Flow (SSE + asyncio.Event)

Follows the same pattern as the existing sync stream (`/match/stream` + `/match/stop`):

1. Frontend starts SSE connection to `/audit/fix/batch/stream`
2. Backend iterates recipes, generates fix proposal
3. Backend yields `fix_propose` event with the proposal, then awaits `asyncio.Event`
4. Frontend shows proposal in a modal, user clicks Apply or Skip
5. Frontend POSTs to `/audit/fix/batch/confirm` with action
6. Backend `Event.set()` unblocks the generator, applies fix or skips
7. Backend yields `fix_applied` or `fix_skipped`, proceeds to next recipe

Timeout: 5 minutes per recipe confirmation. After timeout, auto-skip.

## Frontend Design

### Tab Structure
The audit page header has tabs: **Overview | Ingredients | Steps | Language | Photos**

Each tab loads its data from the cached scan results. The scan runs once when user clicks "Scan All" (or auto-runs on page load if no cached results).

### Recipe Cards
Each card shows:
- Recipe thumbnail (if available)
- Recipe name
- Health score badge (color: green >75, yellow >50, red <=50)
- Issue icons per category

Clicking a card opens a detail modal showing all issues for that recipe.

### Fix Modal (batch wizard)
When user clicks "Fix All" on a tab:
1. Progress bar shows X of Y recipes
2. Current recipe displayed with:
   - Original values (left side)
   - Proposed fix (right side, highlighted changes)
3. Two buttons: **Apply** and **Skip**
4. After each action, moves to next recipe automatically

### Photos Tab
Identical to current audit page functionality, just nested in the tab structure. The DALL-E prompt editor and photo modal remain.

## Verification

1. **Scan test:** Start the app, navigate to `/audit`, click "Scan All". Verify all recipes load with correct issue counts. Check the health scores add up.
2. **Ingredient fix test:** On the Ingredients tab, pick a recipe with missing foods. Click to open fix modal. Verify the Mealie parser returns structured data. Apply fix, then re-scan to confirm issues resolved.
3. **Language test:** Import a recipe in English (target is "nl"). Run scan. Verify it shows up in Language tab. Run fix, confirm translation proposal, verify recipe updated in Mealie.
4. **Batch fix test:** Click "Fix All" on Ingredients tab. Verify the wizard iterates recipes, shows proposals, waits for confirmation. Apply a few, skip a few. Re-scan to confirm applied fixes stuck.
5. **Photo tab test:** Verify existing photo audit functionality still works within the new tab structure.
6. **No regression:** Run the main sync flow (`/`) and verify ingredient matching still works correctly.
