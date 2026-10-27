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


def test_small_discount_ignored():
    # Mucho "beneficio" sobre el papel pero apenas más barato que el mercado: no es un chollazo.
    cfg = dict(CFG, rules=dict(CFG["rules"], min_discount=0.35))
    m = market()
    assert evaluate(L("Nintendo Switch OLED", m.median * 0.7), SWITCH, m, cfg) is None


def test_plural_exclude():
    # "juego" en exclude también descarta "juegos" (packs que inflan el precio)
    assert not matches_product(L("Nintendo Switch OLED + 3 juegos", 280), SWITCH)


def test_variants_not_mixed():
    from bot.pricing import product_base_words, signature
    base = product_base_words(SWITCH)
    plain = signature("Nintendo Switch OLED blanca", base)
    assert signature("Switch OLED como nueva", base) == plain
    assert signature("Switch OLED precintada", base) != plain
    assert signature("Switch OLED + mandos", base) != plain
    assert signature("iPhone 13 256GB") != signature("iPhone 13 128 GB")


def test_config_numbers_as_text():
    from bot import config
    cfg = config.load("config.yaml")
    for p in cfg["products"]:
        assert all(isinstance(w, str) for w in p["must_include"] + p["exclude"])


def _product(name):
    from bot import config
    return next(p for p in config.load("config.yaml")["products"] if p["name"] == name)


def test_accessories_and_games_rejected():
    # Anuncios reales que se colaron en la primera prueba
    ps5, xsx = _product("PS5"), _product("Xbox Series X")
    assert not matches_product(L("PlayStation VR2 (PS5)", 270), ps5)
    assert not matches_product(L("Volante Gaming HORI RWA para PS5, PS4 y PC", 180), ps5)
    assert not matches_product(L("Mouse P.I. for Hire Mouseburg Xbox Series X", 200), xsx)
    assert not matches_product(
        L("Tarjeta de expansión de almacenamiento Seagate 1TB para Xbox Series X|S", 175), xsx)
    # y los de verdad siguen entrando
    assert matches_product(L("Vendo consola PS5 con lector", 380), ps5)
    assert matches_product(L("Consola Xbox Series X 1TB", 330), xsx)
    assert matches_product(L("Sony PlayStation 5 edición estándar", 390), ps5)


def test_storage_in_title():
    ip = _product("iPhone 13 128GB")
    assert matches_product(L("iPhone 13 128GB azul", 300), ip)
    assert matches_product(L("Apple iPhone 13 128 GB", 300), ip)
    sd = _product("Steam Deck LCD 512GB")
    assert matches_product(L("Steam Deck 512GB con funda original", 300, ""), sd) is False  # funda en título
    assert matches_product(L("Steam Deck 512GB", 300), sd)
    from bot.pricing import product_base_words, signature
    base = product_base_words(sd)
    assert signature("Steam Deck 512GB", base) == signature("Steam Deck 512", base)


def test_real_titles_still_match():
    cases = {
        "Game Boy Advance SP": ["Nintendo Game Boy Advance SP AGS-101", "Consola GBA SP plata"],
        "Nintendo Switch OLED": ["Vendo Nintendo Switch OLED blanca"],
        "DJI Mini 3": ["Dron DJI Mini 3 con mando"],
        "Meta Quest 3": ["Gafas VR Meta Quest 3 128GB"],
        "GoPro Hero 11": ["Cámara deportiva GoPro Hero 11 Black"],
    }
    for name, titles in cases.items():
        p = _product(name)
        for t in titles:
            price = (p["min_price"] + p["max_price"]) / 2
            assert matches_product(L(t, price), p), t


def test_disc_drive_accessory():
    ps5 = _product("PS5 Slim")
    assert not matches_product(L("Lector de discos PS5 Slim", 190), ps5)
    assert matches_product(L("PS5 Slim con lector y 2 mandos", 420), ps5)  # pack: otra variante
    assert matches_product(L("PS5 Slim con lector", 420), ps5)


def test_controller_alone_rejected_by_price():
    assert not matches_product(L("Mando PS5 DualSense blanco", 45), _product("PS5"))


def test_revalidate_drops_old_junk(tmp_path):
    from bot.storage import Store
    st = Store(str(tmp_path / "t.db"))
    ps5 = _product("PS5")
    st.upsert("PS5", Listing("wallapop", "a", "Volante HORI para PS5", 180, "u"), True)
    st.upsert("PS5", Listing("wallapop", "b", "Consola PS5", 380, "u"), True)
    assert st.revalidate("PS5", lambda t, p: matches_product(L(t, p), ps5)) == 1
    assert [r[1] for r in st.comparables("PS5", 30)] == ["Consola PS5"]
