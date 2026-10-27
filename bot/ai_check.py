"""Segunda revisión con IA: lee el anuncio entero como lo haría una persona.

Las listas de palabras de pricing.py son el primer filtro. Pero cada vendedor cuenta los
defectos a su manera ("necesita cambiar la pantalla", "no acepto devolución"...), así que
antes de avisar se le pregunta a Claude si el producto está realmente en perfecto estado.
Solo se activa si hay ANTHROPIC_API_KEY en .env.
"""
import json
import logging
import os
from typing import Optional

log = logging.getLogger("bot.ia")

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")

PROMPT = """Eres un experto en compraventa de segunda mano. Revisa este anuncio de Wallapop.
Solo nos interesa comprar productos en PERFECTO estado para revenderlos.

Marca "perfecto": false si hay CUALQUIER señal de que el producto no está perfecto o hay
un problema con la compra, por ejemplo:
- defectos: arañazos, golpes, pantalla o batería cambiada o que haya que cambiar, piezas no
  originales, botones o funciones que fallan, batería por debajo del 88 %, drift, ruidos,
  "necesita reparar", "error", "estado aceptable", "marcas de uso"...
- que no sea el producto completo: solo la caja, solo el mando, piezas, accesorio, juego,
  una funda o una foto del producto.
- señales de problema: bloqueado (iCloud, cuenta, operador), réplica, "leer descripción",
  "sin probar", o "no acepto devoluciones" junto a algún problema.
Si la descripción no menciona ningún problema y el producto parece completo y en buen
estado, marca "perfecto": true. No penalices que la descripción sea corta.

Producto buscado: {product}
Precio: {price} €
Estado indicado por el vendedor: {condition}
Título: {title}
Descripción:
{description}

Responde con "perfecto" y un "motivo" muy corto en español (máximo 12 palabras)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "perfecto": {"type": "boolean"},
        "motivo": {"type": "string"},
    },
    "required": ["perfecto", "motivo"],
    "additionalProperties": False,
}


class AICheck:
    def __init__(self):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(max_retries=3, timeout=120)

    @classmethod
    def from_env(cls) -> Optional["AICheck"]:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        return cls()

    async def review(self, listing, product: dict) -> Optional[tuple]:
        """(perfecto, motivo), o None si no se ha podido revisar."""
        text = PROMPT.format(
            product=product["name"], price=f"{listing.price:.0f}",
            condition=listing.extra.get("condition") or "no indicado",
            title=listing.title, description=(listing.description or "(sin descripción)")[:4000],
        )
        try:
            r = await self.client.beta.messages.create(
                model=MODEL,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": text}],
            )
        except self.anthropic.APIError as e:
            log.warning("no se pudo revisar con IA (%s): %s", type(e).__name__, listing.url)
            return None
        if r.stop_reason in ("refusal", "max_tokens"):
            log.warning("la IA no ha respondido (%s): %s", r.stop_reason, listing.url)
            return None
        try:
            out = json.loads(next(b.text for b in r.content if b.type == "text"))
            return bool(out["perfecto"]), str(out["motivo"]).strip()
        except (StopIteration, ValueError, KeyError):
            log.warning("respuesta de la IA ilegible: %s", listing.url)
            return None
