# Bot de chollos Wallapop + Vinted

Vigila Wallapop y Vinted las 24 horas y te avisa por Telegram solo cuando aparece un
**chollazo histórico**: un producto que se vende rápido y que puedes revender con mucho
beneficio limpio.

## Qué hace

- **Busca sin parar** los productos de `config.yaml` (consolas, Apple, electrónica,
  Lego, Pokémon, zapatillas...) en las dos plataformas, en toda España con envío y en
  mano cerca de La Pobla de Vallbona.
- **Estudia el mercado**: guarda cada anuncio que ve, calcula el precio real de cada
  producto (quitando anuncios basura) y detecta cuáles desaparecen, es decir, cuáles se
  venden, a qué precio y en cuántos días.
- **Filtra la basura**: descarta accesorios, fundas, "busco", "cambio", "roto",
  "para piezas", iCloud bloqueado, réplicas, etc.
- **Solo avisa de chollazos** (por defecto):
  - beneficio limpio de al menos **70 €** y **+45 %**, ya restando envío y comisiones,
  - precio dentro del **10 % más barato** de todo lo visto en 30 días,
  - al menos 20 anuncios comparables, para que el precio de mercado sea fiable,
  - si ya hay ventas detectadas: que se venda en **10 días o menos**,
  - que el coste quepa en el presupuesto (400 €).
- **Avisa de posibles estafas** cuando el precio es demasiado bajo.
- **Informe diario a las 21:00** con tendencias: qué se vende más rápido, qué sube o baja
  de precio, dónde hay más margen y qué tiene más anuncios nuevos.

### Ejemplo de aviso

```
🔥🔥 CHOLLAZO 🔥🔥
Nintendo Switch OLED con caja
Wallapop · Valencia · con envío

🏷 Precio: 119 €  (te cuesta 131 € con envío y comisiones)
📊 Se está vendiendo a: 244 €  (rango 220 €–264 €, 80 anuncios)
✅ Vendidos de verdad: 238 € de media, en 3.2 días (12 ventas)
💸 Revender a: 238 €
🤑 Le sacas: +107 €  (+82%) limpio
📉 51% más barato que el mercado

👉 VER ANUNCIO
```

## Puesta en marcha en Contabo

Puede convivir con tu otro bot sin problema: va en su propio contenedor y usa unos 100 MB de RAM.

### 1. Crear el bot de Telegram

1. En Telegram abre **@BotFather**, escribe `/newbot` y sigue los pasos. Te da un **token**.
2. Abre tu bot nuevo y escríbele `hola`.

### 2. Instalar en el servidor

Conéctate por SSH a tu Contabo y ejecuta:

```bash
# Docker (sáltalo si ya lo tienes)
curl -fsSL https://get.docker.com | sh

git clone https://github.com/Crackpico890/bot-comopraventa.git
cd bot-comopraventa
cp .env.example .env
nano .env        # pega tu TELEGRAM_TOKEN y guarda (Ctrl+O, Enter, Ctrl+X)
```

Saca tu `TELEGRAM_CHAT_ID`:

```bash
docker run --rm --env-file .env -v "$PWD":/app -w /app python:3.12-slim \
  sh -c "pip -q install httpx && python -m bot.chat_id"
```

Copia el número que sale en `.env` como `TELEGRAM_CHAT_ID`.

### 3. Arrancar

```bash
docker compose up -d --build
docker compose logs -f      # ver qué está haciendo (Ctrl+C para salir)
```

Te llegará un mensaje "🤖 Bot de chollos arrancado". Se reinicia solo si se cae o si
reinicias el servidor.

**Ojo:** los primeros días el bot está aprendiendo precios y ventas, así que es normal que
no avise de nada. Cuanto más tiempo lleva encendido, mejores son sus cálculos.

### Comandos útiles

```bash
docker compose restart          # después de cambiar config.yaml
git pull && docker compose up -d --build   # actualizar a la última versión
docker compose down             # pararlo
```

## Ajustes (`config.yaml`)

- `budget`: dinero máximo por compra. Súbelo cuando crezca el bote.
- `rules`: lo estricto que es. Por ejemplo, `chollazo_profit_eur: 100` para que avise menos todavía.
- `rules.only_chollazos: false`: avisa también de chollos normales (`CHOLLO`).
- `products`: añade o quita productos. Copia uno existente y cambia `query`, `must_include` y precios.
- `fees`: comisiones y envíos, por si cambian las tarifas.

## Avisos

- Wallapop y Vinted no ofrecen una API pública: el bot usa la misma que usan sus webs.
  Eso va contra sus condiciones de uso. El bot va despacio y con pausas para no molestar
  ni ser bloqueado, pero si algún día cambian algo puede dejar de funcionar y habrá que
  ajustarlo.
- La detección de "vendido" es aproximada: un anuncio que desaparece puede haberse
  vendido o haberse borrado.
- Revisa siempre el anuncio y al vendedor antes de comprar, y paga solo por la plataforma.

## Desarrollo

```bash
pip install -r requirements-dev.txt
python -m pytest -q
DRY_RUN=1 python -m bot   # ejecuta sin enviar nada a Telegram
```
