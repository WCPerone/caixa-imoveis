"""
History updater
---------------
Reads today's snapshot and updates a long-format history table that
records ONE ROW PER asset per change (price, discount, modalidade, status).

This keeps the DB small: an asset that doesn't change for weeks
contributes a single row, not one row per day.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LATEST = DATA_DIR / "latest.parquet"
DB_PATH = DATA_DIR / "history.sqlite"

TRACKED_FIELDS = ["preco", "valor_avaliacao", "desconto", "modalidade"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    numero_imovel    TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    observed_date    TEXT NOT NULL,
    preco            REAL,
    valor_avaliacao  REAL,
    desconto         REAL,
    modalidade       TEXT,
    PRIMARY KEY (numero_imovel, observed_at)
);
CREATE INDEX IF NOT EXISTS ix_history_imovel ON price_history(numero_imovel);
CREATE INDEX IF NOT EXISTS ix_history_date  ON price_history(observed_date);

CREATE TABLE IF NOT EXISTS asset_meta (
    numero_imovel  TEXT PRIMARY KEY,
    uf             TEXT,
    cidade         TEXT,
    bairro         TEXT,
    endereco       TEXT,
    descricao      TEXT,
    link           TEXT,
    first_seen     TEXT,
    last_seen      TEXT
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def fetch_last_known(conn: sqlite3.Connection) -> pd.DataFrame:
    q = """
    SELECT h.numero_imovel, h.preco, h.valor_avaliacao, h.desconto, h.modalidade
    FROM price_history h
    JOIN (
        SELECT numero_imovel, MAX(observed_at) AS max_at
        FROM price_history
        GROUP BY numero_imovel
    ) m ON h.numero_imovel = m.numero_imovel AND h.observed_at = m.max_at
    """
    return pd.read_sql_query(q, conn)


def main() -> int:
    if not LATEST.exists():
        print(f"ERROR: {LATEST} not found. Run scraper.py first.")
        return 1

    today = pd.read_parquet(LATEST)
    print(f"Today's snapshot: {len(today):,} assets")

    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    last = fetch_last_known(conn)
    if not last.empty:
        merged = today.merge(last, on="numero_imovel", how="left", suffixes=("", "_prev"))
        # A row is "new or changed" if the asset is unseen, or any tracked field differs.
        unchanged_mask = pd.Series(True, index=merged.index)
        for f in TRACKED_FIELDS:
            prev = merged.get(f + "_prev")
            curr = merged.get(f)
            if prev is None or curr is None:
                continue
            same = (prev == curr) | (prev.isna() & curr.isna())
            unchanged_mask &= same
        # Keep rows that are NEW (no prev) or CHANGED
        is_new = merged["preco_prev"].isna() & merged["modalidade_prev"].isna() & merged["desconto_prev"].isna()
        to_insert = merged[~unchanged_mask | is_new]
    else:
        to_insert = today  # first run

    keep_cols = ["numero_imovel", "observed_at", "observed_date",
                 "preco", "valor_avaliacao", "desconto", "modalidade"]
    to_insert = to_insert[keep_cols].copy()

    if to_insert.empty:
        print("No changes since last run.")
    else:
        to_insert.to_sql("price_history", conn, if_exists="append", index=False)
        print(f"Appended {len(to_insert):,} change events to history.")

    # Upsert asset meta
    meta_cols = ["numero_imovel", "uf", "cidade", "bairro", "endereco",
                 "descricao", "link", "observed_date"]
    meta = today[[c for c in meta_cols if c in today.columns]].copy()
    meta = meta.rename(columns={"observed_date": "last_seen"})

    meta.to_sql("_meta_staging", conn, if_exists="replace", index=False)
    conn.executescript("""
        INSERT INTO asset_meta (numero_imovel, uf, cidade, bairro, endereco,
                                descricao, link, first_seen, last_seen)
        SELECT numero_imovel, uf, cidade, bairro, endereco, descricao, link,
               last_seen AS first_seen, last_seen
        FROM _meta_staging
        WHERE true
        ON CONFLICT(numero_imovel) DO UPDATE SET
            uf=excluded.uf,
            cidade=excluded.cidade,
            bairro=excluded.bairro,
            endereco=excluded.endereco,
            descricao=excluded.descricao,
            link=excluded.link,
            last_seen=excluded.last_seen;
        DROP TABLE _meta_staging;
    """)
    conn.commit()
    conn.close()
    print(f"DB updated: {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
