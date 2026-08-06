# Da planilha ao Python — como o `.xlsm` virou o motor `engine/`

Este documento descreve a conversão da pasta de trabalho **"Carteira B3 —
Posição Real"** (17 abas, 28.430 fórmulas, 3 macros VBA e 9 gráficos) no motor
Python que roda por trás da aba **"Importe suas Planilhas"** (`/importar`).

O objetivo não foi "ler o Excel": foi **reescrever a lógica**. O motor abre o
arquivo apenas para colher os *valores* das células — nenhuma fórmula do arquivo
é reavaliada. Tudo que morava em fórmula foi reimplementado em Python puro, com
testes que comparam o resultado contra os números que o Excel havia calculado.

---

## 1. Mapa das 17 abas → módulos Python

O arquivo original tem exatamente estas abas (na ordem em que aparecem no
Excel). A coluna "linhas" é o tamanho real da aba no arquivo de referência.

| # | Aba do `.xlsm` | Linhas | Vira | O que o módulo produz |
|---|---|---|---|---|
| 1 | `Dashboard` | 48 | `engine/calc/charts.py` + `pipeline.py` (KPIs) | A aba não é *lida*: ela é **reconstruída**. Os cartões de topo viram `Analysis.kpis` e os 4 gráficos viram `ChartSeries`. |
| 2 | `Config` | 62 | `engine/extract.py` → `Assumptions` | Todos os parâmetros (capital inicial, aporte, IPCA, Selic, DY alvo, duration, datas, tolerância de ordem, renda mensal alvo). |
| 3 | `Cenarios` | 36 | `engine/calc/cenarios.py` | Retorno por cenário, retorno esperado ponderado pelas probabilidades, IPCA/Selic médios e métricas ponderadas (DY, P/L, P/VP). |
| 4 | `Posicao Consolidada` | 40 | `engine/calc/consolidado.py` | Patrimônio total, quebra por categoria e por liquidez, câmbio USD/BRL, reserva de oportunidade. |
| 5 | `Dolarizado` | 29 | `engine/calc/consolidado.py` | Posições no exterior em US$ → R$, resultado por posição e subtotais por corretora (C6 Global, Nomad, Binance). |
| 6 | `Posicao_B3` | 97 | `engine/calc/posicao.py` | Valor de mercado, peso por posição e por classe, variação 12m ponderada, proventos casados por ticker. |
| 7 | `Proventos` | 63 | `engine/calc/proventos.py` | Total do período, agregação por ticker/tipo/mês, top 8 e *yield* sobre a carteira. |
| 8 | `Rebalanceamento` | 28 | `engine/calc/rebalance.py` | Alvo e banda por classe (±20% relativos), ação `COMPRAR`/`VENDER`/`OK`, desvio em R$ e plano de aporte. |
| 9 | `Simulador` | 70 | `engine/calc/simulador.py` | Juros compostos: taxa mensal efetiva, valor final pela fórmula fechada e a série mês a mês. |
| 10 | `Projecao_2030` | 63 | `engine/calc/projecao.py` | Os dois regimes (Renda × Valorização), série mensal de patrimônio e de renda corrigida pelo IPCA. |
| 11 | `Saldo_Invest_Diario` | 2.194 | `engine/calc/fluxo.py` | Recursão diária do saldo investido (taxa diária equivalente, aportes e retiradas). |
| 12 | `Fluxo de Caixa` | 2.199 | `engine/extract.py` → `CashflowEntry` | 2.191 lançamentos diários: entradas, gastos, fatura de cartão, aporte em investimentos e em reserva. |
| 13 | `FluxoCaixa_Mensal` | 75 | `engine/calc/fluxo.py` | Consolidação mensal (o que os `SUMIFS` faziam), saldo de caixa acumulado, patrimônio e taxa de poupança. |
| 14 | `Cartoes_Parcelas` | 18 | `engine/calc/fluxo.py` (via `card_bill`) | A fatura já entra no fluxo diário como despesa; o motor não precisa da aba auxiliar. |
| 15 | `Cartoes_Projecao` | 25 (×168 colunas) | `engine/calc/fluxo.py` | A grade de 168 colunas de projeção de gasto some: a projeção é uma passada linear sobre o fluxo. |
| 16 | `VBA_Atualizar` | 111 | `engine/quotes.py` | Aba que guardava o **código-fonte VBA** e o passo a passo de instalação no macOS. Deixa de existir. |
| 17 | `Claude Log` | 18 | — | Histórico de edições da planilha; sem equivalente no motor (o histórico agora é o git). |

