"""SQLite: anuncios vistos, historial de precios y alertas enviadas."""
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,
    product TEXT NOT NULL,
    platform TEXT NOT NULL,
    title TEXT,
    price REAL NOT NULL,
    url TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    gone_ts REAL,
    valid INTEGER NOT NULL DEFAULT 1,
    last_check REAL
);
CREATE INDEX IF NOT EXISTS idx_listings_product ON listings(product, last_seen);
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY,
    product TEXT,
    price REAL,
    resale REAL,
    profit REAL,
    level TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    product TEXT NOT NULL,
    ts REAL NOT NULL,
    median REAL,
    resale REAL,
    low REAL,
    samples INTEGER,
    new_24h INTEGER,
    sold_24h INTEGER
);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def upsert(self, product: str, listing, valid: bool) -> bool:
        """Guarda el anuncio. Devuelve True si es la primera vez que se ve."""
        now = time.time()
        cur = self.db.execute("SELECT price FROM listings WHERE key=?", (listing.key,))
        row = cur.fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,NULL,?,NULL)",
                (listing.key, product, listing.platform, listing.title, listing.price,
                 listing.url, now, now, int(valid)),
            )
            self.db.commit()
            return True
        self.db.execute(
            "UPDATE listings SET price=?, last_seen=?, gone_ts=NULL, valid=? WHERE key=?",
            (listing.price, now, int(valid), listing.key),
        )
        self.db.commit()
        return False

    def comparables(self, product: str, days: int) -> list:
        """[(clave, título, precio)] de los anuncios válidos vistos en los últimos días."""
        since = time.time() - days * 86400
        cur = self.db.execute(
            "SELECT key, title, price FROM listings WHERE product=? AND valid=1 AND last_seen>=?",
            (product, since),
        )
        return cur.fetchall()

    def prices(self, product: str, days: int) -> list:
        since = time.time() - days * 86400
        cur = self.db.execute(
            "SELECT price FROM listings WHERE product=? AND valid=1 AND last_seen>=?",
            (product, since),
        )
        return [r[0] for r in cur]

    def to_check(self, platform: str, limit: int, every_s: float = 6 * 3600) -> list:
        """Anuncios válidos aún activos que toca comprobar si se han vendido."""
        now = time.time()
        cur = self.db.execute(
            "SELECT key FROM listings WHERE platform=? AND valid=1 AND gone_ts IS NULL "
            "AND first_seen < ? AND first_seen > ? AND (last_check IS NULL OR last_check < ?) "
            "ORDER BY COALESCE(last_check, 0) LIMIT ?",
            (platform, now - 3600, now - 14 * 86400, now - every_s, limit))
        return [r[0] for r in cur]

    def checked(self, key: str, gone: bool):
        now = time.time()
        if gone:
            self.db.execute("UPDATE listings SET last_check=?, gone_ts=? WHERE key=?", (now, now, key))
        else:
            self.db.execute("UPDATE listings SET last_check=? WHERE key=?", (now, key))
        self.db.commit()

    def sold_stats(self, product: str, days: int = 7):
        """Precio y días que tardaron en desaparecer los anuncios válidos (aprox. ventas)."""
        since = time.time() - days * 86400
        cur = self.db.execute(
            "SELECT price, (gone_ts-first_seen)/86400.0, title FROM listings "
            "WHERE product=? AND valid=1 AND gone_ts IS NOT NULL AND gone_ts>=?",
            (product, since))
        return cur.fetchall()

    def count_new(self, product: str, hours: int = 24) -> int:
        since = time.time() - hours * 3600
        return self.db.execute(
            "SELECT COUNT(*) FROM listings WHERE product=? AND valid=1 AND first_seen>=?",
            (product, since)).fetchone()[0]

    def revalidate(self, product: str, still_valid) -> int:
        """Vuelve a pasar los filtros (solo título y precio) a lo ya guardado. Devuelve cuántos caen."""
        rows = self.db.execute(
            "SELECT key, title, price FROM listings WHERE product=? AND valid=1", (product,)).fetchall()
        bad = [(key,) for key, title, price in rows if not still_valid(title, price)]
        self.db.executemany("UPDATE listings SET valid=0 WHERE key=?", bad)
        self.db.commit()
        return len(bad)

    def already_alerted(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM alerts WHERE key=?", (key,)).fetchone() is not None

    def save_alert(self, deal):
        self.db.execute(
            "INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?)",
            (deal.listing.key, deal.product["name"], deal.listing.price, deal.resale,
             deal.profit, deal.level, time.time()))
        self.db.commit()

    def save_snapshot(self, product, market, new_24h, sold_24h):
        self.db.execute(
            "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?)",
            (product, time.time(), market.median, market.resale, market.low, market.samples,
             new_24h, sold_24h))
        self.db.commit()

    def snapshot_ago(self, product: str, days: float):
        """Mediana registrada hace ~N días, para ver si el precio sube o baja."""
        target = time.time() - days * 86400
        row = self.db.execute(
            "SELECT median FROM market_snapshots WHERE product=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (product, target)).fetchone()
        return row[0] if row else None
