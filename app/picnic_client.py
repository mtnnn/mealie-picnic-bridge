import logging
import os
import re

from python_picnic_api2 import PicnicAPI

logger = logging.getLogger(__name__)

# Disable SSL verification if no custom CA bundle is configured.
# Needed when running behind a proxy/firewall with self-signed certs.
if not os.environ.get("REQUESTS_CA_BUNDLE"):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    os.environ["CURL_CA_BUNDLE"] = ""
    _SSL_VERIFY = False
else:
    _SSL_VERIFY = True


class PicnicClient:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        country_code: str = "NL",
        auth_token: str = "",
    ) -> None:
        if not _SSL_VERIFY:
            from python_picnic_api2.session import PicnicAPISession
            _orig_init = PicnicAPISession.__init__
            def _patched_init(self_session, *args, **kwargs):
                _orig_init(self_session, *args, **kwargs)
                self_session.verify = False
            PicnicAPISession.__init__ = _patched_init

        if auth_token:
            self.api = PicnicAPI(
                auth_token=auth_token,
                country_code=country_code,
            )
            logger.info("Picnic client initialized with auth token")
        else:
            self.api = PicnicAPI(
                username=username,
                password=password,
                country_code=country_code,
            )
            logger.info("Picnic client authenticated with credentials")

        if not _SSL_VERIFY:
            self.api.session.verify = False
            PicnicAPISession.__init__ = _orig_init  # restore

        logger.info("Picnic base URL: %s", self.api._base_url)

    def _flatten_results(self, groups: list) -> list[dict]:
        products = []
        for group in groups:
            if isinstance(group, dict) and "items" in group:
                products.extend(group["items"])
            elif isinstance(group, dict) and "id" in group:
                products.append(group)
        return products

    def _try_search(self, query: str) -> list[dict]:
        try:
            groups = self.api.search(query)
            return self._flatten_results(groups)
        except Exception:
            logger.warning("Picnic search failed for '%s'", query, exc_info=True)
            return []

    def search(self, query: str) -> list[dict]:
        products = self._try_search(query)

        # Retry: split camelCase ("cottageCheese" → "cottage Cheese")
        if not products:
            spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", query)
            if spaced != query:
                logger.info("Retrying search with '%s' (was '%s')", spaced, query)
                products = self._try_search(spaced)

        # Retry: truncated query for compound words without spaces
        # "cottagecheese" → search "cottage" (first half+1), Picnic fuzzy-matches the rest
        if not products and " " not in query and len(query) > 6:
            truncated = query[: len(query) // 2 + 1]
            logger.info("Retrying search with '%s' (truncated from '%s')", truncated, query)
            products = self._try_search(truncated)

        logger.info("Search '%s' → %d products", query, len(products))
        if products:
            top = products[0]
            logger.info(
                "Top: id=%s name='%s' price=%s",
                top.get("id"), top.get("name"), top.get("display_price"),
            )
        return products

    def add_to_cart(self, product_id: str, count: int = 1) -> dict:
        logger.info("Adding product %s (count=%d) to cart", product_id, count)
        return self.api.add_product(product_id, count=count)

    def get_cart(self) -> dict:
        return self.api.get_cart()
