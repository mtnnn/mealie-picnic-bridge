import logging

from python_picnic_api import PicnicAPI

logger = logging.getLogger(__name__)


class PicnicClient:
    def __init__(
        self, username: str, password: str, country_code: str = "NL"
    ) -> None:
        self.api = PicnicAPI(
            username=username,
            password=password,
            country_code=country_code,
        )
        logger.info("Picnic client authenticated")

    def search(self, query: str) -> list[dict]:
        results = self.api.search(query)
        # Flatten: search returns categories each containing items
        products: list[dict] = []
        for category in results:
            products.extend(category.get("items", []))
        return products

    def add_to_cart(self, product_id: str, count: int = 1) -> dict:
        logger.info("Adding product %s (count=%d) to cart", product_id, count)
        return self.api.add_product(product_id, count=count)

    def get_cart(self) -> dict:
        return self.api.get_cart()
