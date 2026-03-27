import asyncio
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import models
from app.config import settings
from app.matcher import find_best_match, parse_ingredient_name
from app.mealie import MealieClient
from app.models import ItemStatus, SyncItemResult, SyncResult
from app.picnic_client import PicnicClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mealie: MealieClient
picnic: PicnicClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mealie, picnic
    mealie = MealieClient(settings.MEALIE_HOST, settings.MEALIE_TOKEN)
    picnic = PicnicClient(
        username=settings.PICNIC_USERNAME,
        password=settings.PICNIC_PASSWORD,
        country_code=settings.PICNIC_COUNTRY_CODE,
        auth_token=settings.PICNIC_AUTH_TOKEN,
    )
    logger.info("Clients initialized")
    yield
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
        .cached { color: #1565c0; }
        .no_match { color: #f57f17; }
        .error { color: #c62828; }
    </style>
</head>
<body>
    <h1>Mealie Picnic Bridge</h1>
    <button id="syncBtn" onclick="doSync()">Sync naar Picnic</button>
    <div id="status"></div>

    <script>
        async function doSync() {
            const btn = document.getElementById('syncBtn');
            const status = document.getElementById('status');
            btn.disabled = true;
            btn.textContent = 'Bezig met sync...';
            status.innerHTML = '';
            try {
                const resp = await fetch('/sync', { method: 'POST' });
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


@app.post("/sync", response_model=SyncResult)
async def sync():
    items_results: list[SyncItemResult] = []
    added = 0
    no_match_count = 0
    error_count = 0

    shopping_lists = await mealie.get_shopping_lists()
    logger.info("Found %d shopping lists", len(shopping_lists))

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
            quantity = max(1, round(item.get("quantity", 1) or 1))

            ingredient_name = parse_ingredient_name(display, note, food_name)

            try:
                # Check cache in food.extras
                extras = food.get("extras") or {}
                cached_id = extras.get("picnic_product_id")
                cached_name = extras.get("picnic_product_name")

                if cached_id:
                    # Use cached mapping
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

                # Search Picnic (delay to mimic human behaviour)
                await asyncio.sleep(random.uniform(10, 25))
                products = await asyncio.to_thread(
                    picnic.search, ingredient_name
                )
                match = find_best_match(
                    ingredient_name, products, settings.FUZZY_THRESHOLD
                )

                if match is None:
                    items_results.append(
                        SyncItemResult(
                            name=ingredient_name,
                            status=ItemStatus.no_match,
                        )
                    )
                    no_match_count += 1
                    continue

                product, score = match
                product_id = str(product["id"])
                product_name = product.get("name", "")

                # Cache the mapping in Mealie food extras
                if food.get("id"):
                    await mealie.update_food_extras(
                        food["id"],
                        {
                            "picnic_product_id": product_id,
                            "picnic_product_name": product_name,
                        },
                    )

                # Add to Picnic cart
                await asyncio.to_thread(
                    picnic.add_to_cart, product_id, quantity
                )
                await asyncio.sleep(random.uniform(10, 25))

                items_results.append(
                    SyncItemResult(
                        name=ingredient_name,
                        status=ItemStatus.matched,
                        picnic_product_id=product_id,
                        picnic_product_name=product_name,
                        score=score,
                    )
                )
                added += 1

            except Exception as exc:
                logger.exception("Error processing item '%s'", ingredient_name)
                items_results.append(
                    SyncItemResult(
                        name=ingredient_name,
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