Fora do mapa por aba, três arquivos fazem a costura:

- `engine/workbook.py` — abre `.xlsm`/`.xlsx`/`.csv` e materializa as células
  (`openpyxl` em modo `data_only=True, read_only=True`). É o **único** arquivo
  que importa openpyxl.
- `engine/extract.py` — traduz as abas para os dataclasses de
  `engine/models.py`, guiando-se pelos **cabeçalhos** e não por endereços fixos:
  se o usuário inserir uma coluna, o motor continua achando "Ticker" e "Qtde".
- `engine/pipeline.py` — orquestra os módulos de cálculo e devolve um único
  `Analysis` serializável em JSON.

Os módulos de `engine/calc/` são **puros**: dataclass entra, dataclass sai. Sem
I/O, sem rede, sem openpyxl. Isso é o que os torna testáveis contra os valores
congelados do Excel.

---

## 2. O que cada macro e cada família de fórmula virou

### 2.1 O macro `AtualizarCotacoes` — de AppleScript a uma chamada em lote

Era o ponto mais frágil da planilha. O botão azul da aba `Posicao_B3` chamava um
`Sub` VBA que, no macOS, **não conseguia fazer HTTP sozinho**: o Excel para Mac
roda em sandbox e não tem `MSXML2.XMLHTTP`. A saída era delegar a rede ao
sistema operacional via `AppleScriptTask`, e para isso o usuário precisava, uma
única vez, colar no Terminal um comando que compilava um `.scpt` dentro de
`~/Library/Application Scripts/com.microsoft.Excel/` — o script apenas executava
`do shell script "curl -s ..."`. Se o passo falhasse (ou o usuário trocasse de
máquina, ou reinstalasse o Office), o botão simplesmente não atualizava nada.

Três problemas somados:

1. **Dependência de plataforma.** Um ramo `#If Mac Then AppleScriptTask ... #Else
   MSXML2.XMLHTTP` — duas implementações, e a do Mac exigia instalação manual
   fora do Excel.
2. **Uma requisição por ticker.** O laço percorria as linhas da carteira e
   chamava a brapi uma vez para cada ativo: **78 requisições** para atualizar a
   carteira inteira, cada uma com `--max-time 60`. Rede lenta = minutos de
   planilha travada, e nenhum limite de retentativa.
3. **JSON "parseado" com `InStr`/`Mid`.** A função auxiliar procurava a
   substring `"regularMarketPrice":` e lia caractere a caractere até achar algo
   que não fosse dígito. Qualquer mudança de ordem ou de formato dos campos da
   API devolvia o número errado — silenciosamente, direto na coluna de preço.

No motor isso virou `engine/quotes.py`, com `urllib` da stdlib:

```python
def fetch_quotes(tickers: Sequence[str], token: Optional[str] = None,
                 timeout: float = 8.0) -> Dict[str, Quote]: ...
def apply_quotes(positions: List[Position],
                 quotes: Dict[str, Quote]) -> tuple[List[Position], List[str]]: ...
```

- **Uma requisição por lote de 20 tickers** para
  `https://brapi.dev/api/quote/TICKER1,TICKER2,...` — a carteira de 78 ativos
  cabe em 4 chamadas em vez de 78.
