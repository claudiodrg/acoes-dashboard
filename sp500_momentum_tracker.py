#!/usr/bin/env python3
"""
Rastreador Diário de Momentum e Valor — Ações do S&P 500
=========================================================

Filtra e pontua ações do índice S&P 500 usando dados em tempo real da
biblioteca ``yfinance``, aplicando três critérios de seleção:

    1. Índice P/L (Preço/Lucro) abaixo de 20      -> ações de "valor"
    2. Volume de negociação 2x acima da média de 20 dias -> interesse anômalo
    3. RSI (Índice de Força Relativa) acima de 50 -> momentum positivo

As ações que passam nos três filtros são ordenadas por uma pontuação de
momentum composta e as 25 principais são exportadas para um arquivo CSV
(e, opcionalmente, para uma tabela HTML simples).

Uso:
    python sp500_momentum_tracker.py                 # roda com padrões
    python sp500_momentum_tracker.py --top 25 --html # gera CSV + HTML
    python sp500_momentum_tracker.py --limit 50      # testa com 50 tickers
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# Parâmetros dos filtros (ajustáveis via linha de comando)
# ---------------------------------------------------------------------------
DEFAULT_PE_MAX = 20.0        # P/L máximo
DEFAULT_VOLUME_MULT = 2.0    # volume >= média_20d * este fator
DEFAULT_RSI_MIN = 50.0       # RSI mínimo
DEFAULT_RSI_PERIOD = 14      # período do RSI
DEFAULT_TOP_N = 25           # quantas ações exportar
DEFAULT_CSV = "sp500_momentum.csv"
DEFAULT_HTML = "sp500_momentum.html"


# ---------------------------------------------------------------------------
# Universo de tickers — componentes do S&P 500
# ---------------------------------------------------------------------------
def get_sp500_tickers() -> list[str]:
    """Retorna a lista de tickers do S&P 500.

    Tenta obter a composição atual da Wikipedia; se a rede falhar,
    recorre a uma lista embutida de fallback com os principais nomes.
    """
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )
        tickers = tables[0]["Symbol"].astype(str).tolist()
        # yfinance usa "-" no lugar de "." (ex.: BRK.B -> BRK-B)
        tickers = [t.replace(".", "-").strip() for t in tickers]
        if tickers:
            return tickers
    except Exception as exc:  # noqa: BLE001
        print(f"[aviso] Não foi possível obter a lista da Wikipedia: {exc}",
              file=sys.stderr)

    # Fallback: subconjunto representativo do S&P 500.
    return _FALLBACK_SP500


_FALLBACK_SP500 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO",
    "BRK-B", "JPM", "LLY", "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "COST",
    "ABBV", "MRK", "CVX", "PEP", "KO", "ADBE", "WMT", "CRM", "BAC", "MCD",
    "ACN", "NFLX", "AMD", "LIN", "CSCO", "TMO", "ABT", "INTC", "WFC", "DIS",
    "QCOM", "TXN", "DHR", "VZ", "PM", "INTU", "AMGN", "IBM", "NKE", "CAT",
    "GE", "NOW", "SPGI", "UNP", "HON", "COP", "LOW", "AMAT", "RTX", "BKNG",
    "GS", "ISRG", "PFE", "T", "BLK", "ELV", "SYK", "TJX", "AXP", "MDT",
    "PLD", "C", "VRTX", "SCHW", "CB", "MU", "ADP", "MDLZ", "GILD", "LRCX",
    "REGN", "BSX", "ADI", "MMC", "CI", "SO", "BX", "ETN", "SLB", "DE",
    "KLAC", "ZTS", "CME", "MO", "FI", "DUK", "SHW", "ICE", "BDX", "WM",
]


# ---------------------------------------------------------------------------
# Indicadores técnicos
# ---------------------------------------------------------------------------
def compute_rsi(close: pd.Series, period: int = DEFAULT_RSI_PERIOD) -> Optional[float]:
    """Calcula o RSI (Wilder) e retorna o valor mais recente.

    Retorna ``None`` se não houver dados suficientes.
    """
    if close is None or len(close) < period + 1:
        return None

    delta = close.diff().dropna()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Suavização de Wilder: a primeira média é a SMA dos primeiros
    # `period` valores; as seguintes usam avg = (avg_ant*(p-1) + atual)/p.
    avg_gain = gain.iloc[:period].mean()
    avg_loss = loss.iloc[:period].mean()
    for i in range(period, len(gain)):
        avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period

    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi)


def momentum_pct(close: pd.Series, lookback: int) -> Optional[float]:
    """Retorno percentual dos últimos ``lookback`` pregões."""
    if close is None or len(close) <= lookback:
        return None
    past = close.iloc[-lookback - 1]
    now = close.iloc[-1]
    if pd.isna(past) or past == 0:
        return None
    return float((now / past - 1.0) * 100.0)


# ---------------------------------------------------------------------------
# Estrutura do resultado
# ---------------------------------------------------------------------------
@dataclass
class StockRow:
    ticker: str
    price: float
    pe_ratio: float
    rsi: float
    volume: float
    avg_volume_20d: float
    volume_ratio: float
    ret_1m: float           # retorno ~1 mês (21 pregões)
    ret_3m: float           # retorno ~3 meses (63 pregões)
    momentum_score: float


# ---------------------------------------------------------------------------
# Avaliação de um único ticker
# ---------------------------------------------------------------------------
def evaluate_ticker(
    ticker: str,
    pe_max: float,
    volume_mult: float,
    rsi_min: float,
    rsi_period: int,
) -> Optional[StockRow]:
    """Baixa dados de um ticker e retorna ``StockRow`` se passar nos filtros."""
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="6mo", interval="1d", auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[skip] {ticker}: erro ao baixar histórico ({exc})",
              file=sys.stderr)
        return None

    if hist is None or hist.empty or "Close" not in hist:
        return None

    close = hist["Close"].dropna()
    volume = hist["Volume"].dropna()
    if len(close) < rsi_period + 1 or len(volume) < 20:
        return None

    price = float(close.iloc[-1])
    last_volume = float(volume.iloc[-1])
    avg_vol_20 = float(volume.iloc[-20:].mean())
    if avg_vol_20 <= 0:
        return None
    vol_ratio = last_volume / avg_vol_20

    rsi = compute_rsi(close, rsi_period)
    if rsi is None:
        return None

    # P/L via metadados (pode faltar para alguns ativos)
    pe_ratio = _safe_pe(tk)
    if pe_ratio is None:
        return None

    # ---- Aplicação dos três filtros ----
    if not (0 < pe_ratio < pe_max):
        return None
    if vol_ratio < volume_mult:
        return None
    if rsi <= rsi_min:
        return None

    ret_1m = momentum_pct(close, 21) or 0.0
    ret_3m = momentum_pct(close, 63) or 0.0

    score = _momentum_score(rsi, vol_ratio, ret_1m, ret_3m, pe_ratio, pe_max)

    return StockRow(
        ticker=ticker,
        price=round(price, 2),
        pe_ratio=round(pe_ratio, 2),
        rsi=round(rsi, 1),
        volume=round(last_volume, 0),
        avg_volume_20d=round(avg_vol_20, 0),
        volume_ratio=round(vol_ratio, 2),
        ret_1m=round(ret_1m, 2),
        ret_3m=round(ret_3m, 2),
        momentum_score=round(score, 2),
    )


def _safe_pe(tk: "yf.Ticker") -> Optional[float]:
    """Extrai o P/L (trailing) dos metadados do ticker, tolerando falhas."""
    try:
        info = tk.info or {}
    except Exception:  # noqa: BLE001
        return None
    pe = info.get("trailingPE")
    if pe is None:
        return None
    try:
        pe = float(pe)
    except (TypeError, ValueError):
        return None
    return pe if pe > 0 else None


def _momentum_score(
    rsi: float,
    vol_ratio: float,
    ret_1m: float,
    ret_3m: float,
    pe_ratio: float,
    pe_max: float,
) -> float:
    """Pontuação composta de momentum (0-100 aprox.).

    Combina força técnica (RSI), interesse de mercado (volume),
    tendência de preço (retornos) e um bônus de valor (P/L baixo).
    """
    # RSI acima de 50 -> 0..50 pontos de intensidade
    rsi_component = (rsi - 50.0) * 1.0          # peso 1.0
    # Volume acima do gatilho -> pontos por múltiplo excedente
    vol_component = (vol_ratio - 1.0) * 8.0      # peso 8.0
    # Tendência de preço
    trend_component = ret_1m * 0.8 + ret_3m * 0.4
    # Bônus de valor: quanto mais barato (P/L baixo), maior o bônus
    value_component = (pe_max - pe_ratio) / pe_max * 10.0

    return rsi_component + vol_component + trend_component + value_component


# ---------------------------------------------------------------------------
# Execução principal / varredura
# ---------------------------------------------------------------------------
def scan(
    tickers: list[str],
    pe_max: float,
    volume_mult: float,
    rsi_min: float,
    rsi_period: int,
    top_n: int,
) -> pd.DataFrame:
    """Varre todos os tickers e retorna um DataFrame ordenado pelo score."""
    rows: list[StockRow] = []
    total = len(tickers)
    for i, ticker in enumerate(tickers, start=1):
        print(f"  [{i:>3}/{total}] {ticker:<8}", end="\r", file=sys.stderr)
        row = evaluate_ticker(ticker, pe_max, volume_mult, rsi_min, rsi_period)
        if row is not None:
            rows.append(row)
            print(f"  [{i:>3}/{total}] {ticker:<8} ✓ score={row.momentum_score}",
                  file=sys.stderr)
    print(file=sys.stderr)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([asdict(r) for r in rows])
    df = df.sort_values("momentum_score", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    return df.head(top_n)


# ---------------------------------------------------------------------------
# Saídas
# ---------------------------------------------------------------------------
def export_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index=False)
    print(f"[ok] CSV exportado: {path} ({len(df)} ações)")


def export_html(df: pd.DataFrame, path: str) -> None:
    """Gera uma tabela HTML simples, no mesmo estilo do dashboard do repo."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body_rows = []
    for _, r in df.iterrows():
        rsi_cls = "green" if r.rsi >= 60 else "yellow"
        body_rows.append(
            f"<tr>"
            f"<td class='rank'>#{int(r['rank'])}</td>"
            f"<td><b>{r.ticker}</b></td>"
            f"<td>${r.price:,.2f}</td>"
            f"<td>{r.pe_ratio:.1f}</td>"
            f"<td class='{rsi_cls}'>{r.rsi:.1f}</td>"
            f"<td>{r.volume_ratio:.2f}x</td>"
            f"<td class='{'green' if r.ret_1m >= 0 else 'red'}'>{r.ret_1m:+.1f}%</td>"
            f"<td class='{'green' if r.ret_3m >= 0 else 'red'}'>{r.ret_3m:+.1f}%</td>"
            f"<td class='green'><b>{r.momentum_score:.1f}</b></td>"
            f"</tr>"
        )

    html = _HTML_TEMPLATE.format(
        stamp=stamp,
        count=len(df),
        rows="\n".join(body_rows),
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[ok] HTML exportado: {path}")


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Momentum & Valor — S&P 500</title>
<style>
:root{{--bg:#0a0c10;--surface:#111318;--border:#1e2330;--accent:#00e5ff;
--green:#00c853;--red:#ff1744;--yellow:#ffd600;--text:#c8d0e0;--text-dim:#5a6478;}}
body{{background:var(--bg);color:var(--text);font-family:Arial,sans-serif;padding:40px;}}
h1{{color:var(--accent);margin-bottom:6px;}}
.sub{{color:var(--text-dim);font-size:13px;margin-bottom:20px;}}
table{{width:100%;border-collapse:collapse;font-size:14px;}}
th,td{{border:1px solid var(--border);padding:10px;text-align:center;}}
th{{background:var(--surface);color:var(--accent);}}
.green{{color:var(--green);font-weight:bold;}}
.red{{color:var(--red);font-weight:bold;}}
.yellow{{color:var(--yellow);font-weight:bold;}}
.rank{{color:var(--text-dim);}}
.legend{{margin-top:20px;font-size:13px;color:var(--text-dim);}}
</style>
</head>
<body>
<h1>📈 Momentum &amp; Valor — S&amp;P 500</h1>
<div class="sub">Top {count} ações • Atualizado em {stamp}<br>
Filtros: P/L &lt; 20 · Volume ≥ 2x média 20d · RSI &gt; 50</div>
<table>
<thead>
<tr>
<th>Rank</th><th>Ação</th><th>Preço</th><th>P/L</th><th>RSI</th>
<th>Vol/Média</th><th>Ret 1M</th><th>Ret 3M</th><th>Score</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
<div class="legend">
Score = intensidade do RSI + excesso de volume + tendência de preço + bônus de valor (P/L baixo).
</div>
</body>
</html>
"""


def print_table(df: pd.DataFrame) -> None:
    """Imprime a tabela no terminal."""
    if df.empty:
        print("\nNenhuma ação passou nos três filtros hoje.")
        return
    cols = ["rank", "ticker", "price", "pe_ratio", "rsi",
            "volume_ratio", "ret_1m", "ret_3m", "momentum_score"]
    print("\n" + df[cols].to_string(index=False))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rastreador diário de momentum e valor — S&P 500 (yfinance)."
    )
    p.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                   help=f"Número de ações a exportar (padrão: {DEFAULT_TOP_N}).")
    p.add_argument("--pe-max", type=float, default=DEFAULT_PE_MAX,
                   help=f"P/L máximo (padrão: {DEFAULT_PE_MAX}).")
    p.add_argument("--volume-mult", type=float, default=DEFAULT_VOLUME_MULT,
                   help=f"Múltiplo de volume vs média 20d (padrão: {DEFAULT_VOLUME_MULT}).")
    p.add_argument("--rsi-min", type=float, default=DEFAULT_RSI_MIN,
                   help=f"RSI mínimo (padrão: {DEFAULT_RSI_MIN}).")
    p.add_argument("--rsi-period", type=int, default=DEFAULT_RSI_PERIOD,
                   help=f"Período do RSI (padrão: {DEFAULT_RSI_PERIOD}).")
    p.add_argument("--limit", type=int, default=None,
                   help="Limita a varredura aos N primeiros tickers (para testes).")
    p.add_argument("--csv", type=str, default=DEFAULT_CSV,
                   help=f"Caminho do CSV de saída (padrão: {DEFAULT_CSV}).")
    p.add_argument("--html", action="store_true",
                   help="Também gera uma tabela HTML.")
    p.add_argument("--html-path", type=str, default=DEFAULT_HTML,
                   help=f"Caminho do HTML de saída (padrão: {DEFAULT_HTML}).")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    print("Rastreador de Momentum & Valor — S&P 500")
    print(f"Filtros: P/L < {args.pe_max} | Volume >= {args.volume_mult}x média 20d "
          f"| RSI > {args.rsi_min}\n")

    tickers = get_sp500_tickers()
    if args.limit:
        tickers = tickers[:args.limit]
    print(f"Universo: {len(tickers)} tickers\n")

    df = scan(
        tickers=tickers,
        pe_max=args.pe_max,
        volume_mult=args.volume_mult,
        rsi_min=args.rsi_min,
        rsi_period=args.rsi_period,
        top_n=args.top,
    )

    print_table(df)

    if not df.empty:
        export_csv(df, args.csv)
        if args.html:
            export_html(df, args.html_path)
    else:
        print("\n[info] Nenhum resultado para exportar. "
              "Tente afrouxar os filtros ou rodar em pregão ativo.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
