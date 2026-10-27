"""Envío de alertas e informes por Telegram (API HTTP oficial, sin librerías extra)."""
import html
import logging

import httpx

log = logging.getLogger(__name__)

PLATFORM = {"wallapop": "Wallapop", "vinted": "Vinted"}


def eur(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def format_deal(deal) -> str:
    l, m = deal.listing, deal.market
    head = "🔥🔥 <b>CHOLLAZO</b> 🔥🔥" if deal.level == "CHOLLAZO" else "💰 <b>CHOLLO</b>"
    lines = [
        head,
        f"<b>{html.escape(l.title)}</b>",
        f"{PLATFORM[l.platform]}" + (f" · {html.escape(l.location)}" if l.location else "")
        + (" · con envío" if l.shipping else " · solo en mano"),
        "",
        f"🏷 <b>Precio:</b> {eur(l.price)}  (te cuesta {eur(deal.buy_cost)} con envío y comisiones)",
        f"📊 <b>Se está vendiendo a:</b> {eur(m.median)}  (rango {eur(m.low)}–{eur(m.high)}, {m.samples} anuncios iguales)",
    ]
    if deal.sold_median:
        lines.append(f"✅ <b>Vendidos de verdad:</b> {eur(deal.sold_median)} de media, "
                     f"en {deal.sold_days:.1f} días ({deal.sold_count} ventas)")
    lines += [
        f"💸 <b>Revender a:</b> {eur(deal.resale)}",
        f"🤑 <b>Le sacas:</b> +{eur(deal.profit)}  (+{deal.profit_pct:.0%}) limpio",
        f"📉 {deal.discount_pct:.0%} más barato que el mercado",
    ]
    if l.extra.get("ai_checked"):
        lines.append(f"🤖 <b>Revisado por IA:</b> {html.escape(l.extra['ai'])}")
    elif "ai_checked" in l.extra:
        lines.append("🤖 No se pudo revisar con IA: lee bien la descripción.")
    if deal.suspicious:
        lines += ["", "⚠️ <b>Ojo:</b> precio demasiado bajo, puede ser estafa. "
                      "Paga solo por la plataforma y revisa el perfil del vendedor."]
    lines += ["", f'👉 <a href="{html.escape(l.url)}">VER ANUNCIO</a>']
    return "\n".join(lines)


class Telegram:
    def __init__(self, client: httpx.AsyncClient, token: str, chat_id: str, dry_run: bool = False):
        self.client = client
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.dry_run = dry_run or not token

    async def send(self, text: str, photo: str = None):
        if self.dry_run:
            log.info("[TELEGRAM dry-run]\n%s", text)
            return
        try:
            if photo and len(text) <= 1024:
                r = await self.client.post(self.base + "/sendPhoto", data={
                    "chat_id": self.chat_id, "photo": photo, "caption": text, "parse_mode": "HTML"})
                if r.status_code == 200:
                    return
            r = await self.client.post(self.base + "/sendMessage", data={
                "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": "false"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.error("error enviando a Telegram: %s", e)

    async def send_deal(self, deal):
        await self.send(format_deal(deal), deal.listing.image)