- **`json.loads` de verdade**, não busca de substring. Item sem
  `regularMarketPrice` válido não entra no resultado: preço da planilha é
  preferível a preço inventado.
- **Nunca levanta exceção.** Rede fora, DNS, TLS, timeout, HTTP 401/404/429/5xx
  ou JSON inválido devolvem `{}` (ou o resultado parcial dos lotes que deram
  certo). Um lote que falha não derruba os outros.
- **Freio de retentativa.** O plano gratuito da brapi recusa lotes com
  `401 MISSING_TOKEN` mas atende um ticker por vez; então um lote vazio é
  retentado individualmente — e o resgate desliga sozinho após 3 falhas
  seguidas, para não repetir a enxurrada de requisições do VBA quando o problema
  é a rede.
- **Preços travados são preservados.** `Position.price_locked` protege Renda
  Fixa privada, Tesouro Direto e qualquer linha marcada "não atualizar": esses
  papéis mantêm o preço da planilha. Cada ticker sem cotação vira um aviso em
  pt-BR, não um erro.
- **Token.** Vem por parâmetro ou da variável de ambiente `BRAPI_TOKEN` — não
  mais de uma célula da aba `VBA_Atualizar` que "não podia ser apagada".

Os outros dois macros tiveram destino parecido:

| Macro VBA | Fazia | Virou |
|---|---|---|
| `AtualizarCotacoes` | 78 GETs à brapi via AppleScript; parse por substring | `engine/quotes.py` — lotes de 20, `json.loads`, sem exceção |
| `AtualizarInternacional` | Câmbio (AwesomeAPI), ações EUA (Stooq CSV) e criptos (Binance), linha a linha por endereço fixo (`ws.Cells(8, "F")` … `ws.Cells(26, "F")`) | `engine/calc/consolidado.py` recalcula US$ → R$ a partir do câmbio extraído; as posições vêm por cabeçalho, não por número de linha |
| `AjustarGraficoSimulador` | Reapontava à mão as 3 séries do gráfico para `B22:B(22+meses)` a cada mudança de horizonte | `engine/calc/charts.py` — a série já sai do tamanho certo, porque é gerada a partir de `simulation.series` |

### 2.2 As famílias de fórmula

As 28.430 fórmulas se concentram em poucos padrões repetidos milhares de vezes.
A contagem por função no arquivo de referência:

| Padrão no Excel | Ocorrências | Virou |
|---|---|---|
| `IFERROR(...)` | 4.526 | Nada — em Python, campo ausente é `None` e denominador zero devolve `0.0` por contrato, sem envelope de erro |
| `INDEX(...)` | 2.335 | Indexação direta de listas/dicionários |
| `SUMIFS(...)` | 216 | Uma passada agregando em `dict` (`engine/calc/fluxo.py`, `proventos.py`) |
| `SUMIF(...)` | 91 | Idem — proventos casados por ticker em `engine/calc/posicao.py` |
| `EOMONTH(...)` | 360 | Aritmética de datas com `datetime.date` |
| `SUMPRODUCT(...)` | 5 | Um `sum(peso * retorno ...)` em `engine/calc/cenarios.py` |
| Nomes definidos com `OFFSET` | 3 (`Sim_Saldo`, `Sim_Investido`, `Sim_Juros`) | Eliminados — a lista Python já tem o tamanho do horizonte |

Distribuição por aba: `Fluxo de Caixa` 11.158 · `Saldo_Invest_Diario` 10.955 ·
`Simulador` 4.811 · `FluxoCaixa_Mensal` 504 · `Projecao_2030` 466 ·
`Posicao_B3` 237 · `Dolarizado` 108 · `Posicao Consolidada` 84 · demais < 40.

---

## 3. Por que ficou mais rápido

Não é "Python é mais rápido que Excel" — é que o **modelo de execução mudou**.

