"""Bucle principal: vigila Wallapop y Vinted 24/7 y avisa por Telegram de los chollazos."""
import asyncio
import logging
import math
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from . import config as config_mod
from .notifier import Telegram
from .pricing import evaluate, market_from_prices, matches_product, sold_summary
from .report import build_report
from .sources.vinted import Vinted
from .sources.wallapop import Wallapop
from .storage import Store

log = logging.getLogger("bot")
TZ = ZoneInfo("Europe/Madrid")


def km_between(lat1, lon1, lat2, lon2) -> float:
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.store = Store(cfg["db_path"])
        self.client = httpx.AsyncClient(timeout=25, follow_redirects=True, http2=False)
        loc = cfg["location"]
        self.sources = {
            "wallapop": Wallapop(self.client, loc["lat"], loc["lon"]),
            "vinted": Vinted(self.client),
        }
        self.tg = Telegram(self.client, cfg["telegram_token"], cfg["telegram_chat_id"], cfg["dry_run"])
        self.paused_until = {name: 0.0 for name in self.sources}
        self.last_refresh = {}
        self.last_report_day = None

    async def pause(self):
        lo, hi = self.cfg["request_delay_s"]
        await asyncio.sleep(random.uniform(lo, hi))

    async def fetch(self, platform: str, product: dict, order: str, pages: int):
        if time.time() < self.paused_until[platform]:
            return None
        try:
            return await self.sources[platform].search(
                product["query"], min_price=product.get("min_price", 0),
                max_price=product.get("max_price"), order=order, pages=pages)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code in (403, 429):
                # Nos están frenando: paramos esa plataforma 15 minutos.
                log.warning("%s respondió %s, pausa de 15 min", platform, code)
                self.paused_until[platform] = time.time() + 900
            else:
                log.warning("%s error %s buscando %s", platform, code, product["name"])
        except (httpx.HTTPError, ValueError) as e:
            log.warning("%s fallo buscando %s: %s", platform, product["name"], e)
        finally:
            await self.pause()
        return None

    def is_reachable(self, listing) -> bool:
        if listing.shipping:
            return True
        lat, lon = listing.extra.get("lat"), listing.extra.get("lon")
        if lat is None or lon is None:
            return False
        loc = self.cfg["location"]
        return km_between(loc["lat"], loc["lon"], lat, lon) <= loc["local_radius_km"]

    async def refresh_market(self, product: dict):
        """Muestreo amplio (varias páginas por relevancia) para conocer el precio real."""
        fresh = []
        for platform in product["platforms"]:
            listings = await self.fetch(platform, product, "most_relevance", pages=3)
            for l in listings or []:
                valid = matches_product(l, product)
                self.store.upsert(product["name"], l, valid)
                if valid:
                    fresh.append(l)
        m = self.market(product)
        if m:
            sold14 = sold_summary(self.store.sold_stats(product["name"], 14))
            for l in fresh:
                await self.consider(l, product, m, sold14)
            sold = sold_summary(self.store.sold_stats(product["name"], 1))
            self.store.save_snapshot(product["name"], m, self.store.count_new(product["name"]),
                                     sold[2] if sold else 0)
            log.info("mercado %s: mediana %.0f € reventa %.0f € (%d anuncios)",
                     product["name"], m.median, m.resale, m.samples)
        self.last_refresh[product["name"]] = time.time()

    def market(self, product: dict):
        return market_from_prices(self.store.prices(product["name"], self.cfg["market_window_days"]),
                                  self.cfg["resale_percentile"], self.cfg["historic_percentile"])

    async def scan(self, product: dict):
        name = product["name"]
        if time.time() - self.last_refresh.get(name, 0) > self.cfg["market_refresh_h"] * 3600:
            await self.refresh_market(product)
        for platform in product["platforms"]:
            listings = await self.fetch(platform, product, "newest", pages=1)
            if not listings:
                continue
            market = self.market(product)
            sold = sold_summary(self.store.sold_stats(name, 14))
            for l in listings:
                valid = matches_product(l, product)
                # Evaluamos antes de guardar para no contaminar el precio de mercado con el propio anuncio.
                if valid and market:
                    await self.consider(l, product, market, sold)
                self.store.upsert(name, l, valid)

    async def consider(self, l, product, market, sold):
        if not self.is_reachable(l) or self.store.already_alerted(l.key):
            return
        deal = evaluate(l, product, market, self.cfg, sold)
        if deal:
            log.info("%s: %s %.0f € -> +%.0f €", deal.level, l.title, l.price, deal.profit)
            await self.tg.send_deal(deal)
            self.store.save_alert(deal)

    async def check_sold(self):
        """Comprueba anuncios antiguos: si han desaparecido, cuentan como venta (precio y rapidez)."""
        for platform, src in self.sources.items():
            if platform not in self.cfg.get("enabled_platforms", self.sources) \
                    or time.time() < self.paused_until[platform]:
                continue
            for key in self.store.to_check(platform, self.cfg["sold_checks_per_cycle"]):
                try:
                    gone = await src.is_gone(key.split(":", 1)[1])
                except httpx.HTTPError:
                    gone = None
                if gone is not None:
                    self.store.checked(key, gone)
                await self.pause()

    async def maybe_report(self):
        now = datetime.now(TZ)
        if now.hour == self.cfg["daily_report_hour"] and self.last_report_day != now.date():
            self.last_report_day = now.date()
            await self.tg.send(build_report(self.store, self.cfg))

    async def run(self):
        n = len(self.cfg["products"])
        plats = " y ".join(p.capitalize() for p in self.cfg.get("enabled_platforms", self.sources))
        await self.tg.send(f"🤖 Bot de chollos arrancado. Vigilando {n} productos en {plats}.")
        while True:
            start = time.time()
            products = list(self.cfg["products"])
            random.shuffle(products)
            for p in products:
                try:
                    await self.scan(p)
                except Exception:
                    log.exception("error inesperado con %s", p["name"])
            try:
                await self.check_sold()
                await self.maybe_report()
            except Exception:
                log.exception("error en tareas de mercado")
            elapsed = time.time() - start
            log.info("ciclo completo en %.0f s", elapsed)
            await asyncio.sleep(max(30, self.cfg["scan_interval_s"] - elapsed))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx registra cada URL en INFO, y la de Telegram lleva el token dentro.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config_mod.load()
    asyncio.run(Bot(cfg).run())


if __name__ == "__main__":
    main()
