# Imóveis Caixa — Pipeline + Dashboard

Sistema completo para baixar diariamente a lista de imóveis de leilão da Caixa,
acompanhar o histórico de preços e status de cada imóvel, e disponibilizar um
dashboard interativo que se atualiza sozinho.

## Como funciona

```
  ┌──────────────────┐     ┌──────────────┐     ┌───────────────┐
  │ GitHub Actions   │────▶│ scraper.py   │────▶│ data/         │
  │ Cron seg-sex 8h  │     │ + history    │     │ latest.parquet│
  └──────────────────┘     └──────────────┘     │ history.sqlite│
                                                 └───────┬───────┘
                                                         │ git push
                                                         ▼
                                                ┌─────────────────┐
                                                │ Streamlit Cloud │
                                                │ (link público)  │
                                                └─────────────────┘
```

A Caixa publica uma lista CSV por estado em
`https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_<UF>.csv`. O scraper
baixa as 27 listas, normaliza os campos (preço, desconto, modalidade), grava um
snapshot diário em Parquet e atualiza um histórico em SQLite com **uma linha por
mudança** — então um imóvel que não muda há semanas ocupa apenas uma linha.

## Estrutura do repositório

```
caixa-imoveis/
├── scraper.py               # baixa e combina as 27 listas
├── update_history.py        # atualiza histórico de mudanças
├── dashboard.py             # app Streamlit
├── requirements.txt
├── .github/workflows/
│   └── daily-scrape.yml     # roda seg-sex às 08:00 BR
└── data/                    # gerado automaticamente
    ├── latest.parquet
    ├── history.sqlite
    └── snapshots/
        └── YYYY-MM-DD.parquet
```

## Setup local (5 minutos)

```bash
git clone <seu-repo>
cd caixa-imoveis
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scraper.py            # baixa dados pela primeira vez
python update_history.py     # cria o histórico
streamlit run dashboard.py   # abre o dashboard local
```

## Deploy no Streamlit Community Cloud (grátis)

1. Suba o projeto para um repositório no GitHub (público ou privado).
2. Acesse <https://share.streamlit.io> e faça login com sua conta GitHub.
3. Clique em **New app**, selecione o repositório, branch `main`,
   arquivo principal `dashboard.py`.
4. (Opcional) Em **Advanced settings → Secrets** adicione:
   ```
   APP_PASSWORD = "sua-senha-aqui"
   ```
   Com isso o app fica protegido por senha e você só compartilha com quem quiser.
5. Clique em **Deploy**. Você recebe um link `https://seu-app.streamlit.app`.

Toda vez que o GitHub Actions fizer commit dos novos dados, o Streamlit Cloud
detecta a mudança no repositório e recarrega o app automaticamente — o
dashboard fica sempre vivo.

## Ativando o agendamento

O workflow `.github/workflows/daily-scrape.yml` já está configurado para rodar
**segunda a sexta às 08:00 horário de Brasília**. Basta:

1. Subir o repositório para o GitHub.
2. Em **Settings → Actions → General**, garantir que workflows estão habilitados.
3. Em **Settings → Actions → General → Workflow permissions**, marcar
   **Read and write permissions** (necessário para o bot fazer commit dos dados).
4. Você pode testar manualmente em **Actions → Daily scrape → Run workflow**.

## Como o histórico funciona

A cada execução, `update_history.py` compara o snapshot de hoje com o último
estado conhecido de cada imóvel. Se preço, desconto, valor de avaliação ou
modalidade mudaram, uma nova linha é gravada em `price_history`. Assim você
consegue ver, no dashboard:

- Quando o imóvel passou do **1º para o 2º leilão**.
- Quando virou **venda direta**.
- Como o **preço caiu** ao longo das etapas.
- Quando o imóvel **saiu da lista** (não aparece mais nos snapshots).

## Personalização

- **Mudar horário do scraping:** edite o `cron` em `daily-scrape.yml`.
- **Adicionar geocoding (mapa):** as listas da Caixa não trazem lat/long. Você
  pode adicionar uma etapa que geocodifica o endereço (ex.: `geopy` com Nominatim)
  e armazena num cache. Cuidado com rate limits.
- **Notificações:** adicione um passo no workflow que dispara um e-mail/Slack
  quando algum imóvel nos seus filtros favoritos baixa de preço.
- **Power BI em vez de Streamlit:** o `latest.parquet` no GitHub é acessível
  via URL raw. Você pode apontar o Power BI Service para essa URL e usar
  refresh agendado, mantendo o pipeline de scraping em GitHub Actions.

## Notas

- Encoding original da Caixa é **Windows-1252**; o scraper trata isso.
- Algumas UFs ocasionalmente retornam HTTP 500 — o scraper tem retry com
  backoff e segue em frente, registrando quais falharam.
- O `download-lista.asp` original aceita escolher uma UF; aqui simplesmente
  iteramos por todas, equivalente a "Estado = Todos".
