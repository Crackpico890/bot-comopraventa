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
from .ai_check import AICheck
from .models import Listing
from .notifier import Telegram
from .pricing import (defect_reason, evaluate, market_from_prices, matches_product, normalize,
                      product_base_words, signature, sold_summary)
from .report import build_report, main_variant_prices
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
        self.alert_times = []
        self.sold_stats = {name: [0, 0] for name in self.sources}   # [desaparecidos, comprobados]
        self.sold_disabled = set()
        self.ai = AICheck.from_env()

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
        for l in fresh:
            await self.consider(l, product)
        m = self.market(product, main_variant_prices(self.store, product, self.cfg["market_window_days"]))
        if m:
            sold = sold_summary(self.store.sold_stats(product["name"], 1))
            self.store.save_snapshot(product["name"], m, self.store.count_new(product["name"]),
                                     sold[2] if sold else 0)
            log.info("mercado %s: mediana %.0f € reventa %.0f € (%d anuncios)",
                     product["name"], m.median, m.resale, m.samples)
        self.last_refresh[product["name"]] = time.time()

    def market(self, product: dict, prices=None):
        if prices is None:
            prices = self.store.prices(product["name"], self.cfg["market_window_days"])
        return market_from_prices(prices, self.cfg["resale_percentile"], self.cfg["historic_percentile"])

    def comparable_market(self, l, product: dict, exclude_key: str = None):
        """Precio de mercado y ventas solo de anuncios de la MISMA variante que `l`."""
        base = product_base_words(product)
        sig = signature(l.title, base)
        rows = self.store.comparables(product["name"], self.cfg["market_window_days"])
        prices = [price for key, title, price in rows
                  if key != exclude_key and signature(title, base) == sig]
        sold_rows = [r for r in self.store.sold_stats(product["name"], 14)
                     if signature(r[2], base) == sig]
        if l.platform in self.sold_disabled:
            sold_rows = []
        return self.market(product, prices), sold_summary(sold_rows)

    async def scan(self, product: dict):
        name = product["name"]
        if time.time() - self.last_refresh.get(name, 0) > self.cfg["market_refresh_h"] * 3600:
            await self.refresh_market(product)
        for platform in product["platforms"]:
            listings = await self.fetch(platform, product, "newest", pages=1)
            for l in listings or []:
                valid = matches_product(l, product)
                self.store.upsert(name, l, valid)
                if valid:
                    await self.consider(l, product)

    async def consider(self, l, product):
        if not self.is_reachable(l) or self.store.already_alerted(l.key):
            return
        # El propio anuncio no cuenta para calcular su precio de mercado.
        market, sold = self.comparable_market(l, product, exclude_key=l.key)
        if not market:
            return
        deal = evaluate(l, product, market, self.cfg, sold)
        if deal and not await self.looks_perfect(l, product):
            return
        if deal:
            log.info("%s: %s %.0f € -> +%.0f € (cat %s, %d iguales) %s", deal.level, l.title, l.price,
                     deal.profit, l.extra.get("category"), market.samples, l.url)
            now = time.time()
            self.alert_times = [t for t in self.alert_times if now - t < 3600]
            if len(self.alert_times) >= self.cfg["rules"].get("max_alerts_per_hour", 3):
                # Demasiados avisos seguidos suele ser señal de un precio de mercado mal calculado.
                log.warning("límite de avisos por hora alcanzado, no se envía: %s", l.url)
                return
            self.alert_times.append(now)
            await self.tg.send_deal(deal)
            self.store.save_alert(deal)

    async def looks_perfect(self, l, product) -> bool:
        """Antes de avisar, lee el anuncio completo: si tiene defectos, no es un chollazo."""
        src = self.sources.get(l.platform)
        if not hasattr(src, "details"):
            return True
        try:
            d = await src.details(l.item_id)
        except (httpx.HTTPError, ValueError):
            d = None
        if d:
            l.description = d["description"] or l.description
            l.extra["condition"] = d["condition"] or l.extra.get("condition")
        if not matches_product(l, product):
            log.info("descartado al leer el anuncio completo: %s %s (%s)", l.title, l.url,
                     defect_reason(normalize(l.title + " " + l.description)) or "filtros")
            self.store.upsert(product["name"], l, False)
            return False
        if self.ai:
            verdict = await self.ai.review(l, product)
            if verdict and not verdict[0]:
                log.info("descartado por la IA: %s %s (%s)", l.title, l.url, verdict[1])
                self.store.upsert(product["name"], l, False)
                return False
            l.extra["ai"] = verdict[1] if verdict else None
            l.extra["ai_checked"] = verdict is not None
        return True

    async def check_sold(self):
        """Comprueba anuncios antiguos: si han desaparecido, cuentan como venta (precio y rapidez)."""
        for platform, src in self.sources.items():
            if platform not in self.cfg.get("enabled_platforms", self.sources) \
                    or platform in self.sold_disabled \
                    or time.time() < self.paused_until[platform]:
                continue
            for key in self.store.to_check(platform, self.cfg["sold_checks_per_cycle"]):
                try:
                    gone = await src.is_gone(key.split(":", 1)[1])
                except (httpx.HTTPError, ValueError):
                    gone = None
                if gone is not None:
                    self.store.checked(key, gone)
                    stats = self.sold_stats[platform]
                    stats[0] += int(gone)
                    stats[1] += 1
                    # Si casi todo sale "vendido", la comprobación no funciona: mejor no usarla.
                    if stats[1] >= 40 and stats[0] / stats[1] > 0.9:
                        log.warning("%s: %d de %d anuncios salen como vendidos; se desactiva la "
                                    "detección de ventas", platform, stats[0], stats[1])
                        self.sold_disabled.add(platform)
                        break
                await self.pause()

    async def maybe_report(self):
        now = datetime.now(TZ)
        if now.hour == self.cfg["daily_report_hour"] and self.last_report_day != now.date():
            self.last_report_day = now.date()
            await self.tg.send(build_report(self.store, self.cfg))

    def revalidate(self):
        """Si se han mejorado los filtros, lo guardado con los antiguos deja de contar."""
        for p in self.cfg["products"]:
            n = self.store.revalidate(
                p["name"], lambda t, price, p=p: matches_product(Listing("", "", t, price, ""), p))
            if n:
                log.info("%s: %d anuncios guardados ya no pasan los filtros", p["name"], n)

    async def run(self):
        self.revalidate()
        n = len(self.cfg["products"])
        plats = " y ".join(p.capitalize() for p in self.cfg.get("enabled_platforms", self.sources))
        ia = "activada" if self.ai else "desactivada (falta ANTHROPIC_API_KEY)"
        await self.tg.send(f"🤖 Bot de chollos arrancado. Vigilando {n} productos en {plats}. "
                           f"Revisión de descripciones con IA: {ia}.")
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
