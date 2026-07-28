# Contrato de Integração — Frontend ↔ Backend

Backend: `Celest.ia-v2-Alpha` (`investment_os/`), API FastAPI somente leitura
sobre artefatos gold. Toda resposta carrega fonte e data-base; o frontend NUNCA
recalcula indicadores (o motor determinístico vive no backend).

## Endpoints (v0.1)

| Endpoint | Conteúdo |
|---|---|
| `GET /health` | status + nome do sistema (configurável) |
| `GET /v1/screener` | resultado completo do preset (params, pendências, empresas com critérios PASS/FAIL/NOT_EVALUATED) |
| `GET /v1/assets` | lista resumida (ticker, empresa, cd_cvm, setor, status, datas) |
| `GET /v1/assets/{ticker}` | métricas do ativo com statuses (`indisponivel`, `prejuizo`, etc.) |
| `GET /v1/tesouro/ipca2050` | taxa atual, janela histórica vs referência, duration/DV01/convexidade, cenários MTM |

## Regras de exibição obrigatórias (docs do backend: METRIC_REGISTRY)

1. Toda tela financeira mostra: data-base, fonte, confiança/status e metodologia.
2. Status ≠ número: `indisponivel`/`prejuizo`/`dado_insuficiente` NUNCA viram 0
   nem célula vazia sem explicação.
3. Preços B3 não são ajustados por proventos — proibido rotular como retorno total.
4. Dados simulados do protótipo devem ter selo visível "SIMULADO" até serem
   substituídos por estes endpoints.

## Plano de substituição (Fase 3+ do roadmap do backend)

1. `lib/agents/market-data.ts` → `GET /v1/screener` / `GET /v1/assets/{ticker}`.
2. Curva patrimonial sintética (`buildEquityCurve`) → remover até existir
   histórico real de carteira importada (Fase 5).
3. Cotações brapi.dev → preços do backend (B3 COTAHIST) com data do pregão.
4. Nova página "Tesouro" → `GET /v1/tesouro/ipca2050`.
