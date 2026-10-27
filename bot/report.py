"""Informe diario de mercado y tendencias."""
import html

from .notifier import eur
from .pricing import market_from_prices, product_base_words, signature, sold_summary


def main_variant_prices(store, product, days):
    """Precios de la variante más anunciada (p. ej. la consola sola, usada)."""
    base = product_base_words(product)
    groups = {}
    for _, title, price in store.comparables(product["name"], days):
        groups.setdefault(signature(title, base), []).append(price)
    return max(groups.values(), key=len) if groups else []


def build_report(store, cfg) -> str:
    rows = []
    for p in cfg["products"]:
        name = p["name"]
        m = market_from_prices(main_variant_prices(store, p, cfg["market_window_days"]),
                               cfg["resale_percentile"], cfg["historic_percentile"])
        if not m:
            continue
        sold = sold_summary(store.sold_stats(name, days=7))
        week_ago = store.snapshot_ago(name, 7)
        trend = (m.median / week_ago - 1) if week_ago else None
        rows.append({
            "name": name, "m": m, "sold": sold, "trend": trend,
            "new": store.count_new(name, 24),
            # margen típico si compras en el 10 % más barato y vendes al precio de reventa
            "spread": m.resale - m.historic_low,
        })
    if not rows:
        return "📊 <b>Informe de mercado</b>\nAún no hay datos suficientes. Mañana tendrás el primero completo."

    def line(r):
        s = f"• <b>{html.escape(r['name'])}</b>: {eur(r['m'].median)}"
        if r["trend"] is not None:
            arrow = "📈" if r["trend"] > 0.02 else "📉" if r["trend"] < -0.02 else "➖"
            s += f" {arrow} {r['trend']:+.0%}"
        if r["sold"]:
            s += f" · {r['sold'][2]} vendidos en {r['sold'][1]:.1f} d"
        return s

    out = ["📊 <b>Informe diario de mercado</b>", ""]

    fast = sorted((r for r in rows if r["sold"]), key=lambda r: (-r["sold"][2], r["sold"][1]))[:5]
    if fast:
        out += ["⚡ <b>Lo que más rápido se vende (7 días)</b>"] + [line(r) for r in fast] + [""]

    trending = [r for r in rows if r["trend"] is not None]
    up = sorted(trending, key=lambda r: -r["trend"])[:3]
    down = sorted(trending, key=lambda r: r["trend"])[:3]
    if up:
        out += ["📈 <b>Subiendo de precio</b>"] + [line(r) for r in up if r["trend"] > 0] + [""]
    if down:
        out += ["📉 <b>Bajando de precio</b> (buen momento para comprar barato)"] \
            + [line(r) for r in down if r["trend"] < 0] + [""]

    best = sorted(rows, key=lambda r: -r["spread"])[:5]
    out += ["💎 <b>Más margen posible</b> (comprando en el 10 % más barato)"]
    out += [f"• <b>{html.escape(r['name'])}</b>: compra ≤ {eur(r['m'].historic_low)}, "
            f"vende ~{eur(r['m'].resale)} → +{eur(r['spread'])}" for r in best]

    hot = sorted(rows, key=lambda r: -r["new"])[:3]
    out += ["", "🆕 <b>Más anuncios nuevos hoy</b>"]
    out += [f"• {html.escape(r['name'])}: {r['new']}" for r in hot]
    return "\n".join(out)
