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

## Endpoints da Fase 5 (perfil, IPS, importação, carteira, rebalanceamento)

Consumidos pelas telas `/politica`, `/importacao`, `/carteira` e
`/plano-aportes` via cliente tipado `lib/api/investment-os.ts`
(`NEXT_PUBLIC_IIOS_API_URL`, padrão `http://localhost:8000`). Erros seguem
`{detail: {code, message}}`.

| Endpoint | Conteúdo |
|---|---|
| `POST /v1/profile/questions` | questões PENDENTES do questionário adaptativo, dado o acumulado de respostas |
| `POST /v1/profile/assess` | avaliação: scores por dimensão, conflitos, questões pendentes, confiança (BAIXA/MEDIA/ALTA) |
| `GET /v1/profile/assessment/latest` | última avaliação (404 `no_assessment` se não houver) |
| `POST /v1/policy/draft` | nova versão em rascunho da IPS (sem `content`, deriva da última avaliação; 409 `no_assessment`) |
| `GET /v1/policy/versions` | histórico de versões da IPS (draft/confirmed/superseded, motivo, autor) |
| `POST /v1/policy/{version_id}/confirm` | confirma uma versão da IPS |
| `GET /v1/policy/confirmed` | IPS vigente (404 `no_confirmed_policy`) |
| `POST /v1/portfolio/import` | upload multipart (xlsx/csv, máx. 10 MB) → prévia com contagens, PII removida e linhas com status; 413 `file_too_large`, 422 `import_invalid` |
| `GET /v1/portfolio/import/{id}/preview` | reobtém a prévia da importação |
| `POST /v1/portfolio/import/{id}/rows/{row_id}/correct` | corrige ticker/classe de uma linha ambígua ou desconhecida |
| `POST /v1/portfolio/import/{id}/confirm` | cria snapshot (`accept_partial` obrigatório com linhas problemáticas; 409 `confirm_rejected`) |
| `GET /v1/portfolio/snapshots` | lista de snapshots versionados |
| `GET /v1/portfolio/snapshots/{id}` | snapshot + posições |
| `GET /v1/portfolio/snapshots/{id}/analysis` | análise: patrimônio precificado + nota, pesos, concentração, violações da IPS, qualidade dos dados, confiança, fontes, data-base |
| `POST /v1/portfolio/snapshots/{id}/rebalance` | plano de aportes: próximo aporte, planos 3/6 meses, ações priorizadas, premissas, vendas evitadas; 409 `policy_not_confirmed` |
| `GET /v1/portfolio/issues` | ocorrências registradas pelo backend |

Regras de exibição aplicadas nessas telas: nenhum mock silencioso (API fora do
ar = estado de erro com instrução de subir o backend); `null`/status nunca
viram 0 (`indisponível`/`desconhecido`); preços com data do pregão;
nota de cobertura do patrimônio e premissas do plano sempre visíveis.

## Plano de substituição (Fase 3+ do roadmap do backend)

1. `lib/agents/market-data.ts` → `GET /v1/screener` / `GET /v1/assets/{ticker}`.
2. Curva patrimonial sintética (`buildEquityCurve`) → remover até existir
   histórico real de carteira importada (Fase 5).
3. Cotações brapi.dev → preços do backend (B3 COTAHIST) com data do pregão.
4. Nova página "Tesouro" → `GET /v1/tesouro/ipca2050`.