**Uma passada O(n), não um grafo de dependências.** O Excel não tem noção de
"rodar o cálculo uma vez": ele monta um grafo com as 28.430 fórmulas e o
reavalia sempre que algo muda. O motor lê os dados uma vez, percorre as 78
posições, os 56 proventos e as 2.191 linhas diárias em **uma passada linear** e
devolve o resultado. As recursões (saldo diário, série do simulador, projeção
mensal) são laços de acumulador: cada elemento é calculado exatamente uma vez.

**Sem recálculo em cascata.** Na planilha, mudar a taxa do simulador (`B12`)
invalidava as 4.811 fórmulas da aba, que puxavam a `Cenarios`, que puxava a
`Posicao_B3`, que puxava a `Proventos`. Uma edição = milhares de células
recalculadas. No motor, mudar um parâmetro é passar outro `float` para
`compute_simulation` — o custo é proporcional ao número de meses, e só.

**Sem funções voláteis.** Os três nomes definidos usavam
`OFFSET(Simulador!$B$22;0;0;MAX(1;Simulador!$B$12+1);1)` para dimensionar as
séries dos gráficos ao horizonte escolhido. `OFFSET` é **volátil**: recalcula a
cada digitação em qualquer célula da pasta, arrastando junto as séries dos
gráficos. Em Python a série é uma `list[SimulationPoint]` que já nasce com
`meses + 1` elementos — o conceito de "intervalo dinâmico" desaparece.

**Sem os `IFERROR` de defesa.** Quase 4.500 fórmulas eram envelopes contra
`#DIV/0!` e `#N/A` de `PROCV`/`MATCH`. Cada envelope é uma avaliação extra da
expressão interna. No motor, a regra é do contrato: divisão por zero, lista
vazia e campo `None` devolvem `0.0` ou `[]` — nunca exceção, nunca envelope.

**Sem abrir o Excel.** A planilha só recalculava com o Office aberto, em uma
máquina, por uma pessoa. O motor roda em uma função serverless de 1 GB com
`maxDuration` de 30 s (`vercel.json`) e devolve um JSON que o front-end desenha
em 9 gráficos.

**Sem passo manual.** O antigo ciclo era: abrir o arquivo → habilitar macros →
clicar em ATUALIZAR COTAÇÕES → esperar 78 requisições → clicar em ATUALIZAR
INTERNACIONAL → clicar em AJUSTAR GRÁFICO → salvar. Agora: arrastar o arquivo
para `/importar`.

---

## 4. Como rodar local

Requisitos: **Python 3.11** e uma única dependência.

```bash
pip install -r requirements.txt     # openpyxl==3.1.5
```

O motor não usa pandas nem numpy — é stdlib + openpyxl, de propósito (bundle e
*cold start* na Vercel).

### CLI — analisar um arquivo no terminal

```bash
cd /caminho/para/celestia-financeiro
python3 -m engine.cli arquivo.xlsm
```

Imprime a análise completa em JSON (o mesmo payload que a API devolve). Útil
para conferir números, versionar um *snapshot* de saída ou alimentar outro
script. Aceita `.xlsm`, `.xlsx` e `.csv`.

### Servidor — desenvolvimento com o front-end

```bash
python3 -m engine.server            # sobe em http://127.0.0.1:8000
```

Em outro terminal, aponte o Next.js para ele e suba o site:

```bash
NEXT_PUBLIC_ENGINE_URL=http://127.0.0.1:8000 npm run dev
```

O `next.config.mjs` reescreve `/api/py/*` para esse endereço em
desenvolvimento; em produção a variável fica vazia e as chamadas caem na função
serverless `api/planilha.py`.

### O endpoint

`POST /api/planilha` aceita dois formatos de corpo:

- `multipart/form-data` com o campo `file`;
- `application/json` com `{"file_base64": "...", "file_name": "carteira.xlsm"}`.

Limite de **6 MB**; extensões aceitas: `.xlsx`, `.xlsm`, `.xltx`, `.xltm`,
`.csv`, `.tsv`, `.txt`. Erros voltam como `{"error": "mensagem em pt-BR"}` com
status 400 (arquivo inválido ou ausente), 413 (grande demais) ou 500.

