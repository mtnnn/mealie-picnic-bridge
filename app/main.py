import asyncio
import logging
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

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


@dataclass
class PendingItem:
    ingredient_name: str
    products: list[dict]
    food: dict
    quantity: int
    raw_quantity: float | None = None
    unit_name: str | None = None
    matched_product: dict | None = field(default=None, init=False)
    llm_matched: bool = field(default=False, init=False)


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


INDEX_HTML = """<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Mealie Picnic Bridge</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, sans-serif; max-width: 600px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
        h1 { margin-bottom: 1.5rem; }
        button { background: #4CAF50; color: white; border: none; padding: 12px 24px; font-size: 1.1rem; border-radius: 6px; cursor: pointer; }
        button:hover { background: #45a049; }
        button:disabled { background: #ccc; cursor: wait; }
        #status { margin-top: 1.5rem; }
        .result { background: #f5f5f5; border-radius: 6px; padding: 1rem; margin-top: 1rem; }
        .item { padding: 4px 0; border-bottom: 1px solid #eee; }
        .matched { color: #2e7d32; }
        .llm_matched { color: #6a1b9a; }
        .cached { color: #1565c0; }
        .no_match { color: #f57f17; }
        .error { color: #c62828; }
    </style>
</head>
<body>
    <h1>Mealie Picnic Bridge</h1>
    <button id="syncBtn" onclick="doSync()">Sync naar Picnic</button>
    <label style="margin-left:1rem"><input type="checkbox" id="skipCache"> Cache overslaan</label>
    <div id="status"></div>

    <script>
        async function doSync() {
            const btn = document.getElementById('syncBtn');
            const status = document.getElementById('status');
            btn.disabled = true;
            btn.textContent = 'Bezig met sync...';
            status.innerHTML = '';
            try {
                const skip = document.getElementById('skipCache').checked;
                const url = skip ? '/sync?skip_cache=true' : '/sync';
                const resp = await fetch(url, { method: 'POST' });
                const data = await resp.json();
                renderResult(data);
            } catch (e) {
                status.innerHTML = '<div class="result error">Fout: ' + e.message + '</div>';
            } finally {
                btn.disabled = false;
                btn.textContent = 'Sync naar Picnic';
            }
        }

        function renderResult(data) {
            const status = document.getElementById('status');
            let html = '<div class="result">';
            html += '<strong>' + data.timestamp + '</strong><br>';
            html += 'Totaal: ' + data.total_items + ' | Toegevoegd: ' + data.added_to_cart;
            html += ' | Geen match: ' + data.no_match + ' | Fouten: ' + data.errors + '<br><br>';
            for (const item of data.items) {
                html += '<div class="item ' + item.status + '">';
                html += item.name + ' &rarr; ';
                if (item.picnic_product_name) {
                    html += item.picnic_product_name;
                    if (item.score) html += ' (' + Math.round(item.score) + '%)';
                } else if (item.error) {
                    html += item.error;
                } else {
                    html += 'geen match';
                }
                html += '</div>';
            }
            html += '</div>';
            status.innerHTML = html;
        }

        // Load last status on page load
        fetch('/status').then(r => r.json()).then(data => {
            if (data.last_sync) renderResult(data.last_sync);
        });
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.get("/status")
async def status():
    return {"last_sync": models.last_sync_result}


@app.get("/auth", response_class=HTMLResponse)
async def auth_page():
    if not picnic.needs_2fa:
        return HTMLResponse("<h3>Already authenticated</h3><p>Auth token: <code>"
                            + picnic.auth_token[:12] + "...</code></p>"
                            "<p><a href='/'>Back</a></p>")
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Picnic 2FA</title>
<style>body{font-family:system-ui;max-width:400px;margin:2rem auto;padding:0 1rem}
input,button{padding:10px 16px;font-size:1rem;border-radius:6px;border:1px solid #ccc;margin:4px}
button{background:#4CAF50;color:white;border:none;cursor:pointer}
#msg{margin-top:1rem;padding:8px;border-radius:4px}</style></head>
<body>
<h2>Picnic 2FA</h2>
<p>Step 1: Request SMS code</p>
<button onclick="requestCode()">Stuur SMS code</button>
<p style="margin-top:1rem">Step 2: Enter the code</p>
<input id="code" placeholder="123456" maxlength="6">
<button onclick="verifyCode()">Verifieer</button>
<div id="msg"></div>
<script>
const msg = document.getElementById('msg');
async function requestCode() {
    msg.textContent = 'Sending...';
    const r = await fetch('/auth/request-code', {method:'POST'});
    const d = await r.json();
    msg.textContent = d.ok ? 'SMS verstuurd!' : 'Fout: ' + d.error;
    msg.style.background = d.ok ? '#e8f5e9' : '#ffebee';
}
async function verifyCode() {
    const code = document.getElementById('code').value;
    if (!code) return;
    msg.textContent = 'Verifying...';
    const r = await fetch('/auth/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code})});
    const d = await r.json();
    msg.textContent = d.ok ? 'Authenticated! Token: ' + d.token_preview + ' — Save as PICNIC_AUTH_TOKEN' : 'Fout: ' + d.error;
    msg.style.background = d.ok ? '#e8f5e9' : '#ffebee';
}
</script></body></html>""")


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


