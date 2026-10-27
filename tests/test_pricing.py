import yaml

from bot.models import Listing
from bot.notifier import format_deal
from bot.pricing import evaluate, market_from_prices, matches_product

CFG = yaml.safe_load(open("config.yaml"))
SWITCH = next(p for p in CFG["products"] if p["name"] == "Nintendo Switch OLED")
SWITCH.setdefault("must_include", []); SWITCH.setdefault("exclude", [])


def L(title, price, desc="", platform="wallapop", shipping=True):
    return Listing(platform=platform, item_id="1", title=title, price=price,
                   url="https://es.wallapop.com/item/x-1", description=desc, shipping=shipping)


def market():
    prices = [230, 240, 235, 250, 245, 225, 260, 238, 242, 248, 233, 255, 229, 236,
              244, 251, 239, 247, 232, 241, 5, 900]   # 5 y 900 son basura
    return market_from_prices(prices, 0.4, 0.1)


def test_filters():
    assert matches_product(L("Nintendo Switch OLED blanca", 200), SWITCH)
    assert not matches_product(L("Nintendo Switch Lite", 150), SWITCH)
    assert not matches_product(L("Funda Nintendo Switch OLED", 150), SWITCH)
    assert not matches_product(L("Nintendo Switch OLED", 150, "no enciende, para piezas"), SWITCH)
    assert not matches_product(L("Busco Switch OLED", 150), SWITCH)
    # "incluye cargador" en la descripción no debe descartar
    assert matches_product(L("Switch OLED completa", 200, "incluye cargador y dock"), SWITCH)


def test_market_ignores_outliers():
    m = market()
    assert 235 < m.median < 245
    assert m.low >= 220 and m.high <= 265
    assert m.samples == 20


def test_chollazo_detected():
    deal = evaluate(L("Nintendo Switch OLED", 120), SWITCH, market(), CFG)
    assert deal and deal.level == "CHOLLAZO"
    assert deal.profit > 70
    text = format_deal(deal)
    assert "CHOLLAZO" in text and "Se está vendiendo a" in text and "Le sacas" in text
    assert "https://es.wallapop.com/item/x-1" in text


def test_normal_price_ignored():
    assert evaluate(L("Nintendo Switch OLED", 215), SWITCH, market(), CFG) is None


def test_slow_selling_ignored():
    sold = (240, 25.0, 8)   # se vende pero tarda 25 días
    assert evaluate(L("Nintendo Switch OLED", 120), SWITCH, market(), CFG, sold) is None


def test_over_budget_ignored():
    cfg = dict(CFG, budget=100)
    assert evaluate(L("Nintendo Switch OLED", 120), SWITCH, market(), cfg) is None


def test_scam_warning():
    deal = evaluate(L("Nintendo Switch OLED", 60), SWITCH, market(), CFG)
    assert deal and deal.suspicious
