import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import models
from app.audit_scanner import AuditScanner
from app.config import settings
from app.language_auditor import LanguageAuditor
from app.llm_matcher import LLMMatcher, MatchRequest
from app.matcher import find_best_match, parse_ingredient_name
from app.mealie import MealieClient
from app.models import ItemStatus, SyncItemResult, SyncResult
from app.picnic_client import PicnicClient
from app.recipe_auditor import RecipeAuditor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Units that represent weight/volume — when amount is in these units,
# quantity is almost always 1 (one package)
_WEIGHT_VOLUME_UNITS = {
    "gram", "g", "gr", "kg", "kilogram",
    "ml", "milliliter", "liter", "l", "cl", "dl",
}

mealie: MealieClient
picnic: PicnicClient
llm_matcher: LLMMatcher | None = None
recipe_auditor: RecipeAuditor
audit_scanner: AuditScanner

APP_DIR = Path(__file__).parent


@dataclass
class PendingItem:
    ingredient_name: str
    products: list[dict]
    food: dict
    quantity: int
    raw_quantity: float | None = None
    unit_name: str | None = None
    index: int = 0
    matched_product: dict | None = field(default=None, init=False)
    llm_matched: bool = field(default=False, init=False)
    llm_quantity: int | None = field(default=None, init=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mealie, picnic, llm_matcher, recipe_auditor, audit_scanner
    mealie = MealieClient(settings.MEALIE_HOST, settings.MEALIE_TOKEN)
    picnic = PicnicClient(
        username=settings.PICNIC_USERNAME,
        password=settings.PICNIC_PASSWORD,
        country_code=settings.PICNIC_COUNTRY_CODE,
        auth_token=settings.PICNIC_AUTH_TOKEN,
    )
    recipe_auditor = RecipeAuditor(
        mealie=mealie,
        mealie_host=settings.MEALIE_HOST,
        openai_api_key=settings.OPENAI_API_KEY,
        brave_api_key=settings.BRAVE_API_KEY,
    )
    if settings.LLM_MATCHING_ENABLED and settings.ANTHROPIC_API_KEY:
        llm_matcher = LLMMatcher(
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.LLM_MODEL,
            max_products_per_item=settings.LLM_MAX_PRODUCTS_PER_ITEM,
        )
        logger.info("LLM matching enabled (model: %s)", settings.LLM_MODEL)
    else:
        logger.info("Using fuzzy matching (LLM matching disabled)")

    # Language auditor (optional — needs an API key)
    language_auditor = None
    provider = settings.AUDIT_LLM_PROVIDER
    if provider == "anthropic" and settings.ANTHROPIC_API_KEY:
        language_auditor = LanguageAuditor(
            provider="anthropic",
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            model=settings.LLM_MODEL,
        )
    elif provider == "openai" and settings.OPENAI_API_KEY:
        language_auditor = LanguageAuditor(
            provider="openai",
            openai_api_key=settings.OPENAI_API_KEY,
        )
    if language_auditor:
        logger.info("Language auditor enabled (provider: %s)", provider)

    audit_scanner = AuditScanner(
        mealie=mealie,
        mealie_host=settings.MEALIE_HOST,
        language_auditor=language_auditor,
        target_language=settings.AUDIT_TARGET_LANGUAGE,
        parser=settings.AUDIT_PARSER,
    )
    logger.info("Clients initialized")
    yield
    picnic.close()
    await recipe_auditor.close()
    await mealie.close()


app = FastAPI(title="Mealie Picnic Bridge", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Cache-busting: changes on every app restart
import time as _time
_cache_bust = str(int(_time.time()))
templates.env.globals["v"] = _cache_bust


# === Pages ===


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    return templates.TemplateResponse(request, "audit.html")


# === API: Audit ===


@app.get("/audit/recipes")
async def audit_recipes():
    try:
        statuses = await recipe_auditor.get_recipe_photo_statuses()
        return [s.model_dump() for s in statuses]
    except Exception as exc:
        logger.exception("Failed to fetch recipe statuses")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/audit/prompt-template")
async def audit_prompt_template():
    """Return the default DALL-E prompt template."""
    from app.recipe_auditor import DEFAULT_PROMPT_TEMPLATE
    return {"template": DEFAULT_PROMPT_TEMPLATE}


@app.post("/audit/recipes/{slug}/generate")
async def audit_generate(slug: str, body: dict | None = None):
    try:
        prompt_template = (body or {}).get("prompt_template") or None
        result = await recipe_auditor.search_photos(slug, prompt_template=prompt_template)
        return result.model_dump()
    except Exception as exc:
        logger.exception("Failed to generate photos for %s", slug)
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/audit/recipes/{slug}/apply")
async def audit_apply(slug: str, body: dict):
    image_url = body.get("image_url")
    if not image_url:
        raise HTTPException(status_code=422, detail="image_url is required")
    try:
        await recipe_auditor.apply_photo(slug, image_url)
        return {"ok": True}
    except Exception as exc:
        logger.exception("Failed to apply photo for %s", slug)
        raise HTTPException(status_code=502, detail=str(exc))


# === API: Audit Scan & Fix ===

_scan_cancel: asyncio.Event | None = None


@app.post("/audit/scan/stream")
async def audit_scan_stream():
    global _scan_cancel
    _scan_cancel = asyncio.Event()
    cancel = _scan_cancel

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event_type, data in audit_scanner.scan_all(cancel):
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/audit/scan/stop")
async def audit_scan_stop():
    if _scan_cancel is not None:
        _scan_cancel.set()
        return {"ok": True}
    return {"ok": False, "error": "No scan in progress"}


@app.get("/audit/results")
async def audit_results():
    result = audit_scanner.last_result
    if result is None:
        raise HTTPException(status_code=404, detail="No scan results available. Run a scan first.")
    return result.model_dump()


@app.post("/audit/fix/ingredients/propose")
async def audit_fix_ingredients_propose(body: dict):
    recipe_slug = body.get("recipe_slug")
    if not recipe_slug:
        raise HTTPException(status_code=422, detail="recipe_slug required")
    try:
        parser = body.get("parser")
        proposal = await audit_scanner.propose_ingredient_fix(recipe_slug, parser)
        return proposal.model_dump()
    except Exception as exc:
        logger.exception("Failed to propose ingredient fix for %s", recipe_slug)
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/audit/fix/ingredients/apply")
async def audit_fix_ingredients_apply(body: dict):
    recipe_slug = body.get("recipe_slug")
    fixes = body.get("fixes", [])
    if not recipe_slug:
        raise HTTPException(status_code=422, detail="recipe_slug required")
    try:
        result = await audit_scanner.apply_ingredient_fix(recipe_slug, fixes)
        return result.model_dump()
    except Exception as exc:
        logger.exception("Failed to apply ingredient fix for %s", recipe_slug)
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/audit/fix/language/propose")
async def audit_fix_language_propose(body: dict):
    recipe_slug = body.get("recipe_slug")
    if not recipe_slug:
        raise HTTPException(status_code=422, detail="recipe_slug required")
    try:
        proposal = await audit_scanner.propose_language_fix(recipe_slug)
        return proposal.model_dump()
    except Exception as exc:
        logger.exception("Failed to propose language fix for %s", recipe_slug)
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/audit/fix/language/apply")
async def audit_fix_language_apply(body: dict):
    recipe_slug = body.get("recipe_slug")
    if not recipe_slug:
        raise HTTPException(status_code=422, detail="recipe_slug required")
    try:
        result = await audit_scanner.apply_language_fix(recipe_slug, body)
        return result.model_dump()
    except Exception as exc:
        logger.exception("Failed to apply language fix for %s", recipe_slug)
        raise HTTPException(status_code=502, detail=str(exc))


_batch_cancel: asyncio.Event | None = None


@app.post("/audit/fix/batch/stream")
async def audit_fix_batch_stream(body: dict):
    fix_type = body.get("fix_type")
    recipe_slugs = body.get("recipe_slugs", [])
    parser = body.get("parser")
    if not fix_type or not recipe_slugs:
        raise HTTPException(status_code=422, detail="fix_type and recipe_slugs required")

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event_type, data in audit_scanner.batch_fix_stream(
            fix_type, recipe_slugs, parser
        ):
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/audit/fix/batch/confirm")
async def audit_fix_batch_confirm(body: dict):
    recipe_slug = body.get("recipe_slug")
    action = body.get("action", "skip")
    if not recipe_slug:
        raise HTTPException(status_code=422, detail="recipe_slug required")
    ok = audit_scanner.confirm_batch_fix(recipe_slug, action)
    return {"ok": ok}


# === API: Auth ===


@app.get("/auth/status")
async def auth_status():
    return {
        "needs_2fa": picnic.needs_2fa,
        "authenticated": not picnic.needs_2fa and bool(picnic.auth_token),
    }


@app.get("/auth", response_class=HTMLResponse)
async def auth_page():
    """Legacy auth page — redirects to main page which shows the modal."""
    return HTMLResponse(
        '<meta http-equiv="refresh" content="0;url=/">'
        "<p>Redirecting to <a href='/'>home</a>...</p>"
    )


@app.post("/auth/request-code")
async def auth_request_code():
    try:
        await asyncio.to_thread(picnic.request_2fa_code)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/auth/verify")
async def auth_verify(body: dict):
    try:
        await asyncio.to_thread(picnic.verify_2fa_code, body["code"])
        _save_token_to_env(picnic.auth_token)
        return {"ok": True, "token_preview": picnic.auth_token[:12] + "..."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


_ENV_HOST_PATH = "/app/.env.host"


def _save_token_to_env(token: str) -> None:
    """Write PICNIC_AUTH_TOKEN back to the mounted .env so it survives restarts."""
    try:
        path = _ENV_HOST_PATH
        with open(path) as f:
            lines = f.readlines()

        found = False
        for i, line in enumerate(lines):
            if line.startswith("PICNIC_AUTH_TOKEN="):
                lines[i] = f"PICNIC_AUTH_TOKEN={token}\n"
                found = True
                break

        if not found:
            lines.append(f"PICNIC_AUTH_TOKEN={token}\n")

        with open(path, "w") as f:
            f.writelines(lines)
        logger.info("Auth token saved to .env")
    except Exception:
        logger.warning("Could not save token to .env", exc_info=True)


# === API: Shopping Lists ===


@app.get("/shopping-lists")
async def get_shopping_lists():
    """Return shopping list summaries for the UI."""
    lists = await mealie.get_shopping_lists()
    result = []
    for sl in lists:
        items = await mealie.get_list_items(sl["id"])
        result.append({
            "id": sl["id"],
            "name": sl.get("name", "Unnamed"),
            "item_count": len(items),
        })
    return result


@app.get("/shopping-lists/{list_id}/items")
async def get_list_items(list_id: str):
    """Return parsed items for a shopping list."""
    items = await mealie.get_list_items(list_id)
    result = []
    for item in items:
        food = item.get("food") or {}
        display = item.get("display", "")
        note = item.get("note")
        food_name = food.get("name")
        raw_quantity = item.get("quantity") or 1
        unit_obj = item.get("unit") or {}
        unit_name = unit_obj.get("name") or unit_obj.get("abbreviation") or None

        name = parse_ingredient_name(display, note, food_name)
        result.append({
            "name": name,
            "quantity": raw_quantity,
            "unit": unit_name,
        })
    return result


_PICNIC_IMG_BASE = "https://storefront-prod.nl.picnicinternational.com/static/images"


def _picnic_image_url(image_id: str | None, size: str = "small") -> str | None:
    if not image_id:
        return None
    return f"{_PICNIC_IMG_BASE}/{image_id}/{size}.png"


# === API: Product Override ===


@app.get("/picnic/search")
async def picnic_search_products(q: str):
    """Search Picnic for products by query string."""
    products = await asyncio.to_thread(picnic.search, q)
    return [
        {
            "id": str(p.get("id", "")),
            "name": p.get("name", ""),
            "unit_quantity": p.get("unit_quantity", ""),
            "display_price": p.get("display_price", 0),
            "image_url": _picnic_image_url(p.get("image_id")),
        }
        for p in products[:20]
    ]


@app.post("/foods/{food_id}/product")
async def set_food_product(food_id: str, body: dict):
    """Save a manually chosen Picnic product to Mealie food extras."""
    product_id = body.get("product_id")
    product_name = body.get("product_name", "")
    if not product_id:
        raise HTTPException(status_code=422, detail="product_id required")
    await mealie.update_food_extras(
        food_id,
        {"picnic_product_id": product_id, "picnic_product_name": product_name},
    )
    return {"ok": True}


# === API: Status & Cache ===


@app.get("/status")
async def status():
    return {"last_sync": models.last_sync_result}


@app.delete("/cache")
async def delete_cache():
    """Clear all cached Picnic product mappings from Mealie foods."""
    foods = await mealie.get_all_foods()
    cleared = 0
    for food in foods:
        food_id = food.get("id")
        if food_id and await mealie.clear_food_picnic_cache(food_id, food):
            cleared += 1
    logger.info("Cleared Picnic cache from %d foods", cleared)
    return {"ok": True, "cleared": cleared}


# === API: Match Stream (Phase 1 — search & match, no cart) ===

_match_cancel: asyncio.Event | None = None


@app.post("/match/stream")
async def match_stream(skip_cache: bool = False):
    global _match_cancel
    _match_cancel = asyncio.Event()
    cancel = _match_cancel

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event_type, data in _match_generator(skip_cache, cancel):
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/match/stop")
async def match_stop():
    if _match_cancel is not None:
        _match_cancel.set()
        return {"ok": True}
    return {"ok": False, "error": "No match in progress"}


# === API: Cart Sync (Phase 2 — add matched items to Picnic cart) ===

_cart_cancel: asyncio.Event | None = None


@app.post("/cart/sync")
async def cart_sync(items: list[dict]):
    """Add a list of matched items to the Picnic cart.

    Each item: {product_id, product_name, quantity, food_id?, image_url?}
    """
    global _cart_cancel
    _cart_cancel = asyncio.Event()
    cancel = _cart_cancel

    async def event_stream() -> AsyncGenerator[str, None]:
        added = 0
        errors = 0
        for i, item in enumerate(items):
            if cancel.is_set():
                yield f"event: cart_cancelled\ndata: {json.dumps({})}\n\n"
                return

            product_id = item.get("product_id")
            quantity = item.get("quantity", 1)
            product_name = item.get("product_name", "")

            yield f"event: cart_item_start\ndata: {json.dumps({'index': i, 'product_name': product_name})}\n\n"

            try:
                await asyncio.to_thread(picnic.add_to_cart, product_id, quantity)
                await asyncio.sleep(random.uniform(10, 25))
                added += 1
                yield f"event: cart_item_done\ndata: {json.dumps({'index': i, 'product_name': product_name})}\n\n"
            except Exception as exc:
                errors += 1
                logger.exception("Failed to add %s to cart", product_id)
                yield f"event: cart_item_error\ndata: {json.dumps({'index': i, 'error': str(exc)})}\n\n"

        yield f"event: cart_complete\ndata: {json.dumps({'added': added, 'errors': errors, 'total': len(items)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/cart/stop")
async def cart_stop():
    if _cart_cancel is not None:
        _cart_cancel.set()
        return {"ok": True}
    return {"ok": False, "error": "No cart sync in progress"}


# === Match Generator (search + match, no cart adds) ===


async def _match_generator(
    skip_cache: bool = False,
    cancel: asyncio.Event | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Search & match logic. Yields matched items but does NOT add to cart."""
    no_match_count = 0
    error_count = 0
    matched_count = 0
    pending: list[PendingItem] = []
    item_index = 0

    shopping_lists = await mealie.get_shopping_lists()
    logger.info("Found %d shopping lists", len(shopping_lists))

    # Pre-scan to collect all items
    all_items_info = []
    all_raw_items = []
    for sl in shopping_lists:
        list_items = await mealie.get_list_items(sl["id"])
        for item in list_items:
            food = item.get("food") or {}
            display = item.get("display", "")
            note = item.get("note") or ""
            food_name = food.get("name")
            raw_quantity = item.get("quantity") or 1
            unit_obj = item.get("unit") or {}
            unit_name = unit_obj.get("name") or unit_obj.get("abbreviation") or None
            ingredient_name = parse_ingredient_name(display, note, food_name)

            # Include note in ingredient name for better matching
            # e.g. "Mozzarella" + note "Geraspt" → "Mozzarella Geraspt"
            if note and note.lower() not in ingredient_name.lower():
                ingredient_name = f"{ingredient_name} {note}"

            all_items_info.append({
                "name": ingredient_name,
                "quantity": raw_quantity,
                "unit": unit_name,
            })
            all_raw_items.append((item, ingredient_name, food, raw_quantity, unit_name, note))

    yield "match_start", {
        "total_items": len(all_raw_items),
        "lists": [sl.get("name", "?") for sl in shopping_lists],
        "items": all_items_info,
    }

    # --- Phase 1: Search ---
    for raw_item, ingredient_name, food, raw_quantity, unit_name, note in all_raw_items:
        if cancel and cancel.is_set():
            yield "match_cancelled", {}
            return

        yield "item_start", {"name": ingredient_name, "index": item_index, "phase": "searching"}

        try:
            extras = food.get("extras") or {}
            cached_id = extras.get("picnic_product_id")
            cached_name = extras.get("picnic_product_name")
            cached_image = extras.get("picnic_image_id")

            # Skip cache when item has a note (variant info like "Geraspt")
            # because the same food may need different products per recipe
            use_cache = cached_id and not skip_cache and not note
            if use_cache:
                # Cached items: use stored quantity or default to 1.
                # The LLM already determined the right quantity when first matched.
                cached_qty = extras.get("picnic_quantity", 1)
                yield "item_result", {
                    "name": ingredient_name,
                    "index": item_index,
                    "status": "cached",
                    "picnic_product_id": cached_id,
                    "picnic_product_name": cached_name,
                    "food_id": food.get("id"),
                    "image_url": _picnic_image_url(cached_image),
                    "quantity": cached_qty,
                }
                matched_count += 1
                item_index += 1
                continue

            await asyncio.sleep(random.uniform(10, 25))
            products = await asyncio.to_thread(picnic.search, ingredient_name)

            if not products:
                yield "item_result", {
                    "name": ingredient_name,
                    "index": item_index,
                    "status": "no_match",
                    "food_id": food.get("id"),
                }
                no_match_count += 1
                item_index += 1
                continue

            pending.append(PendingItem(
                ingredient_name=ingredient_name,
                products=products,
                food=food,
                quantity=1,  # default; LLM matcher will determine actual quantity
                raw_quantity=raw_quantity,
                unit_name=unit_name,
                index=item_index,
            ))

        except Exception as exc:
            logger.exception("Error searching item '%s'", ingredient_name)
            yield "item_result", {
                "name": ingredient_name,
                "index": item_index,
                "status": "error",
                "error": str(exc),
            }
            error_count += 1

        item_index += 1

    # --- Phase 2: LLM / Fuzzy Match ---
    for p in pending:
        yield "item_start", {"name": p.ingredient_name, "index": p.index, "phase": "matching"}

    if pending and llm_matcher:
        try:
            requests = [
                MatchRequest(
                    p.ingredient_name, p.products,
                    quantity=p.raw_quantity, unit=p.unit_name,
                )
                for p in pending
            ]
            llm_results = await llm_matcher.match_batch(requests)
            for p, result in zip(pending, llm_results):
                p.matched_product = result.selected_product
                p.llm_matched = result.selected_product is not None
                qty = result.recommended_quantity
                if (p.unit_name
                        and p.unit_name.lower() in _WEIGHT_VOLUME_UNITS
                        and qty > 5):
                    logger.warning(
                        "Capping unreasonable llm_quantity %d → 1 for '%s' (unit: %s)",
                        qty, p.ingredient_name, p.unit_name,
                    )
                    qty = 1
                p.llm_quantity = qty
        except Exception:
            logger.warning("LLM matching failed, falling back to fuzzy matching", exc_info=True)
            for p in pending:
                match = find_best_match(
                    p.ingredient_name, p.products, settings.FUZZY_THRESHOLD
                )
                p.matched_product = match[0] if match else None
    else:
        for p in pending:
            match = find_best_match(
                p.ingredient_name, p.products, settings.FUZZY_THRESHOLD
            )
            p.matched_product = match[0] if match else None

    # --- Emit match results ---
    for p in pending:
        if p.matched_product is None:
            yield "item_result", {
                "name": p.ingredient_name,
                "index": p.index,
                "status": "no_match",
                "food_id": p.food.get("id"),
            }
            no_match_count += 1
            continue

        product_id = str(p.matched_product["id"])
        product_name = p.matched_product.get("name", "")
        image_url = _picnic_image_url(p.matched_product.get("image_id"))
        cart_qty = p.llm_quantity if p.llm_quantity is not None else p.quantity

        # Save match to Mealie food extras (including quantity for future cached lookups)
        if p.food.get("id"):
            extras_update = {
                "picnic_product_id": product_id,
                "picnic_product_name": product_name,
                "picnic_quantity": cart_qty,
            }
            if p.matched_product.get("image_id"):
                extras_update["picnic_image_id"] = p.matched_product["image_id"]
            await mealie.update_food_extras(p.food["id"], extras_update)

        item_status = ItemStatus.llm_matched if p.llm_matched else ItemStatus.matched
        yield "item_result", {
            "name": p.ingredient_name,
            "index": p.index,
            "status": item_status.value,
            "picnic_product_id": product_id,
            "picnic_product_name": product_name,
            "food_id": p.food.get("id"),
            "image_url": image_url,
            "quantity": cart_qty,
        }
        matched_count += 1

    total = matched_count + no_match_count + error_count
    yield "match_complete", {
        "total_items": total,
        "matched": matched_count,
        "no_match": no_match_count,
        "errors": error_count,
    }
    logger.info(
        "Match complete: %d items, %d matched, %d no match, %d errors",
        total, matched_count, no_match_count, error_count,
    )
