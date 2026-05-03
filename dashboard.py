"""
Caixa Imóveis — Dashboard
-------------------------
Streamlit app to filter the daily list and inspect each asset's
price/status history over time.

Run locally:   streamlit run dashboard.py
Deploy:        push to GitHub, connect at https://share.streamlit.io
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"
LATEST = DATA_DIR / "latest.parquet"
DB_PATH = DATA_DIR / "history.sqlite"

st.set_page_config(
    page_title="Imóveis Caixa — Leilões",
    page_icon="🏠",
    layout="wide",
)

# -------------------- Optional password gate --------------------
def gate() -> None:
    """Simple password protection. Set APP_PASSWORD in Streamlit secrets."""
    pw = st.secrets.get("APP_PASSWORD", None) if hasattr(st, "secrets") else None
    if not pw:
        return  # no password configured -> public
    if st.session_state.get("auth_ok"):
        return
    st.title("🔒 Acesso restrito")
    user_pw = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if user_pw == pw:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()

gate()

# -------------------- Data loading --------------------
@st.cache_data(ttl=60 * 60)
def load_latest() -> pd.DataFrame:
    if not LATEST.exists():
        return pd.DataFrame()
    return pd.read_parquet(LATEST)

@st.cache_data(ttl=60 * 60)
def load_history(numero: str) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """
            SELECT observed_date, preco, valor_avaliacao, desconto, modalidade
            FROM price_history
            WHERE numero_imovel = ?
            ORDER BY observed_at
            """,
            conn,
            params=(numero,),
        )

df = load_latest()

if df.empty:
    st.warning("Sem dados ainda. Execute `python scraper.py` para baixar a primeira lista.")
    st.stop()

# -------------------- Header --------------------
st.title("🏠 Imóveis Caixa — Leilões")
obs_dates = pd.to_datetime(df["observed_at"]).dt.tz_localize(None)
st.caption(
    f"Última atualização: **{obs_dates.max():%d/%m/%Y %H:%M}** · "
    f"{len(df):,} imóveis · {df['uf'].nunique()} UFs"
)

# -------------------- Filters --------------------
with st.sidebar:
    st.header("Filtros")
    ufs = sorted(df["uf"].dropna().unique())
    sel_uf = st.multiselect("UF", ufs, default=[])

    df_uf = df if not sel_uf else df[df["uf"].isin(sel_uf)]

    cidades = sorted(df_uf["cidade"].dropna().unique())
    sel_cidade = st.multiselect("Cidade", cidades, default=[])

    if "modalidade" in df.columns:
        modalidades = sorted(df["modalidade"].dropna().unique())
        sel_mod = st.multiselect("Modalidade de venda", modalidades, default=[])
    else:
        sel_mod = []

    pmax = float(df["preco"].max(skipna=True) or 0)
    if pmax > 0:
        preco_range = st.slider(
            "Faixa de preço (R$)",
            min_value=0.0,
            max_value=float(pmax),
            value=(0.0, float(pmax)),
            step=10000.0,
            format="%.0f",
        )
    else:
        preco_range = (0.0, 0.0)

    desc_min = st.slider("Desconto mínimo (%)", 0, 90, 0, step=5)

    busca = st.text_input("Buscar por bairro ou endereço")

# Apply filters
mask = pd.Series(True, index=df.index)
if sel_uf:
    mask &= df["uf"].isin(sel_uf)
if sel_cidade:
    mask &= df["cidade"].isin(sel_cidade)
if sel_mod:
    mask &= df["modalidade"].isin(sel_mod)
if pmax > 0 and "preco" in df.columns:
    mask &= df["preco"].between(preco_range[0], preco_range[1], inclusive="both") | df["preco"].isna()
if "desconto" in df.columns and desc_min > 0:
    mask &= df["desconto"].fillna(0) >= desc_min
if busca:
    pat = busca.strip().lower()
    text = (df["bairro"].fillna("") + " " + df["endereco"].fillna("")).str.lower()
    mask &= text.str.contains(pat)

filtered = df[mask].copy()

# -------------------- KPIs --------------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Imóveis filtrados", f"{len(filtered):,}")
if "preco" in filtered.columns and not filtered["preco"].dropna().empty:
    k2.metric("Preço médio", f"R$ {filtered['preco'].mean():,.0f}")
    k3.metric("Preço mediano", f"R$ {filtered['preco'].median():,.0f}")
if "desconto" in filtered.columns and not filtered["desconto"].dropna().empty:
    k4.metric("Desconto médio", f"{filtered['desconto'].mean():.0f}%")

st.divider()

# -------------------- Table --------------------
st.subheader("Lista")
display_cols = [c for c in [
    "numero_imovel", "uf", "cidade", "bairro", "endereco",
    "preco", "valor_avaliacao", "desconto", "modalidade", "link"
] if c in filtered.columns]

st.dataframe(
    filtered[display_cols].rename(columns={
        "numero_imovel": "Nº", "uf": "UF", "cidade": "Cidade",
        "bairro": "Bairro", "endereco": "Endereço",
        "preco": "Preço (R$)", "valor_avaliacao": "Avaliação (R$)",
        "desconto": "Desconto (%)", "modalidade": "Modalidade",
        "link": "Link",
    }),
    use_container_width=True,
    height=420,
    column_config={
        "Link": st.column_config.LinkColumn("Link", display_text="abrir"),
        "Preço (R$)": st.column_config.NumberColumn(format="R$ %.0f"),
        "Avaliação (R$)": st.column_config.NumberColumn(format="R$ %.0f"),
        "Desconto (%)": st.column_config.NumberColumn(format="%.0f%%"),
    },
    hide_index=True,
)

# Export filtered slice
csv_bytes = filtered.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
st.download_button("⬇ Baixar CSV filtrado", csv_bytes, file_name="imoveis_filtrados.csv")

st.divider()

# -------------------- Per-asset history --------------------
st.subheader("Histórico de um imóvel")
options = filtered["numero_imovel"].tolist()
if not options:
    st.info("Ajuste os filtros para selecionar um imóvel.")
else:
    sel = st.selectbox("Nº do imóvel", options, format_func=lambda x: f"{x}")
    if sel:
        row = df[df["numero_imovel"] == sel].iloc[0]
        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown(f"**{row.get('cidade', '')} / {row.get('uf', '')}**")
            st.markdown(f"📍 {row.get('endereco', '')} — {row.get('bairro', '')}")
            if row.get("link"):
                st.markdown(f"[Página oficial Caixa]({row['link']})")
            st.markdown(f"**Modalidade atual:** {row.get('modalidade', '—')}")
            st.markdown(f"**Preço atual:** R$ {row.get('preco', 0):,.0f}")
            st.markdown(f"**Avaliação:** R$ {row.get('valor_avaliacao', 0):,.0f}")
            if pd.notna(row.get("desconto")):
                st.markdown(f"**Desconto:** {row['desconto']:.0f}%")
            st.markdown(row.get("descricao", ""))
        with c2:
            hist = load_history(sel)
            if hist.empty:
                st.info("Sem histórico ainda — só aparecerá após mudanças entre snapshots.")
            else:
                hist["observed_date"] = pd.to_datetime(hist["observed_date"])
                st.line_chart(
                    hist.set_index("observed_date")[["preco", "valor_avaliacao"]],
                    use_container_width=True,
                )
                st.dataframe(
                    hist.rename(columns={
                        "observed_date": "Data", "preco": "Preço",
                        "valor_avaliacao": "Avaliação",
                        "desconto": "Desconto", "modalidade": "Modalidade",
                    }),
                    hide_index=True,
                    use_container_width=True,
                )
