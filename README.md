# 📈 Rastreador de Momentum & Valor — S&P 500

Ferramentas para acompanhar diariamente ações do índice **S&P 500**,
cruzando critérios de **valor** (P/L baixo) e **momentum** (RSI e volume),
usando dados em tempo real da biblioteca [`yfinance`](https://pypi.org/project/yfinance/).

## Conteúdo

| Arquivo | Descrição |
|---------|-----------|
| `sp500_momentum_tracker.py` | Script principal: baixa dados via `yfinance`, aplica os filtros, pontua e exporta o resultado. |
| `index.html` | Dashboard estático de exemplo (visual do repositório). |
| `sp500_momentum.html` | Tabela HTML gerada pelo script (quando usado `--html`). |

## Critérios de seleção

Uma ação entra na lista somente se passar **nos três filtros**:

1. **P/L (Preço/Lucro) < 20** — foco em ações de valor.
2. **Volume ≥ 2× a média de 20 dias** — interesse de mercado acima do normal.
3. **RSI > 50** — força relativa indicando momentum positivo.

As aprovadas são ordenadas por uma **pontuação de momentum** composta:

```
score = intensidade do RSI (acima de 50)
      + excesso de volume (acima do gatilho)
      + tendência de preço (retornos de 1M e 3M)
      + bônus de valor (quanto menor o P/L, maior o bônus)
```

As **25 melhores** são exportadas.

## Instalação

```bash
pip install yfinance pandas
```

## Uso

```bash
# Padrão: varre o S&P 500 e exporta as 25 melhores para sp500_momentum.csv
python sp500_momentum_tracker.py

# Também gera uma tabela HTML no estilo do dashboard
python sp500_momentum_tracker.py --html

# Testar rápido com os 50 primeiros tickers
python sp500_momentum_tracker.py --limit 50

# Ajustar filtros
python sp500_momentum_tracker.py --pe-max 25 --volume-mult 1.5 --rsi-min 55 --top 30
```

### Opções

| Opção | Padrão | Descrição |
|-------|--------|-----------|
| `--top` | 25 | Número de ações a exportar. |
| `--pe-max` | 20 | P/L máximo. |
| `--volume-mult` | 2.0 | Múltiplo de volume vs. média de 20 dias. |
| `--rsi-min` | 50 | RSI mínimo. |
| `--rsi-period` | 14 | Período do RSI. |
| `--limit` | (todos) | Limita a varredura aos N primeiros tickers (testes). |
| `--csv` | `sp500_momentum.csv` | Caminho do CSV de saída. |
| `--html` | — | Também gera uma tabela HTML. |
| `--html-path` | `sp500_momentum.html` | Caminho do HTML de saída. |

## Saída

O CSV contém, por ação: `rank, ticker, price, pe_ratio, rsi, volume,
avg_volume_20d, volume_ratio, ret_1m, ret_3m, momentum_score`.

## Observações

- O universo de tickers é obtido da Wikipedia; se a rede bloquear o acesso,
  o script usa uma **lista de fallback** com os principais componentes.
- Dados de mercado dependem do Yahoo Finance. Em ambientes com rede restrita
  (ou fora do pregão), a lista pode vir vazia — rode em um ambiente com acesso
  ao Yahoo Finance e durante/logo após o pregão para melhores resultados.
- Esta ferramenta é apenas para fins **educacionais / informativos** e **não
  constitui recomendação de investimento**.
