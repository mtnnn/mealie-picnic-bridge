import hashlib
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API_VERSION = "15"
_BASE_URL = "https://storefront-prod.{cc}.picnicinternational.com/api/{v}"

_COMMON_HEADERS = {
    "User-Agent": "okhttp/3.12.2",
    "Content-Type": "application/json; charset=UTF-8",
}
_PICNIC_HEADERS = {
    "x-picnic-agent": "30100;1.15.232-15154",
    "x-picnic-did": "3C417201548B2E3B",
}


class PicnicClient:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        country_code: str = "NL",
        auth_token: str = "",
    ) -> None:
        self._base = _BASE_URL.format(cc=country_code.lower(), v=_API_VERSION)
        self._auth_token = auth_token
        self._client = httpx.Client(base_url=self._base, headers=_COMMON_HEADERS, timeout=30)

        if auth_token:
            logger.info("Picnic client initialized with auth token")
        elif username and password:
            self._login(username, password)
        else:
            raise ValueError("Provide either auth_token or username+password")

        logger.info("Picnic base URL: %s", self._base)

    def _login(self, username: str, password: str) -> None:
        secret = hashlib.md5(password.encode()).hexdigest()
        resp = self._request(
            "POST",
            "/user/login",
            json={"key": username, "secret": secret, "client_id": 30100},
            authenticated=False,
        )
        self._auth_token = resp.headers.get("x-picnic-auth", "")
        if not self._auth_token:
            raise RuntimeError("Login failed: no x-picnic-auth header in response")

        body = resp.json()
        self._needs_2fa = body.get("second_factor_authentication_required", False)
        if self._needs_2fa:
            logger.warning("Picnic account requires 2FA — use /auth to complete")
        else:
            logger.info("Picnic client authenticated with credentials")

    def _request(
        self, method: str, path: str, *, authenticated: bool = True, **kwargs: Any
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if authenticated and self._auth_token:
            headers["x-picnic-auth"] = self._auth_token
        resp = self._client.request(method, path, headers=headers, **kwargs)
        if not resp.is_success:
            logger.error(
                "Picnic API %s %s → %d: %s",
                method, path, resp.status_code, resp.text[:500],
            )
        resp.raise_for_status()
        return resp

    @staticmethod
    def _extract_selling_units(obj: Any) -> list[dict]:
        """Recursively extract sellingUnit objects from Fusion page response."""
        results: list[dict] = []
        if isinstance(obj, dict):
            if "sellingUnit" in obj:
                su = obj["sellingUnit"]
                if isinstance(su, dict) and "id" in su:
                    results.append(su)
            for v in obj.values():
                results.extend(PicnicClient._extract_selling_units(v))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(PicnicClient._extract_selling_units(item))
        return results

    def _try_search(self, query: str) -> list[dict]:
        try:
            resp = self._request(
                "GET",
                "/pages/search-page-results",
                params={"search_term": query},
                headers=_PICNIC_HEADERS,
            )
            return self._extract_selling_units(resp.json())
        except Exception:
            logger.warning("Picnic search failed for '%s'", query, exc_info=True)
            return []

    def search(self, query: str) -> list[dict]:
        products = self._try_search(query)

        # Retry: split camelCase ("cottageCheese" -> "cottage Cheese")
        if not products:
            spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", query)
            if spaced != query:
                logger.info("Retrying search with '%s' (was '%s')", spaced, query)
                products = self._try_search(spaced)

        # Retry: truncated query for compound words without spaces
        if not products and " " not in query and len(query) > 6:
            truncated = query[: len(query) // 2 + 1]
            logger.info("Retrying search with '%s' (truncated from '%s')", truncated, query)
            products = self._try_search(truncated)

        logger.info("Search '%s' -> %d products", query, len(products))
        if products:
            top = products[0]
            logger.info(
                "Top: id=%s name='%s' price=%s",
                top.get("id"), top.get("name"), top.get("display_price"),
            )
        return products

    def add_to_cart(self, product_id: str, count: int = 1) -> dict:
        logger.info("Adding product %s (count=%d) to cart", product_id, count)
        resp = self._request(
            "POST",
            "/cart/add_product",
            json={"product_id": product_id, "count": count},
        )
        return resp.json()

    @property
    def needs_2fa(self) -> bool:
        return getattr(self, "_needs_2fa", False)

    @property
    def auth_token(self) -> str:
        return self._auth_token

    def request_2fa_code(self) -> None:
        try:
            self._request(
                "POST", "/user/2fa/generate",
                json={"channel": "SMS"},
                headers=_PICNIC_HEADERS,
            )
        except httpx.HTTPStatusError as exc:
            # Picnic may return empty body causing issues, but 200 is fine
            if exc.response.status_code != 200:
                raise
        logger.info("2FA code requested via SMS")

    def verify_2fa_code(self, code: str) -> None:
        resp = self._client.request(
            "POST", "/user/2fa/verify",
            json={"otp": code},
            headers={
                "x-picnic-auth": self._auth_token,
                **_PICNIC_HEADERS,
            },
        )
        if not resp.is_success:
            logger.error("2FA verify failed: %d %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        new_token = resp.headers.get("x-picnic-auth")
        if new_token:
            self._auth_token = new_token
        self._needs_2fa = False
        logger.info("2FA verified — auth token: %s...", self._auth_token[:12])

    def close(self) -> None:
        self._client.close()

    def get_cart(self) -> dict:
        resp = self._request("GET", "/cart")
        return resp.json()
