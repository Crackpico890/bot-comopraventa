"""Búsqueda en Wallapop usando la API que usa su propia web (es.wallapop.com)."""
import logging
from typing import Optional

import httpx

from ..models import Listing

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.wallapop.com/api/v3/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Origin": "https://es.wallapop.com",
    "Referer": "https://es.wallapop.com/",
    "X-DeviceOS": "0",
}


class Wallapop:
    name = "wallapop"

    def __init__(self, client: httpx.AsyncClient, lat: float, lon: float):
        self.client = client
        self.lat = lat
        self.lon = lon

    async def search(self, query: str, *, min_price: float = 0, max_price: Optional[float] = None,
                     order: str = "newest", distance_km: Optional[int] = None,
                     pages: int = 1) -> list:
        params = {
            "keywords": query,
            "latitude": self.lat,
            "longitude": self.lon,
            "order_by": order,          # "newest" | "most_relevance" | "price_low_to_high"
            "source": "search_box",
            "min_sale_price": int(min_price),
        }
        if max_price:
            params["max_sale_price"] = int(max_price)
        if distance_km:
            params["distance"] = int(distance_km * 1000)

        results = []
        next_page = None
        for _ in range(pages):
            p = dict(params)
            if next_page:
                p = {"next_page": next_page, "source": "search_box"}
            r = await self.client.get(SEARCH_URL, params=p, headers=HEADERS)
            r.raise_for_status()
            data = r.json()
            items, next_page = _extract(data)
            results.extend(i for i in (_parse(x) for x in items) if i)
            if not next_page:
                break
        return results

    async def is_gone(self, item_id: str) -> Optional[bool]:
        """True si el anuncio se ha vendido o borrado, None si no se puede saber."""
        r = await self.client.get(f"https://api.wallapop.com/api/v3/items/{item_id}", headers=HEADERS)
        if r.status_code in (404, 410):
            return True
        if r.status_code != 200:
            return None
        flags = r.json().get("flags") or {}
        return bool(flags.get("sold") or flags.get("expired") or flags.get("banned"))


def _extract(data: dict):
    # La API ha cambiado de forma varias veces; soportamos los formatos conocidos.
    if "data" in data:
        payload = data["data"].get("section", {}).get("payload", {})
        items = payload.get("items", [])
    else:
        items = data.get("search_objects", [])
    next_page = (data.get("meta") or {}).get("next_page")
    return items, next_page


def _parse(x: dict) -> Optional[Listing]:
    try:
        price = x.get("price")
        price = price.get("amount") if isinstance(price, dict) else price
        slug = x.get("web_slug") or x.get("id")
        images = x.get("images") or []
        img = None
        if images:
            urls = images[0].get("urls") or {}
            img = urls.get("medium") or urls.get("big") or images[0].get("medium")
        loc = x.get("location") or {}
        shipping = x.get("shipping") or {}
        reserved = x.get("reserved") or {}
        created = x.get("created_at") or x.get("modified_at")
        return Listing(
            platform="wallapop",
            item_id=str(x["id"]),
            title=x.get("title", ""),
            description=x.get("description", ""),
            price=float(price),
            url=f"https://es.wallapop.com/item/{slug}",
            image=img,
            location=loc.get("city", ""),
            shipping=bool(shipping.get("user_allows_shipping", x.get("shipping_allowed", False))),
            reserved=bool(reserved.get("flag", False)) if isinstance(reserved, dict) else bool(reserved),
            created_ts=(created / 1000 if created and created > 1e11 else created),
            extra={"lat": loc.get("latitude"), "lon": loc.get("longitude")},
        )
    except (KeyError, TypeError, ValueError) as e:
        log.debug("anuncio wallapop ignorado: %s", e)
        return None
