"""Filtrado de anuncios, estimación del precio de mercado y cálculo del beneficio."""
import re
import statistics
import unicodedata
from functools import lru_cache
from dataclasses import dataclass
from typing import Iterable, Optional

from .models import Listing

# Si aparecen en el título, el anuncio no es el producto completo (accesorio, compra, cambio...).
TITLE_EXCLUDE = [
    "busco", "compro", "cambio", "intercambio", "funda", "carcasa", "protector",
    "cristal templado", "pegatina", "skin", "soporte", "cargador", "cable", "repuesto",
    "caja vacia", "solo caja", "sin consola", "piezas", "despiece", "alquilo", "alquiler",
    "reservado", "lote de juegos", "solo mando",
]
# Si aparecen en el título o la descripción, el producto tiene un problema.
TEXT_EXCLUDE = [
    "roto", "rota", "averiado", "averiada", "no funciona", "no enciende", "para piezas",
    "pantalla rota", "replica", "imitacion", "falso", "fake", "bloqueado", "bloqueada",
    "icloud", "leer descripcion", "sin probar", "a reparar", "para reparar",
    "clon", "1:1", "aaa",
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


def _contains(haystack: str, word: str) -> bool:
    # Admite el plural: "juego" también descarta "juegos", "mando" descarta "mandos".
    return re.search(r"\b" + re.escape(normalize(word)) + r"(?:s|es)?\b", haystack) is not None


# --- Variantes -------------------------------------------------------------
# Solo se compara un anuncio con otros de la misma variante: un iPhone de 64 GB con
# otros de 64 GB, una consola sola con otras solas (no con packs con juegos), algo
# usado con otros usados (no con precintados).
VARIANT_WORDS = {"pro", "max", "mini", "plus", "lite", "oled", "slim", "digital", "xl",
                 "new", "ultra", "air", "cellular", "lte", "5g", "combo"}
BUNDLE_WORDS = {"juego", "juegos", "pack", "lote", "mandos", "bundle", "accesorios", "kit",
                "fly", "extras"}
NEW_WORDS = {"precintado", "precintada", "nuevo", "nueva", "sellado", "sellada", "estrenar",
             "abrir"}
USED_PHRASES = ("como nuevo", "como nueva", "casi nuevo", "casi nueva", "practicamente nuevo",
                "practicamente nueva", "seminuevo", "seminueva")
STORAGE_RE = re.compile(r"\b(\d{2,4})\s?(gb|tb|g)\b")


def product_base_words(product: dict) -> frozenset:
    """Palabras que todos los anuncios del producto llevan: no sirven para distinguir variantes."""
    text = normalize(" ".join(product.get("must_include", []) + [product.get("query", "")]))
    return frozenset(re.findall(r"[a-z0-9]+", text.replace("|", " ")))


@lru_cache(maxsize=50000)
def signature(title: str, base: frozenset = frozenset()) -> frozenset:
    t = normalize(title)
    for phrase in USED_PHRASES:
        t = t.replace(phrase, " ")
    toks = {m.group(1) + ("tb" if m.group(2) == "tb" else "gb") for m in STORAGE_RE.finditer(t)}
    words = set(re.findall(r"[a-z0-9]+", t))
    toks |= (words & VARIANT_WORDS) - base
    if words & BUNDLE_WORDS or "+" in t:
        toks.add("pack")
    if words & NEW_WORDS:
        toks.add("nuevo")
    return frozenset(toks)


def matches_product(listing: Listing, product: dict) -> bool:
    """True si el anuncio corresponde al producto buscado (y no a un accesorio, roto, etc.)."""
    title = normalize(listing.title)
    full = title + " " + normalize(listing.description)
    if listing.reserved:
        return False
    for word in product.get("must_include", []):
        # Admite alternativas: "oled|o.l.e.d"
        if not any(_contains(title, alt) for alt in word.split("|")):
            return False
    for word in TITLE_EXCLUDE + product.get("exclude", []):
        if _contains(title, word):
            return False
    for word in TEXT_EXCLUDE:
        if _contains(full, word):
            return False
    lo, hi = product.get("min_price", 1), product.get("max_price", 100000)
    return lo <= listing.price <= hi


def trimmed(prices: Iterable[float]) -> list:
    """Quita valores atípicos con el método IQR (anuncios a 1 € o a precio de chiste)."""
    data = sorted(p for p in prices if p > 0)
    if len(data) < 4:
        return data
    q1, _, q3 = statistics.quantiles(data, n=4)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [p for p in data if lo <= p <= hi]


def percentile(data: list, pct: float) -> float:
    data = sorted(data)
    if not data:
        raise ValueError("sin datos")
    k = (len(data) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(data) - 1)
    return data[f] + (data[c] - data[f]) * (k - f)


@dataclass
class Market:
    median: float          # precio medio al que se anuncia
    resale: float          # precio de reventa conservador (percentil bajo)
    low: float
    high: float
    samples: int
    historic_low: float    # percentil muy bajo de todo lo visto: el "precio de chollo histórico"


def market_from_prices(prices: Iterable[float], resale_pct: float = 0.4,
                       historic_pct: float = 0.1) -> Optional[Market]:
    data = trimmed(prices)
    if not data:
        return None
    return Market(
        median=statistics.median(data),
        resale=percentile(data, resale_pct),
        low=data[0],
        high=data[-1],
        samples=len(data),
        historic_low=percentile(data, historic_pct),
    )


@dataclass
class Deal:
    listing: Listing
    product: dict
    market: Market
    buy_cost: float        # precio + envío + comisión de compra
    resale: float          # precio al que lo revendes
    net_sale: float        # lo que cobras al revender, ya sin comisiones ni envío
    profit: float
    profit_pct: float
    discount_pct: float    # cuánto más barato que la mediana
    suspicious: bool
    level: str             # "CHOLLAZO" | "CHOLLO"
    sold_median: Optional[float] = None
    sold_days: Optional[float] = None
    sold_count: int = 0


def sold_summary(rows: list) -> Optional[tuple]:
    """rows = [(precio, días_hasta_venderse)] -> (mediana, días medios, nº ventas)."""
    if not rows:
        return None
    prices = trimmed(r[0] for r in rows)
    if not prices:
        return None
    return statistics.median(prices), statistics.median(r[1] for r in rows), len(rows)


def evaluate(listing: Listing, product: dict, market: Market, cfg: dict,
             sold: Optional[tuple] = None) -> Optional[Deal]:
    fees = cfg["fees"]
    rules = cfg["rules"]
    resale = market.resale
    use_sold = sold and sold[2] >= rules["min_sold_samples"]
    if use_sold:
        # Si sabemos a cuánto se vende de verdad, usamos el valor más prudente de los dos.
        resale = min(resale, sold[0])
        if sold[1] > rules["max_days_to_sell"]:
            return None   # se vende demasiado lento
    plat = fees[listing.platform]
    if listing.shipping:
        buy_cost = listing.price * (1 + plat["buy_fee_pct"]) + plat["buy_fee_fixed"] + plat["shipping"]
    else:
        buy_cost = listing.price   # en mano: sin envío ni comisión
    # Reventa: vendes al precio "resale" y descuentas comisión de venta y envío de venta.
    sell = fees["resale"]
    net_sale = resale * (1 - sell["sell_fee_pct"]) - sell["shipping"]
    profit = net_sale - buy_cost
    profit_pct = profit / buy_cost if buy_cost else 0
    discount_pct = 1 - listing.price / market.median if market.median else 0

    if market.samples < rules["min_samples"]:
        return None
    if buy_cost > cfg["budget"]:
        return None
    if profit < rules["min_profit_eur"] or profit_pct < rules["min_profit_pct"]:
        return None
    if discount_pct < rules.get("min_discount", 0):
        return None

    suspicious = discount_pct >= rules["suspicious_discount"]
    # Chollazo histórico: mucho beneficio Y más barato que el 90 % de todo lo visto en el periodo.
    big = (profit >= rules["chollazo_profit_eur"]
           and profit_pct >= rules["chollazo_profit_pct"]
           and listing.price <= market.historic_low)
    if rules.get("only_chollazos", True) and not big:
        return None
    return Deal(
        listing=listing, product=product, market=market, buy_cost=buy_cost,
        net_sale=net_sale, profit=profit, profit_pct=profit_pct,
        discount_pct=discount_pct, suspicious=suspicious,
        level="CHOLLAZO" if big else "CHOLLO",
        resale=resale,
        sold_median=sold[0] if use_sold else None,
        sold_days=sold[1] if use_sold else None,
        sold_count=sold[2] if use_sold else 0,
    )
