"""Búsqueda en Vinted usando la API interna de www.vinted.es."""
import logging
import time
from typing import Optional

import httpx

from ..models import Listing

log = logging.getLogger(__name__)

BASE = "https://www.vinted.es"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
}


class Vinted:
    name = "vinted"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._cookie_ts = 0.0

    async def _ensure_session(self, force: bool = False):
        # Vinted exige la cookie de sesión que da al visitar la portada. Se renueva cada hora.
        if force or time.time() - self._cookie_ts > 3600:
            self.client.cookies.clear()
            r = await self.client.get(BASE + "/", headers={**HEADERS, "Accept": "text/html"})
            r.raise_for_status()
            self._cookie_ts = time.time()

    async def search(self, query: str, *, min_price: float = 0, max_price: Optional[float] = None,
                     order: str = "newest", pages: int = 1, **_) -> list:
        await self._ensure_session()
        order_map = {"newest": "newest_first", "most_relevance": "relevance",
                     "price_low_to_high": "price_low_to_high"}
        results = []
        for page in range(1, pages + 1):
            params = {"search_text": query, "order": order_map.get(order, order),
                      "per_page": 96, "page": page, "currency": "EUR"}
            if min_price:
                params["price_from"] = int(min_price)
            if max_price:
                params["price_to"] = int(max_price)
            r = await self.client.get(BASE + "/api/v2/catalog/items", params=params, headers=HEADERS)
            if r.status_code in (401, 403):
                await self._ensure_session(force=True)
                r = await self.client.get(BASE + "/api/v2/catalog/items", params=params, headers=HEADERS)
            r.raise_for_status()
            items = r.json().get("items", [])
            results.extend(i for i in (_parse(x) for x in items) if i)
            if len(items) < 96:
                break
        return results

    async def is_gone(self, item_id: str) -> Optional[bool]:
        await self._ensure_session()
        r = await self.client.get(f"{BASE}/api/v2/items/{item_id}", headers=HEADERS)
        if r.status_code in (404, 410):
            return True
        if r.status_code != 200:
            return None
        item = r.json().get("item") or {}
        return bool(item.get("is_closed") or item.get("is_hidden") or item.get("item_closing_action"))


def _parse(x: dict) -> Optional[Listing]:
    try:
        price = x.get("price")
        price = price.get("amount") if isinstance(price, dict) else price
        photo = x.get("photo") or {}
        url = x.get("url") or f"{BASE}/items/{x['id']}"
        if url.startswith("/"):
            url = BASE + url
        ts = (photo.get("high_resolution") or {}).get("timestamp")
        return Listing(
            platform="vinted",
            item_id=str(x["id"]),
            title=x.get("title", ""),
            description=" ".join(filter(None, [x.get("brand_title"), x.get("status")])),
            price=float(price),
            url=url,
            image=photo.get("url"),
            shipping=True,   # en Vinted todo va con envío
            reserved=bool(x.get("is_reserved", False)),
            created_ts=ts,
        )
    except (KeyError, TypeError, ValueError) as e:
        log.debug("anuncio vinted ignorado: %s", e)
        return None
