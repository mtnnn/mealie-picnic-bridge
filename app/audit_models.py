from enum import Enum

from pydantic import BaseModel


class AuditCategory(str, Enum):
    ingredients = "ingredients"
    steps = "steps"
    language = "language"
    photos = "photos"


class IngredientIssue(BaseModel):
    ingredient_index: int
    original_text: str
    issue_type: str  # "missing_food", "missing_quantity", "missing_unit"
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
    detected_language: str
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
    instruction_count: int
    ingredient_issue_count: int
    total_ingredients: int
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


# --- Fix proposals ---


class IngredientFix(BaseModel):
    ingredient_index: int
    original_text: str
    proposed_quantity: float | None = None
    proposed_unit: str | None = None
    proposed_unit_id: str | None = None
    proposed_food: str | None = None
    proposed_food_id: str | None = None
    confidence: float | None = None


class IngredientFixProposal(BaseModel):
    recipe_slug: str
    recipe_name: str
    parser_used: str
    ingredients: list[IngredientFix]


class IngredientTranslation(BaseModel):
    ingredient_index: int
    original_food_name: str
    translated_food_name: str
    matched_food_id: str | None = None
    matched_food_name: str | None = None
    needs_new_food: bool = False


class TranslationFixProposal(BaseModel):
    recipe_slug: str
    recipe_name: str
    source_language: str
    proposed_name: str
    proposed_description: str | None = None
    proposed_steps: list[str]
    ingredient_translations: list[IngredientTranslation]


class FixResult(BaseModel):
    recipe_slug: str
    fix_type: str
    success: bool
    detail: str