### Testes

```bash
python3 -m pytest tests/ -q
```

As fixtures são **sintéticas** — os dataclasses são construídos direto no teste.
Nenhuma planilha real entra no repositório.

---

## 5. Privacidade

A planilha de origem contém patrimônio, saldos bancários e posições reais. O
motor foi construído em torno disso:

- **Processamento em memória.** O arquivo enviado vai do corpo da requisição
  para `bytes`, de `bytes` para `io.BytesIO` e daí para o openpyxl. O parse do
  `multipart` é feito à mão justamente para evitar `cgi.FieldStorage`, que
  gravava uploads grandes em disco. **Nada é escrito em arquivo temporário.**
- **Nada é persistido.** Não há banco de dados, não há bucket, não há log do
  conteúdo. Terminada a requisição, o JSON da análise vai para o navegador e os
  bytes da planilha são descartados junto com o processo.
- **Nada é versionado.** O `.gitignore` bloqueia `*.xlsm`, `*.xlsx` e
  `analise*.json`. O arquivo do usuário nunca foi — e não deve ser — copiado
  para dentro do repositório, nem mesmo como fixture.
- **A única saída de rede é a cotação.** `engine/quotes.py` é o único módulo que
  fala com a internet, e só envia **tickers** para a brapi.dev. Quantidades,
  valores, saldos e nomes de instituição jamais saem da máquina. Todo
  `engine/calc/` é offline por construção.
- **Token fora do arquivo.** O `BRAPI_TOKEN` mora em variável de ambiente, não
  em uma célula da planilha como no tempo do VBA.

## 6. Três defeitos da planilha que a conversão revelou

Portar fórmula por fórmula obriga a explicar cada número — e três deles não se
explicavam. Nos três casos o motor faz o certo; ficam registrados aqui porque a
planilha original continua com eles.

### 6.1 O KPI "Var. 12M ponderada" está congelado

`Dashboard!N6` parece um indicador, mas é um **valor digitado**: 0,0336. Não há
fórmula por trás. Ele foi calculado uma vez e nunca mais acompanhou os preços.
Recalculando com os dados atuais — média ponderada pelo valor de mercado das
classes listadas, entre os 64 ativos que têm o dado — o número é **0,03595**.

O motor recalcula a cada análise, e o KPI diz de quantos ativos ele saiu.

### 6.2 A banda de ETFs no rebalanceamento sempre manda vender

A regra da aba é `alvo × 0,8` a `alvo × 1,2`. Em todas as classes, a coluna
"Banda máx." tem `=E×1,2` — **menos na linha de ETFs**, onde `Rebalanceamento!G21`
está vazia. A fórmula da ação continua lá:

```excel
=IF(D21<F21,"COMPRAR",IF(D21>G21,"VENDER","OK"))
```

Com `G21` vazia, o Excel a lê como zero, o teste vira `0,0325 > 0` e a resposta é
**sempre VENDER** — qualquer que seja a alocação. Com a banda correta
(2,4% a 3,6%) e o peso atual de 3,25%, a resposta certa é **OK**.

O motor calcula a banda a partir do alvo, sem depender de célula preenchida.

### 6.3 A aba Simulador ficou para trás do Config

`Simulador!B4` (valor inicial) é digitado e não referencia `Config!B4`. Os dois
divergiram: **R$ 2.400.000 na aba contra R$ 2.440.000 no Config** — R$ 61 mil de
diferença no patrimônio projetado em 4 anos.

Aqui não dá para adivinhar qual é o certo, então o motor **reproduz a aba** (o
mesmo número que o Excel mostra) e emite um aviso visível na tela dizendo que os
dois valores divergem. A escolha fica com quem conhece o dado.

> Uso educacional. Não constitui recomendação de investimento nos termos da
> Resolução CVM 20/2021.
