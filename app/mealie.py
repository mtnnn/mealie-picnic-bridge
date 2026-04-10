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

    async def get_all_recipes(self) -> list[dict]:
        recipes: list[dict] = []
        page = 1
        while True:
            resp = await self.client.get(
                "/api/recipes",
                params={"page": page, "perPage": 50},
            )
            resp.raise_for_status()
            data = resp.json()
            recipes.extend(data.get("items", []))
            if page >= data.get("totalPages", 1):
                break
            page += 1
        return recipes

    async def get_recipe(self, slug: str) -> dict:
        resp = await self.client.get(f"/api/recipes/{slug}")
        resp.raise_for_status()
        return resp.json()

    async def upload_recipe_image(
        self, slug: str, image_bytes: bytes, extension: str
    ) -> None:
        resp = await self.client.put(
            f"/api/recipes/{slug}/image",
            files={"image": (f"image.{extension}", image_bytes, "image/jpeg" if extension == "jpg" else f"image/{extension}")},
            data={"extension": extension},
        )
        resp.raise_for_status()
        logger.info("Uploaded image to recipe %s", slug)

    async def get_all_foods(self) -> list[dict]:
        foods: list[dict] = []
        page = 1
        while True:
            resp = await self.client.get(
                "/api/foods",
                params={"page": page, "perPage": 50},
            )
            resp.raise_for_status()
            data = resp.json()
            foods.extend(data.get("items", []))
            if page >= data.get("totalPages", 1):
                break
            page += 1
        return foods

    async def clear_food_picnic_cache(self, food_id: str, food: dict) -> bool:
        """Remove picnic_product_id/name from a food's extras. Returns True if changed."""
        extras = food.get("extras") or {}
        if "picnic_product_id" not in extras and "picnic_product_name" not in extras:
            return False
        extras.pop("picnic_product_id", None)
        extras.pop("picnic_product_name", None)
        resp = await self.client.put(
            f"/api/foods/{food_id}",
            json={**food, "extras": extras},
        )
        resp.raise_for_status()
        return True

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

    async def parse_ingredients(
        self, ingredients: list[str], parser: str = "nlp"
    ) -> list[dict]:
        """Call Mealie's ingredient parser API."""
        resp = await self.client.post(
            "/api/parser/ingredients",
            json={"parser": parser, "ingredients": ingredients},
        )
        resp.raise_for_status()
        return resp.json()

    async def update_recipe(self, slug: str, data: dict) -> dict:
        """Full update of a recipe via PUT /api/recipes/{slug}."""
        resp = await self.client.put(f"/api/recipes/{slug}", json=data)
        if resp.status_code == 422:
            logger.error("Recipe update 422 for %s: %s", slug, resp.text[:1000])
        resp.raise_for_status()
        return resp.json()

    async def create_food(self, name: str) -> dict:
        """Create a new food in Mealie."""
        resp = await self.client.post("/api/foods", json={"name": name})
        resp.raise_for_status()
        return resp.json()

    async def search_foods(self, query: str) -> list[dict]:
        """Search foods by name."""
        resp = await self.client.get(
            "/api/foods",
            params={"search": query, "perPage": 10},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
