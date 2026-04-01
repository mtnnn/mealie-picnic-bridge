import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import models
from app.config import settings
from app.llm_matcher import LLMMatcher, MatchRequest
from app.matcher import find_best_match, parse_ingredient_name
from app.mealie import MealieClient
from app.models import ItemStatus, SyncItemResult, SyncResult
from app.picnic_client import PicnicClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mealie: MealieClient
picnic: PicnicClient
llm_matcher: LLMMatcher | None = None

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
    global mealie, picnic, llm_matcher
    mealie = MealieClient(settings.MEALIE_HOST, settings.MEALIE_TOKEN)
    picnic = PicnicClient(
        username=settings.PICNIC_USERNAME,
        password=settings.PICNIC_PASSWORD,
        country_code=settings.PICNIC_COUNTRY_CODE,
        auth_token=settings.PICNIC_AUTH_TOKEN,
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
    logger.info("Clients initialized")
    yield
    picnic.close()
    await mealie.close()


app = FastAPI(title="Mealie Picnic Bridge", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


# === Pages ===


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


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


# === API: Sync Status ===


@app.get("/status")
async def status():
    return {"last_sync": models.last_sync_result}


# === API: Sync (original, kept for backward compat) ===


@app.post("/sync", response_model=SyncResult)
async def sync(skip_cache: bool = False):
    results: list[SyncItemResult] = []
    async for event_type, data in _sync_generator(skip_cache):
        if event_type == "item_result":
            results.append(SyncItemResult(
                name=data["name"],
                status=ItemStatus(data["status"]),
                picnic_product_name=data.get("picnic_product_name"),
                picnic_product_id=data.get("picnic_product_id"),
                score=data.get("score"),
                error=data.get("error"),
            ))
        elif event_type == "sync_complete":
            result = SyncResult(
                timestamp=datetime.now(),
                total_items=data["total_items"],
                added_to_cart=data["added_to_cart"],
                no_match=data["no_match"],
                errors=data["errors"],
                items=results,
            )
            models.last_sync_result = result
            return result

    # Fallback if generator ends without sync_complete
    result = SyncResult(
        timestamp=datetime.now(),
        total_items=len(results),
        added_to_cart=sum(1 for r in results if r.status in (ItemStatus.matched, ItemStatus.llm_matched, ItemStatus.cached)),
        no_match=sum(1 for r in results if r.status == ItemStatus.no_match),
        errors=sum(1 for r in results if r.status == ItemStatus.error),
        items=results,
    )
    models.last_sync_result = result
    return result


# === API: Sync Stream (SSE) ===

_sync_cancel: asyncio.Event | None = None


@app.post("/sync/stream")
async def sync_stream(skip_cache: bool = False):
    global _sync_cancel
    _sync_cancel = asyncio.Event()
    cancel = _sync_cancel

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event_type, data in _sync_generator(skip_cache, cancel):
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


@app.post("/sync/stop")
async def sync_stop():
    if _sync_cancel is not None:
        _sync_cancel.set()
        return {"ok": True}
    return {"ok": False, "error": "No sync in progress"}


# === Sync Generator (shared logic) ===


async def _sync_generator(
    skip_cache: bool = False,
    cancel: asyncio.Event | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Core sync logic as an async generator yielding (event_type, data) tuples."""
    items_results: list[SyncItemResult] = []
    added = 0
    no_match_count = 0
    error_count = 0
    pending: list[PendingItem] = []
    item_index = 0

    shopping_lists = await mealie.get_shopping_lists()
    logger.info("Found %d shopping lists", len(shopping_lists))

    # Pre-scan to collect all items for sync_start event
    all_items_info = []
    all_raw_items = []
    for sl in shopping_lists:
        list_items = await mealie.get_list_items(sl["id"])
        for item in list_items:
            food = item.get("food") or {}
            display = item.get("display", "")
            note = item.get("note")
            food_name = food.get("name")
            raw_quantity = item.get("quantity") or 1
            unit_obj = item.get("unit") or {}
            unit_name = unit_obj.get("name") or unit_obj.get("abbreviation") or None
            ingredient_name = parse_ingredient_name(display, note, food_name)

            all_items_info.append({
                "name": ingredient_name,
                "quantity": raw_quantity,
                "unit": unit_name,
            })
            all_raw_items.append((item, ingredient_name, food, raw_quantity, unit_name))

    # Emit sync_start
    yield "sync_start", {
        "total_items": len(all_raw_items),
        "lists": [sl.get("name", "?") for sl in shopping_lists],
        "items": all_items_info,
    }

    # --- Phase 1: Collect ---
    for raw_item, ingredient_name, food, raw_quantity, unit_name in all_raw_items:
        if cancel and cancel.is_set():
            yield "sync_cancelled", {}
            return

        if unit_name:
            quantity = 1
        else:
            quantity = max(1, round(raw_quantity))

        yield "item_start", {"name": ingredient_name, "index": item_index, "phase": "searching"}

        try:
            extras = food.get("extras") or {}
            cached_id = extras.get("picnic_product_id")
            cached_name = extras.get("picnic_product_name")

            if cached_id and not skip_cache:
                await asyncio.to_thread(picnic.add_to_cart, cached_id, quantity)
                await asyncio.sleep(random.uniform(10, 25))
                yield "item_result", {
                    "name": ingredient_name,
                    "index": item_index,
                    "status": "cached",
                    "picnic_product_id": cached_id,
                    "picnic_product_name": cached_name,
                }
                items_results.append(SyncItemResult(
                    name=ingredient_name, status=ItemStatus.cached,
                    picnic_product_id=cached_id, picnic_product_name=cached_name,
                ))
                added += 1
                item_index += 1
                continue

            await asyncio.sleep(random.uniform(10, 25))
            products = await asyncio.to_thread(picnic.search, ingredient_name)

            if not products:
                yield "item_result", {
                    "name": ingredient_name,
                    "index": item_index,
                    "status": "no_match",
                }
                items_results.append(SyncItemResult(
                    name=ingredient_name, status=ItemStatus.no_match,
                ))
                no_match_count += 1
                item_index += 1
                continue

            pending.append(PendingItem(
                ingredient_name=ingredient_name,
                products=products,
                food=food,
                quantity=quantity,
                raw_quantity=raw_quantity,
                unit_name=unit_name,
                index=item_index,
            ))

        except Exception as exc:
            logger.exception("Error collecting item '%s'", ingredient_name)
            yield "item_result", {
                "name": ingredient_name,
                "index": item_index,
                "status": "error",
                "error": str(exc),
            }
            items_results.append(SyncItemResult(
                name=ingredient_name, status=ItemStatus.error, error=str(exc),
            ))
            error_count += 1

        item_index += 1

    # --- Phase 2: Match ---
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
                p.llm_quantity = result.recommended_quantity
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

    # --- Phase 3: Process ---
    for p in pending:
        if cancel and cancel.is_set():
            yield "sync_cancelled", {}
            return

        try:
            if p.matched_product is None:
                yield "item_result", {
                    "name": p.ingredient_name,
                    "index": p.index,
                    "status": "no_match",
                }
                items_results.append(SyncItemResult(
                    name=p.ingredient_name, status=ItemStatus.no_match,
                ))
                no_match_count += 1
                continue

            product_id = str(p.matched_product["id"])
            product_name = p.matched_product.get("name", "")

            if p.food.get("id"):
                await mealie.update_food_extras(
                    p.food["id"],
                    {"picnic_product_id": product_id, "picnic_product_name": product_name},
                )

            cart_qty = p.llm_quantity if p.llm_quantity is not None else p.quantity
            await asyncio.to_thread(picnic.add_to_cart, product_id, cart_qty)
            await asyncio.sleep(random.uniform(10, 25))

            item_status = ItemStatus.llm_matched if p.llm_matched else ItemStatus.matched
            yield "item_result", {
                "name": p.ingredient_name,
                "index": p.index,
                "status": item_status.value,
                "picnic_product_id": product_id,
                "picnic_product_name": product_name,
            }
            items_results.append(SyncItemResult(
                name=p.ingredient_name, status=item_status,
                picnic_product_id=product_id, picnic_product_name=product_name,
            ))
            added += 1

        except Exception as exc:
            logger.exception("Error processing item '%s'", p.ingredient_name)
            yield "item_result", {
                "name": p.ingredient_name,
                "index": p.index,
                "status": "error",
                "error": str(exc),
            }
            items_results.append(SyncItemResult(
                name=p.ingredient_name, status=ItemStatus.error, error=str(exc),
            ))
            error_count += 1

    # Emit sync_complete
    total = len(items_results)
    yield "sync_complete", {
        "total_items": total,
        "added_to_cart": added,
        "no_match": no_match_count,
        "errors": error_count,
    }

    # Store last result
    result = SyncResult(
        timestamp=datetime.now(),
        total_items=total,
        added_to_cart=added,
        no_match=no_match_count,
        errors=error_count,
        items=items_results,
    )
    models.last_sync_result = result
    logger.info(
        "Sync complete: %d items, %d added, %d no match, %d errors",
        total, added, no_match_count, error_count,
    )
