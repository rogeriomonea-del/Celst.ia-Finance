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

## Endpoints da Fase 6 (visão geral, macro, Tesouro Direto)

Consumidos pelas telas `/visao-geral`, `/macro` e `/tesouro` via o mesmo
cliente tipado `lib/api/investment-os.ts` (mesma base
`NEXT_PUBLIC_IIOS_API_URL`; erros `{detail: {code, message}}`).

| Endpoint | Conteúdo |
|---|---|
| `GET /v1/overview` | painel agregado: screener (contagens + aprovadas com P/L e P/VPA), Tesouro (data-base, nº títulos, janelas no radar, referência IPCA+ 2050 vs threshold monitorado), regimes macro com confiança, carteira (snapshots + IPS confirmada, sem valores) e saúde dos dados (mtime de cada gold + auditoria de ingestão). Cada bloco pode vir `{status: "indisponivel", motivo}` — a UI mostra o motivo e o comando que gera o artefato |
| `GET /v1/macro/regimes` | regimes por dimensão (estado, detalhe, confiança `ALTA/MEDIA/BAIXA/INDISPONIVEL`, data-base, fonte, natureza), séries recentes (`selic_meta`, `ipca_mensal`, `ptax_venda`, `ibc_br`, `divida_bruta_pib`, `cdi_anual`, `igp_m`), premissas, fontes e itens fora do escopo; 404 `gold_missing` → rodar `python -m investment_os.cli macro` |
| `GET /v1/macro/series/{serie_id}` | série individual `{serie_id, pontos: [{data, valor}], fonte}`; 404 `serie_not_found` |
| `GET /v1/tesouro/titulos` | painel completo: títulos (taxas/PU compra e venda, `modelado`, duration Macaulay/modificada, DV01, convexidade, `motivo_nao_modelado`, histórico com percentil), curvas nominal prefixada e real IPCA+, radar de janelas por percentil (critério, saída por histerese, invalidação, nota de não recomendação), parâmetros do radar e `historico_oficial_desde` |
| `GET /v1/tesouro/titulos/{tipo}/{vencimento}/cenarios` | cenários MTM de choques ±50/100/150/200 bps (PU novo, variação %, efeito duration/convexidade, resíduo) + risco do título; `tipo` contém espaços → sempre `encodeURIComponent`; 409 `nao_modelado` com motivo; 404 `gold_missing` → rodar `python -m investment_os.cli macro` |

Regras adicionais dessas telas: bloco `indisponivel` nunca vira 0 nem some —
mostra motivo + comando; título não modelado exibe badge com motivo, nunca
número no lugar de duration/DV01; nota "não é recomendação" do radar sempre
visível; rodapé do Tesouro deixa explícito que as taxas são as ofertadas ao
varejo (Tesouro Transparente) e NÃO a curva indicativa ANBIMA; staleness de
gold > 24h destacada na Visão geral.

## Endpoints da Fase 7 (banco de ativos B3, cotação intradiária, chat)

Consumidos pelas telas `/ativos` e `/pergunte` via o mesmo cliente tipado
`lib/api/investment-os.ts` (mesma base `NEXT_PUBLIC_IIOS_API_URL`; erros
`{detail: {code, message}}`).

| Endpoint | Conteúdo |
|---|---|
| `GET /v1/ativos?tipo=&busca=&limite=&pagina=` | universo completo do registro B3 (~2.468 ativos): `total`/`pagina`/`limite`, `data_base`, `fonte`, contagens por tipo (`acao_br`, `fii`, `bdr`, `etf_ou_fundo`, `unit`) e lista com ticker, tipo (classificação HEURÍSTICA com `classificacao_confianca` ALTA/MEDIA/BAIXA), `cnpj_emissor`, `ultimo_pregao`, `ultimo_fechamento` (pode ser `null`; `ajustado_por_proventos: false`) e `especificacao`; 404 `registry_missing`, 422 `tipo_invalido` |
| `GET /v1/ativos/{ticker}` | um ativo do registro + `fonte`; 404 `ativo_not_found` |
| `GET /v1/ativos/{ticker}/intradiario` | cotação intradiária de AGREGADOR (brapi.dev — não oficial, uso indicativo; `usavel_em_calculos: false`, `aviso` e `token_configurado` sempre presentes) + bloco `oficial_d1` (fechamento oficial D-1 com pregão e fonte) + `consultado_em`; 404 `ativo_not_found`; 503 `intradiario_indisponivel` → a UI exibe o fechamento oficial D-1 como fallback SEM esconder o erro |
| `POST /v1/chat` | body `{pergunta (3..4000), historico (máx. 20 mensagens role/content)}` → resposta ESTRUTURADA: `resposta_direta`, `evidencias` (afirmação/valor/fonte/data-base), `fontes`, `data_base`, `premissas`, `confianca` (ALTA/MEDIA/BAIXA), `riscos`, `contra_argumento`, `dados_ausentes`, `gatilhos_revisao` + `ferramentas_chamadas` (trace: ferramenta, argumentos, ok, codigo_erro) + `modelo`; 422 validação; 503 `chat_indisponivel` = backend sem `ANTHROPIC_API_KEY` (estado dedicado na UI; nenhuma outra tela depende dessa chave) |
| `GET /v1/chat/ferramentas` | lista das ferramentas determinísticas expostas ao assistente (`nome`, `descricao`) + `nota` |

Regras de exibição dessas telas: a classificação de tipo dos ativos é
heurística — o selo de confiança acompanha cada linha e a tela explica que não
é dado oficial; fechamentos B3 rotulados "não ajustado por proventos"; a
cotação intradiária carrega selo "AGREGADOR — indicativo" + aviso do backend e
NUNCA alimenta cálculos; o chat mantém aviso permanente ("o assistente não
calcula números; não é recomendação de investimento") e renderiza TODOS os
blocos estruturados, incluindo contra-argumento e dados ausentes — resposta
vazia vira estado explícito, nunca é ocultada. As chaves (brapi/Anthropic)
vivem SOMENTE no backend — nada de secret no bundle.

## Plano de substituição (Fase 3+ do roadmap do backend)

1. `lib/agents/market-data.ts` → `GET /v1/screener` / `GET /v1/assets/{ticker}`.
2. Curva patrimonial sintética (`buildEquityCurve`) → remover até existir
   histórico real de carteira importada (Fase 5).
3. Cotações brapi.dev → preços do backend (B3 COTAHIST) com data do pregão.
4. Nova página "Tesouro" → `GET /v1/tesouro/ipca2050`.
