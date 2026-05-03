"""
Caixa Imóveis Scraper
---------------------
Downloads the per-state CSV lists from https://venda-imoveis.caixa.gov.br
combines them into a single normalized dataset, and writes a snapshot.

The site exposes one CSV per UF at:
    https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_<UF>.csv

The page download-lista.asp is just a chooser that points to those files.
"""

from __future__ import annotations

import io
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]

BASE_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_{uf}.csv"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/octet-stream,*/*",
}

# Folder layout
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
LATEST_PARQUET = DATA_DIR / "latest.parquet"


def br_now() -> datetime:
    """Brasília time (UTC-3, no DST since 2019)."""
    return datetime.now(timezone.utc) - timedelta(hours=3)


def download_uf(uf: str, retries: int = 3) -> bytes | None:
    url = BASE_URL.format(uf=uf)
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 200 and len(r.content) > 200:
                return r.content
            print(f"  [{uf}] HTTP {r.status_code} (size={len(r.content)}), retry {attempt}")
        except requests.RequestException as e:
            print(f"  [{uf}] {e!r}, retry {attempt}")
        time.sleep(2 * attempt)
    return None


def find_header_row(text: str) -> int:
    """The Caixa CSV starts with a few title lines. Find the actual header row."""
    for i, line in enumerate(text.splitlines()[:15]):
        # Header row contains the column 'N° do imóvel' or 'UF' alongside others
        low = line.lower()
        if ("imóvel" in low or "imovel" in low) and ";" in line and "uf" in low:
            return i
    return 0  # fallback


def parse_money(value) -> float | None:
    if pd.isna(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    # "R$ 123.456,78" -> 123456.78
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_uf_csv(raw: bytes, uf: str) -> pd.DataFrame:
    # The Caixa file is Latin-1 / Windows-1252.
    for enc in ("cp1252", "latin1", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Could not decode {uf} CSV")

    header_row = find_header_row(text)

    df = pd.read_csv(
        io.StringIO(text),
        sep=";",
        skiprows=header_row,
        dtype=str,
        engine="python",
        on_bad_lines="skip",
    )

    # Normalize column names
    df.columns = [c.strip() for c in df.columns]
    rename_map = {}
    for col in df.columns:
        low = col.lower()
        if "n" in low and ("imóvel" in low or "imovel" in low):
            rename_map[col] = "numero_imovel"
        elif low == "uf":
            rename_map[col] = "uf"
        elif "cidade" in low:
            rename_map[col] = "cidade"
        elif "bairro" in low:
            rename_map[col] = "bairro"
        elif "endere" in low:
            rename_map[col] = "endereco"
        elif "preço" in low or "preco" in low:
            rename_map[col] = "preco"
        elif "avalia" in low:
            rename_map[col] = "valor_avaliacao"
        elif "desconto" in low:
            rename_map[col] = "desconto"
        elif "modalidade" in low:
            rename_map[col] = "modalidade"
        elif "link" in low:
            rename_map[col] = "link"
        elif "descri" in low:
            rename_map[col] = "descricao"
    df = df.rename(columns=rename_map)

    # Drop rows without an asset number
    if "numero_imovel" not in df.columns:
        print(f"  [{uf}] WARN: 'numero_imovel' column not found. Columns={list(df.columns)}")
        return pd.DataFrame()

    df = df[df["numero_imovel"].notna() & (df["numero_imovel"].str.strip() != "")]
    df["numero_imovel"] = df["numero_imovel"].str.strip()

    # Coerce types
    for money_col in ("preco", "valor_avaliacao"):
        if money_col in df.columns:
            df[money_col] = df[money_col].map(parse_money)

    if "desconto" in df.columns:
        df["desconto"] = (
            df["desconto"].astype(str).str.replace("%", "", regex=False).map(parse_money)
        )

    # Force UF column: file might be missing it
    if "uf" not in df.columns or df["uf"].isna().all():
        df["uf"] = uf

    # Trim strings
    for c in ("cidade", "bairro", "endereco", "modalidade", "link", "descricao"):
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    return df


def build_snapshot() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    print(f"Downloading {len(UFS)} state lists from Caixa...")
    for uf in UFS:
        raw = download_uf(uf)
        if raw is None:
            print(f"  [{uf}] FAILED")
            continue
        try:
            df = parse_uf_csv(raw, uf)
        except Exception as e:
            print(f"  [{uf}] parse error: {e!r}")
            continue
        print(f"  [{uf}] {len(df):>6} rows")
        frames.append(df)

    if not frames:
        raise RuntimeError("No data downloaded — aborting.")

    combined = pd.concat(frames, ignore_index=True)

    # Add observation timestamp
    now = br_now()
    combined["observed_at"] = now.isoformat(timespec="seconds")
    combined["observed_date"] = now.date().isoformat()

    # De-duplicate by asset number — keep the row with highest discount (most recent stage)
    combined = combined.drop_duplicates(subset=["numero_imovel"], keep="first")

    # Final column order
    cols = [
        "numero_imovel", "uf", "cidade", "bairro", "endereco",
        "preco", "valor_avaliacao", "desconto", "modalidade",
        "descricao", "link", "observed_at", "observed_date",
    ]
    cols = [c for c in cols if c in combined.columns]
    return combined[cols]


def main() -> int:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    df = build_snapshot()

    today = br_now().date().isoformat()
    snap_path = SNAPSHOTS_DIR / f"{today}.parquet"
    df.to_parquet(snap_path, index=False)
    df.to_parquet(LATEST_PARQUET, index=False)

    print(f"\nSaved {len(df):,} unique assets to {snap_path}")
    print(f"Latest snapshot also at {LATEST_PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