@app.post("/sync", response_model=SyncResult)
async def sync(skip_cache: bool = False):
    items_results: list[SyncItemResult] = []
    added = 0
    no_match_count = 0
    error_count = 0
    pending: list[PendingItem] = []

    shopping_lists = await mealie.get_shopping_lists()
    logger.info("Found %d shopping lists", len(shopping_lists))

    # --- Phase 1: Collect --- Handle cached items, search Picnic for the rest
    for sl in shopping_lists:
        list_items = await mealie.get_list_items(sl["id"])
        if not list_items:
            continue

        logger.info("Processing list '%s' with %d items", sl.get("name", "?"), len(list_items))

        for item in list_items:
            food = item.get("food") or {}
            food_name = food.get("name")
            display = item.get("display", "")
            note = item.get("note")
            raw_quantity = item.get("quantity") or 1
            unit_obj = item.get("unit") or {}
            unit_name = unit_obj.get("name") or unit_obj.get("abbreviation") or None

            # Cart count: only use raw quantity when there's no unit (meaning
            # "3 tomatoes" = 3 items). When a unit like g/ml/cup is present
            # (e.g. "500 gram"), the cart count is always 1.
            if unit_name:
                quantity = 1
            else:
                quantity = max(1, round(raw_quantity))

            ingredient_name = parse_ingredient_name(display, note, food_name)

            try:
                # Check cache in food.extras
                extras = food.get("extras") or {}
                cached_id = extras.get("picnic_product_id")
                cached_name = extras.get("picnic_product_name")

                if cached_id and not skip_cache:
                    await asyncio.to_thread(
                        picnic.add_to_cart, cached_id, quantity
                    )
                    await asyncio.sleep(random.uniform(10, 25))
                    items_results.append(
                        SyncItemResult(
                            name=ingredient_name,
                            status=ItemStatus.cached,
                            picnic_product_id=cached_id,
                            picnic_product_name=cached_name,
                        )
                    )
                    added += 1
                    continue

                # Search Picnic
                await asyncio.sleep(random.uniform(10, 25))
                products = await asyncio.to_thread(
                    picnic.search, ingredient_name
                )

                if not products:
                    items_results.append(
                        SyncItemResult(
                            name=ingredient_name,
                            status=ItemStatus.no_match,
                        )
                    )
                    no_match_count += 1
                    continue

                pending.append(
                    PendingItem(
                        ingredient_name=ingredient_name,
                        products=products,
                        food=food,
                        quantity=quantity,
                        raw_quantity=raw_quantity,
                        unit_name=unit_name,
                    )
                )

            except Exception as exc:
                logger.exception("Error collecting item '%s'", ingredient_name)
                items_results.append(
                    SyncItemResult(
                        name=ingredient_name,
                        status=ItemStatus.error,
                        error=str(exc),
                    )
                )
                error_count += 1

    # --- Phase 2: Match --- LLM batch or fuzzy per-item
    if pending and llm_matcher:
        try:
            requests = [
                MatchRequest(
                    p.ingredient_name,
                    p.products,
                    quantity=p.raw_quantity,
                    unit=p.unit_name,
                )
                for p in pending
            ]
            llm_results = await llm_matcher.match_batch(requests)
            for p, result in zip(pending, llm_results):
                p.matched_product = result.selected_product
                p.llm_matched = result.selected_product is not None
        except Exception:
            logger.warning("LLM matching failed, falling back to fuzzy matching")
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

    # --- Phase 3: Process --- Add to cart, cache, build results
    for p in pending:
        try:
            if p.matched_product is None:
                items_results.append(
                    SyncItemResult(
                        name=p.ingredient_name,
                        status=ItemStatus.no_match,
                    )
                )
                no_match_count += 1
                continue

            product_id = str(p.matched_product["id"])
            product_name = p.matched_product.get("name", "")

            # Cache in Mealie food extras
            if p.food.get("id"):
                await mealie.update_food_extras(
                    p.food["id"],
                    {
                        "picnic_product_id": product_id,
                        "picnic_product_name": product_name,
                    },
                )

            # Add to Picnic cart
            await asyncio.to_thread(
                picnic.add_to_cart, product_id, p.quantity
            )
            await asyncio.sleep(random.uniform(10, 25))

            status = (
                ItemStatus.llm_matched if p.llm_matched else ItemStatus.matched
            )
            items_results.append(
                SyncItemResult(
                    name=p.ingredient_name,
                    status=status,
                    picnic_product_id=product_id,
                    picnic_product_name=product_name,
                )
            )
            added += 1

        except Exception as exc:
            logger.exception("Error processing item '%s'", p.ingredient_name)
            items_results.append(
                SyncItemResult(
                    name=p.ingredient_name,
                    status=ItemStatus.error,
                    error=str(exc),
                )
            )
            error_count += 1

    result = SyncResult(
        timestamp=datetime.now(),
        total_items=len(items_results),
        added_to_cart=added,
        no_match=no_match_count,
        errors=error_count,
        items=items_results,
    )
    models.last_sync_result = result
    logger.info(
        "Sync complete: %d items, %d added, %d no match, %d errors",
        result.total_items,
        result.added_to_cart,
        result.no_match,
        result.errors,
    )
    return result
