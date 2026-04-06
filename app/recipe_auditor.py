import asyncio
import logging

import httpx
from openai import AsyncOpenAI

from app.mealie import MealieClient
from app.models import PhotoCandidate, PhotoSearchResult, RecipePhotoStatus

logger = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/images/search"
DOWNLOAD_TIMEOUT = 60.0


class RecipeAuditor:
    def __init__(
        self,
        mealie: MealieClient,
        mealie_host: str,
        openai_api_key: str = "",
        brave_api_key: str = "",
    ) -> None:
        self.mealie = mealie
        self.mealie_host = mealie_host.rstrip("/")
        self._openai_key = openai_api_key
        self._brave_key = brave_api_key
        self._openai: AsyncOpenAI | None = None
        self._http: httpx.AsyncClient | None = None

    def _get_openai(self) -> AsyncOpenAI:
        if not self._openai_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        if self._openai is None:
            self._openai = AsyncOpenAI(api_key=self._openai_key)
        return self._openai

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT)
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()

    async def get_recipe_photo_statuses(self) -> list[RecipePhotoStatus]:
        recipes = await self.mealie.get_all_recipes()
        result = []
        for r in recipes:
            has_photo = bool(r.get("image"))
            image_url = (
                f"{self.mealie_host}/api/media/recipes/{r['id']}/images/original.webp"
                if has_photo
                else None
            )
            result.append(
                RecipePhotoStatus(
                    id=r["id"],
                    slug=r["slug"],
                    name=r["name"],
                    description=r.get("description") or "",
                    has_photo=has_photo,
                    image_url=image_url,
                )
            )
        return result

    async def search_photos(self, slug: str, prompt_template: str | None = None) -> PhotoSearchResult:
        recipe = await self.mealie.get_recipe(slug)
        dalle_task = asyncio.create_task(self._generate_dalle(recipe, prompt_template=prompt_template))
        brave_task = asyncio.create_task(self._search_brave(recipe))
        dalle_result, brave_result = await asyncio.gather(
            dalle_task, brave_task, return_exceptions=True
        )
        result = PhotoSearchResult(slug=slug)
        if isinstance(dalle_result, Exception):
            logger.warning("DALL-E generation failed for %s: %s", slug, dalle_result)
            result.dalle_error = str(dalle_result)
        else:
            result.dalle_result = dalle_result
        if isinstance(brave_result, Exception):
            logger.warning("Brave search failed for %s: %s", slug, brave_result)
            result.brave_error = str(brave_result)
        else:
            result.brave_results = brave_result
        return result

    async def _generate_dalle(self, recipe: dict, prompt_template: str | None = None) -> PhotoCandidate:
        client = self._get_openai()
        prompt = _build_dalle_prompt(recipe, template=prompt_template)
        response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="hd",
            n=1,
        )
        return PhotoCandidate(
            source="dalle",
            url=response.data[0].url,
            title=f"AI: {recipe['name']}",
        )

    async def _search_brave(self, recipe: dict) -> list[PhotoCandidate]:
        if not self._brave_key:
            raise ValueError("BRAVE_API_KEY is not configured")
        resp = await self._get_http().get(
            BRAVE_URL,
            params={"q": f"{recipe['name']} food recipe", "count": 3},
            headers={"X-Subscription-Token": self._brave_key},
        )
        resp.raise_for_status()
        return [
            PhotoCandidate(
                source="brave",
                url=item.get("thumbnail", {}).get("src", ""),
                title=item.get("title"),
            )
            for item in resp.json().get("results", [])[:3]
        ]

    async def apply_photo(self, slug: str, image_url: str) -> None:
        resp = await self._get_http().get(image_url)
        resp.raise_for_status()
        ext = _ext_from_content_type(resp.headers.get("content-type", "image/jpeg"))
        await self.mealie.upload_recipe_image(slug, resp.content, ext)
        logger.info(
            "Applied photo to recipe %s (ext: %s, %d bytes)", slug, ext, len(resp.content)
        )


DEFAULT_PROMPT_TEMPLATE = (
    "editorial food photography of {name}, "
    "{description} "
    "Key ingredients: {ingredients}. "
    "Beautiful plating on a rustic ceramic dish, warm natural side light, "
    "shallow depth of field, 45-degree angle, high detail, "
    "soft golden highlights, scattered fresh herbs and subtle garnish around the plate, "
    "Bon Appétit magazine quality, professional food styling"
)


def _build_dalle_prompt(recipe: dict, template: str | None = None) -> str:
    name = recipe.get("name", "dish")
    desc = (recipe.get("description") or "")[:120]
    ingredients_list = [
        i.get("note") or i.get("title") or ""
        for i in recipe.get("recipeIngredient", [])[:5]
        if i.get("note") or i.get("title")
    ]
    ingredients = ", ".join(ingredients_list) if ingredients_list else ""

    tmpl = template or DEFAULT_PROMPT_TEMPLATE
    return tmpl.format(
        name=name,
        description=desc,
        ingredients=ingredients,
    )


def _ext_from_content_type(content_type: str) -> str:
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }
    return mapping.get(content_type.split(";")[0].strip().lower(), "jpg")
