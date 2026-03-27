import logging

import httpx

logger = logging.getLogger(__name__)


class MealieClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def get_shopping_lists(self) -> list[dict]:
        lists: list[dict] = []
        page = 1
        while True:
            resp = await self.client.get(
                "/api/households/shopping/lists",
                params={"page": page, "perPage": 50},
            )
            resp.raise_for_status()
            data = resp.json()
            lists.extend(data.get("items", []))
            if page >= data.get("totalPages", 1):
                break
            page += 1
        return lists

    async def get_list_items(self, list_id: str) -> list[dict]:
        resp = await self.client.get(
            f"/api/households/shopping/lists/{list_id}"
        )
        resp.raise_for_status()
        return resp.json().get("listItems", [])

    async def update_food_extras(
        self, food_id: str, extras: dict
    ) -> None:
        # GET current food to preserve existing extras
        resp = await self.client.get(f"/api/foods/{food_id}")
        resp.raise_for_status()
        food = resp.json()

        current_extras = food.get("extras", {}) or {}
        current_extras.update(extras)

        resp = await self.client.put(
            f"/api/foods/{food_id}",
            json={**food, "extras": current_extras},
        )
        resp.raise_for_status()
        logger.info("Updated food %s extras: %s", food_id, extras)
