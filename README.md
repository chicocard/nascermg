# nascer-pipeline — Fase 0

Pipeline de dados do app sobre gestação, parto e nascimento. Baixa SINASC, SIM, SIH-RD e CNES do DATASUS,
converte para Parquet, organiza em camadas (bronze → silver → gold) com dbt + DuckDB e materializa o
plano de análise exportado do painel **Modelos do Nascer**. Tudo roda em containers Docker; nenhum
arquivo original é alterado.

```
nascer-pipeline/
├── docker-compose.yml       serviços: ingest, territorio, malha, dbt, plano, consulta, analise, exportar, app
├── .env.example             UF, anos, competência do CNES (copie para .env)
├── ingest/                  download FTP + conversão DBC → Parquet; territorio.py; malha.py (mapas → GeoJSON)
├── dbt/                     projeto dbt (DuckDB): models/silver, models/gold, models/plano, seeds, tests
├── plano/                   plano_analise.json (exportado do painel), mapa_variaveis.yml, plano2dbt.py
├── analise/                 cluster_municipios.py (tipologia), correlacoes.py (sífilis congênita × taxas) e seus .yml
├── app/                     export_app.py (warehouse → JSON) e public/ (o app: index.html, vendor/, data/)
├── scripts/                 run.ps1 (atalhos Windows) e consulta.py
└── data/                    criado na execução: raw/ bronze/ warehouse/ export/
```

## 1. Pré-requisitos (uma vez)

1. Instale o **Docker Desktop para Windows** (https://www.docker.com/products/docker-desktop/). Na instalação,
   aceite o backend **WSL 2**. Reinicie se pedir. Abra o Docker Desktop e espere o ícone ficar verde ("Engine running").
2. Em **Settings → Resources** (se o Docker Desktop mostrar essa aba), deixe ao menos **8 GB de memória** para os
   containers. Com WSL 2 isso é controlado pelo arquivo `%UserProfile%\.wslconfig`; se não existir, crie com:
   ```
   [wsl2]
   memory=10GB
   processors=6
   ```
   e rode `wsl --shutdown` no PowerShell antes de reabrir o Docker Desktop.
3. Descompacte `nascer-pipeline.zip` em uma pasta sem espaços no nome, por exemplo `C:\dev\nascer-pipeline`.
4. Abra o **PowerShell** nessa pasta (no Explorer: Shift + botão direito → "Abrir janela do PowerShell aqui").
5. Se o PowerShell recusar executar scripts, rode uma vez:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## 2. Configuração

```powershell
Copy-Item .env.example .env
notepad .env
```
Piloto sugerido (já é o padrão): `UF=MG`, `ANOS=2022,2023`, `CNES_MES=12`. Salve e feche.

Se mudar a UF, ajuste também `uf` e `uf_cod` em `dbt/dbt_project.yml` (MG = 31).

## 3. Execução

```powershell
.\scripts\run.ps1 ingest       # 1) baixa e converte. MG, 2 anos: ~40 arquivos, 10-40 min conforme o FTP
.\scripts\run.ps1 territorio   # 2) municípios IBGE + base territorial (regiões, macrorregiões, coordenadas) (1-2 min)
.\scripts\run.ps1 malha        # 3) mapas de polígonos (TabWin .MAP, shapefile ou GeoJSON) → app\public\data (segundos)
.\scripts\run.ps1 build        # 4) dbt: cria o warehouse e roda os testes (2-10 min)
.\scripts\run.ps1 conferir     # 5) totais para comparar com o TabNet (seção 5)
.\scripts\run.ps1 plano        # 6) gera e executa o modelo do plano exportado do painel
.\scripts\run.ps1 analise      # 7) tipologia, sífilis congênita × taxas e áreas quentes (seção 9)
.\scripts\run.ps1 exportar     # 8) warehouse → JSON do app (app\public\data)
.\scripts\run.ps1 app          # 9) sobe o servidor e abre http://localhost:8080 no navegador
```
`.\scripts\run.ps1 tudo` encadeia os nove passos. A primeira execução constrói as imagens Docker (3-6 min).
`.\scripts\run.ps1 parar` desliga o servidor do app (ele volta sozinho quando o Docker Desktop abrir, até você parar).

Se o PowerShell recusar o script ("não está assinado digitalmente"), rode uma vez
`Unblock-File .\scripts\run.ps1` ou chame com `powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 tudo`.

Tudo é **idempotente**: rodar de novo só faz o que falta. Para refazer um arquivo, apague o `.parquet`
correspondente em `data\bronze\...`; o `.dbc` original em `data\raw\` é reaproveitado.

O que cada passo produz:

| Passo | Saída | Conteúdo |
|---|---|---|
| ingest | `data\raw\<BASE>\*.dbc` | cópia fiel do DATASUS (não apague: é a prova de origem) |
| ingest | `data\bronze\<base>\*.parquet` + `.meta.json` | mesma informação, colunas em texto, com md5 e nº de linhas |
| territorio | `data\bronze\territorio\*.parquet`, `_colunas.txt` | municípios IBGE (7 dígitos) e tabelas da base territorial |
| malha | `app\public\data\malha_mg.geojson`, `malha_regsaud_mg.geojson`, `malha_macsaud_mg.geojson` | polígonos de municípios, regiões e macrorregiões |
| build | `data\warehouse\nascer.duckdb` | esquemas `main_silver`, `main_gold`, `main_ref`, `main_plano` |
| plano | `dbt\models\plano\plano_analise.sql` e tabela `main_plano.plano_analise` | variáveis do plano na unidade escolhida |
| analise | esquema `main_analise` no warehouse e `data\export\cluster_*.csv`, `sifc_*.csv` | tipologia dos municípios; sífilis congênita × taxas |
| exportar | `app\public\data\*.json`, `malha_mg.geojson` | indicadores por território e ano, fluxos, hospitais, cenas do modo exposição |
| app | http://localhost:8080 | o app (nginx servindo `app\public`) |

## 4. O que há no warehouse

Silver (uma linha por registro original, tipado): `stg_sinasc`, `stg_sim_do`, `stg_sim_dofet`, `stg_sih_rd`,
`stg_cnes`, `stg_cnes_leitos`.

Gold:

| Tabela | Grão | Para quê |
|---|---|---|
| `dim_municipio` | município | código 7 dígitos, nome, UF, região de saúde |
| `dim_estabelecimento` | CNES × ano | leitos obstétricos, UTIN, UCIN, UTI adulto; flags tem_utin etc. |
| `fato_nascimentos` | nascido vivo | SINASC + município 7 dígitos + estrutura do hospital + derivadas (cesárea, Robson, Kotelchuck, parto fora do município, baixo peso, prematuro, Apgar < 7) |
| `fato_obitos_neonatais` | óbito 0-27 dias | com NUMERODN para vínculo à DN, local, causa, hospital com UTIN |
| `fato_obitos_maternos` | óbito materno declarado | causa agrupada; sem correção por investigação |
| `fato_internacoes_obst` | AIH | partos (SIGTAP), internações obstétricas, recém-nascidos; UTI, transferência, near miss (proxy) |
| `od_partos` | residência → ocorrência × hospital | matriz origem-destino para o mapa de fluxos |
| `ind_territorio` | município e região de saúde × ano | indicadores por residência (taxas por 1.000 NV; `area_pequena` sinaliza NV < 500) |
| `necessidade_leitos` | município e região × ano | leitos necessários (Rede Cegonha e Equações 1 e 3 do Caderno 1) × existentes (CNES), saldo e Fnr |
| `conferencia_tabnet` | ano × item | totais para conferência manual |

Consultas livres:
```powershell
.\scripts\run.ps1 tabelas
.\scripts\run.ps1 sql "select * from main_gold.ind_territorio where nivel = 'regiao_saude' order by ano, cod"
docker compose run --rm consulta "select * from main_gold.necessidade_leitos" --csv necessidade   # grava data\export\necessidade.csv
```
O arquivo `data\warehouse\nascer.duckdb` também abre direto no DBeaver, no DuckDB CLI ou em R (`duckdb::dbConnect`).

## 5. Conferência obrigatória com o TabNet

Antes de qualquer análise, compare `conferencia_tabnet` com o TabNet (tabnet.datasus.gov.br):

| Item | Caminho no TabNet |
|---|---|
| SINASC: nascidos vivos | Estatísticas Vitais → Nascidos vivos → por residência da mãe, UF, ano |
| SINASC: cesáreas | idem, linha "Tipo de parto" |
| SIM: óbitos < 1 ano; 0-6 dias; 7-27 dias | Estatísticas Vitais → Mortalidade → Óbitos infantis → por residência, "Faixa etária detalhada" |
| SIM: óbitos maternos | Mortalidade → Óbitos de mulheres em idade fértil e maternos → "Óbito materno declarado" |
| SIH: AIHs de parto | Assistência à Saúde → Produção hospitalar (SIH/SUS) → por local de residência → procedimentos de parto |
| CNES: leitos | Rede Assistencial → CNES → Leitos de internação → especialidade, competência de dezembro |

Diferenças até ~1% são normais (as bases são atualizadas). Diferença grande em um item indica arquivo faltando
(veja o resumo do `ingest`), ano ainda preliminar ou erro de decodificação (veja o teste `assert_proporcao_neonatal`).

Conferido nos dados reais de MG 2022-2023 (primeira execução, 18/10/2026): nascidos vivos 235.063 e 233.918;
cesáreas 58,4% e 59,1%; óbitos neonatais 1.898 e 1.853 (mortalidade neonatal 8,1 e 7,9 por mil); 72% dos óbitos
infantis são neonatais (decodificação da idade do SIM confirmada); AIHs de parto 157.476 e 148.939; leitos obstétricos
4.205 e 4.229; leitos de UTI neonatal 923 e 941.

Fatos sobre os microdados públicos descobertos nessa execução:

* **Não há NUMERODN nem NUMERODO**: o registro é identificado por `CONTADOR`. O vínculo individual DN ↔ DO
  (óbito do mesmo recém-nascido) não é possível com os arquivos públicos; a mortalidade entra só no nível territorial.
  Com bases identificadas (SES-MG) o vínculo volta a funcionar sem mudar o código, porque as colunas são opcionais.
* **KOTELCHUCK** (conferido contra CONSPRENAT): 1 = não fez pré-natal, 2 = inadequado, 3 = intermediário,
  4 = adequado, 5 = mais que adequado, 9 = não classificado. "Adequado" no pipeline = 4 ou 5.
* **TPROBSON** traz 11 = não classificado (tratado como nulo).
* **COBRANCA** (motivo de saída) tem códigos obstétricos próprios: 61 alta da mãe e do RN, 62 alta da mãe e permanência
  do RN, 63 alta da mãe e óbito do RN, 64 óbito fetal, 65-67 óbito materno. Estão em `seeds/sih_cobranca.csv` e viram
  colunas em `fato_internacoes_obst`.
* **MARCA_UTI**: 74-76 adulto, 77-79 pediátrica, 80-82 neonatal (coluna `uti_tipo`).
* O campo REGSAUDE do CNES não serve para definir região de saúde (preenchimento inconsistente).

Itens a conferir uma vez, porque vieram do meu conhecimento e não de uma tabela oficial baixada pelo pipeline:

* `dbt\seeds\cnes_codleito.csv` — códigos de especialidade de leito (obstetrícia 10 e 43; UTI neonatal 80-82; UCIN 92-93;
  UTI adulto 74-76). Confira no TabNet do CNES (Leitos → "Especialidade do leito" mostra código e nome) e corrija o CSV.
* `dbt\seeds\sigtap_partos.csv` — procedimentos de parto do SIGTAP. Confira em sigtap.datasus.gov.br.
* Decodificação da idade no SIM (`dbt\macros\utilidades.sql`): 0 = minutos, 1 = horas, 2 = dias, 3 = meses, 4 = anos.
  O teste `assert_proporcao_neonatal` falha se a fração neonatal entre óbitos infantis sair de 45-90%.
* Códigos de KOTELCHUCK (3 e 4 = adequado), ESCMAE2010 (0-2 = até fundamental), TPNASCASSI (2 = enfermeira), ESTCIVMAE (1 = solteira).
  Estão marcados com "CONFERIR" nos modelos; confira no dicionário do SINASC (documentação do DATASUS).

## 6. Regiões de saúde

`territorio` procura a base territorial do DATASUS no FTP (caminhos conhecidos e, se falharem, uma varredura de
`/territorio` registrada em `data\bronze\territorio\_ftp_territorio.txt`), carrega a **versão mais recente**
encontrada (registrada em `_versao.txt`; nomes de colunas de versões antigas são normalizados) e lista as tabelas em
`data\bronze\territorio\_colunas.txt`. Da base vêm as regiões de saúde (`rl_municip_regsaud`), as macrorregiões
(`rl_municip_macsaud`, `tb_macsaud`) e as coordenadas das sedes (`tb_municip`), usadas pelo app. Enquanto a relação município → região não existir, tudo funciona no nível
municipal e o gerador do plano avisa e usa município no lugar de região.
O dbt espera a relação município → região em `rl_municip_regsaud` (colunas `co_municip`, `co_regsaud`) e os nomes em
`tb_regsaud` (`co_regsaud`, `ds_nome`). Se os nomes no `_colunas.txt` forem outros, ajuste o bloco `territorio` em
`dbt\dbt_project.yml` e rode `build` de novo. Sem essas tabelas o pipeline funciona só no nível municipal
(a coluna `regiao_saude_cod` fica vazia).

### Mapas de polígonos

`malha` lê a pasta `MALHA_DIR_HOST` do `.env` (padrão `data\malhas`) e gera os GeoJSON que o app desenha e que a
análise espacial usa para a vizinhança. Aceita os mapas do **TabWin/DATASUS** (`mg_municip.MAP`, `mg_regsaud.MAP`,
`mg_macsaud.MAP` — formato binário lido pelo próprio script, sem dependências), um **shapefile** do IBGE
(`.shp` + `.dbf`, campo `CD_MUN`; exige `pyshp`) ou um **GeoJSON** (`codarea`/`CD_MUN`/`cod7`). Os códigos de 6
dígitos do TabWin recebem o 7º dígito pela tabela do IBGE carregada no passo `territorio`. Com os três mapas, o app
desenha regiões de saúde e macrorregiões com seus próprios contornos (e os limites municipais por cima); sem eles,
colore os municípios pelo valor da região. Sem mapa nenhum, o `exportar` tenta baixar a malha municipal do IBGE.

## 7. O plano de análise (painel → pipeline)

1. No painel **Modelos do Nascer**, aba *Plano de análise*, clique em **Copiar JSON**.
2. Cole o conteúdo em `plano\plano_analise.json` (substituindo o atual, que é o seu plano de 18/10/2026).
3. `.\scripts\run.ps1 plano`.

O gerador (`plano\plano2dbt.py`) lê o JSON, traduz cada variável pelo `plano\mapa_variaveis.yml` e escreve
`dbt\models\plano\plano_analise.sql` na unidade escolhida (indivíduo, município, região de saúde, estabelecimento).
Se a verificação metodológica do painel tiver item vermelho ("bad"), o gerador **para** e lista os motivos;
`python plano2dbt.py plano.json --forcar` ignora o bloqueio, registrando o aviso no SQL.
Variáveis não mensuráveis ou ainda não implementadas aparecem como comentário no topo do SQL gerado.

Para acrescentar uma variável: inclua a expressão SQL em `mapa_variaveis.yml` (aliases `f` = fato_nascimentos,
`o` = óbito neonatal vinculado pela DN, `t` = ind_territorio, `nl` = necessidade_leitos).

## 8. Parâmetros de necessidade

Os valores do Caderno 1 (Critérios e Parâmetros Assistenciais SUS 2017, NESCON/UFMG) e do Projeto Parâmetros estão em
`dbt\dbt_project.yml`, bloco `parametros`: fator de sub-registro (1,05), proporções de risco, fórmulas da Rede Cegonha,
faixas de TMP, taxa de internação neonatal, proporção com UTI (P25-P75 das capitais), taxa de ocupação esperada e Fnr.
O modelo `necessidade_leitos` usa os valores **observados no SIH** limitados a essas faixas e registra, lado a lado,
o observado e o usado. Mude os parâmetros ali e rode `build` para recalcular.

## 9. Análises: tipologia dos municípios e sífilis congênita

`analise` roda dois scripts Python (container `analise`, configuração em `analise\*.yml`):

**Tipologia (`cluster_municipios.py`, `cluster_config.yml`).** Agrupa os municípios por k-means sobre variáveis
padronizadas: tamanho (nascidos vivos/ano; população do IBGE se `populacao_ibge.parquet` existir), acesso
(distância geodésica entre sedes até a UTI neonatal, a UTI adulto, o município que realiza cesáreas e o que
realiza partos; distância média percorrida para o parto), fluxos (partos fora do município e da região, cesáreas
recebidas de outros municípios, partos realizados/partos de residentes), oferta (leitos obstétricos por mil NV,
UTIN) e processo (pré-natal adequado, partos SUS, cesárea). Decisões metodológicas, todas ajustáveis no `.yml`:

* `modo: determinantes` — a mortalidade **não entra** no agrupamento; é descrita por tipo (com IC 95%). Assim a
  pergunta "a mortalidade difere entre tipos de município?" continua respondível. `completo` inclui os desfechos.
* `estratificar_por: realiza_partos` — municípios sem serviço de parto local (estrato A) e com serviço (B) são
  agrupados separadamente; sem isso essa diferença estrutural domina tudo. Tipos A1..An ordenados pela distância
  à UTIN, B1..Bn pelo tamanho.
* `equilibrar_blocos: sim` — cada bloco conceitual pesa o mesmo (as cinco distâncias juntas valem o mesmo que
  o tamanho); `k` pela silhueta entre `k_min` e `k_max`, descartando soluções com grupo menor que `tamanho_minimo`.
* Taxas de mortalidade estabilizadas por Bayes empírico (prior = região de saúde, `peso_prior_nv`).

Saída: `main_analise.cluster_municipios` (um registro por município), `cluster_perfil` (médias por tipo, NV,
mortalidade com IC) e `cluster_meta`; CSV em `data\export`. Rótulos como "pequenos, sem serviço de parto, UTIN
a 40-100 km" são gerados por regras simples a partir das médias; os três z-escores mais distintivos de cada tipo
ficam em `distintivas`.

**Sífilis congênita (`correlacoes.py`, `correlacoes_config.yml`).** Requer `SINAN_SIFC` em `BASES` (arquivo
nacional `SIFCBRaa.dbc`; a UF é filtrada pelo município de residência; caso = notificação não descartada, ano =
ano do diagnóstico; a taxa só é calculada nos anos com arquivo carregado). Produz:

* `sifc_correlacoes`: Spearman (ρ, IC 95%, p, n) entre a taxa de sífilis congênita e cada taxa municipal, em
  três níveis — municípios com ≥ `nv_minimo_municipio` NV, regiões de saúde, macrorregiões;
* `sifc_por_tipo`: taxa por tipo de município (tipologia acima), IC 95% e % de municípios sem caso notificado;
* `sifc_regressao`: quase-Poisson (casos, offset ln NV, erros-padrão corrigidos pela sobredispersão) com as
  covariáveis de `regressao_covariaveis`; IRR por 1 desvio-padrão de cada variável, ajustadas entre si;
* `sifc_municipios`: a base analítica.

Leia tudo como análise **ecológica**: a unidade é o município, não a gestante; e a taxa notificada mede também a
capacidade de testar e notificar (um município que não notifica nenhum caso não é, necessariamente, um município
sem sífilis). No app, a variável "Sífilis congênita" abre um gráfico de dispersão município a município contra a
taxa escolhida, com ρ, a tabela de IRR e a taxa por tipo.

**Áreas quentes — análise espacial bayesiana (`espacial.py`, `espacial_config.yml`).** Para sífilis congênita,
mortalidade neonatal, neonatal precoce e materna, por município e por região de saúde:

1. observados O (SIM/SINAN, por residência) e esperados E = NV × taxa estadual (padronização indireta); SMR com IC
   95% (Byar) e Bayes empírico Poisson-gama (Clayton e Kaldor, 1987) como referência sem espaço;
2. modelo **BYM2** (Besag, York e Mollié, 1991, na reparametrização de Riebler et al., 2016): log RR = β0 + σ(√(ρ/s)·φ
   + √(1−ρ)·θ), com φ ~ ICAR (vizinhos) e θ ~ N(0,1); amostragem NUTS com PyMC + nutpie (≈ 10–20 s por desfecho);
   saída: RR suavizado (média e IC 95%) e **P(RR > 1)**, a probabilidade de excesso de risco;
3. classes (Richardson et al., 2004): quente forte (P ≥ 0,95), quente (P ≥ 0,80), fria (P ≤ 0,20), indefinida;
4. Moran global (999 permutações) e LISA (Anselin, 1995) sobre o risco com suavização **não** espacial, para não
   medir a autocorrelação que o próprio modelo cria; aglomerados alto-alto/baixo-baixo.

Vizinhança: contiguidade por polígonos da malha do IBGE (precisa de internet na primeira vez e de `shapely`); sem
malha, triangulação de Delaunay das sedes com poda; regiões vizinhas quando algum par de municípios é vizinho.
Macrorregiões (16) recebem só SMR e probabilidade sem suavização espacial. Se o PyMC não carregar, o script usa o
Bayes empírico e registra isso em `espacial_meta`. Saída: `main_analise.espacial` (uma linha por território ×
desfecho: observados, esperados, SMR, RR, IC, P, classe, LISA) e `espacial_meta` (vizinhança, diagnósticos R̂,
divergências, Moran). No app: variáveis "Áreas quentes", "Risco relativo suavizado" e "P(RR > 1)" para cada desfecho,
em qualquer nível, e duas cenas (sífilis congênita e mortalidade neonatal precoce).

## 10. O app (v0)

`app\public\index.html` é uma página única com deck.gl (biblioteca copiada em `app\public\vendor`, sem CDN: funciona
sem internet). Ela lê os JSON gerados por `exportar`:

| Arquivo | Conteúdo |
|---|---|
| `meta.json` | totais estaduais por ano, lista de variáveis (nome, unidade, paleta), data da exportação |
| `territorios.json` | indicadores por município, região de saúde e macrorregião, por ano, inclusive necessidade de leitos |
| `fluxos.json` | fluxos residência → município do parto com ao menos 10 nascimentos no ano, com coordenadas |
| `hospitais.json` | estabelecimentos com leito obstétrico ou UTI neonatal, leitos por tipo e partos realizados |
| `cenas.json` | roteiro do modo exposição: texto com os números reais, modelo conceitual de referência, fonte |
| `clusters.json`, `sifilis.json` | tipologia e análise de sífilis congênita (seção 9), quando `analise` tiver rodado |
| `malha_mg.geojson` | polígonos dos municípios (API de malhas do IBGE, qualidade mínima). Se o download falhar, o app mostra cada município como um ponto na sede |

Ao abrir, o app entra em **modo exposição**: percorre as cenas sozinho (fluxos de nascimento, cesárea, mortalidade
neonatal precoce, UTI neonatal, pré-natal, tipologia dos municípios, sífilis congênita, financiamento), desenhando as linhas e as barras na frente de quem assiste.
Qualquer clique nos controles passa para o **modo análise** (variável, nível, ano, fluxos, hospitais; setas do teclado
trocam de cena, espaço pausa); "Retomar apresentação" volta ao automático. Passe o mouse sobre um município, uma
linha ou um hospital para ver os números.

As classes do mapa são quintis; quando a distribuição é concentrada (muitos municípios pequenos com taxa zero, ou
com 100% dos partos fora), o app usa quebras naturais de Jenks. Taxas municipais de áreas pequenas oscilam muito:
para comparar, use região de saúde ou macrorregião.

Para mudar o roteiro, edite a lista `cenas` dentro de `main()` em `app\export_app.py` (textos, variável, nível, camadas e duração de
cada cena) e rode `exportar` de novo. O design (cores, fontes, painel) está no `<style>` do `index.html`.

## 11. Publicar o app para outras pessoas

O app é um site estático: `app\public` (página, biblioteca deck.gl e os JSON/GeoJSON agregados, ~8 MB). Não há
servidor de aplicação nem banco: publicar é copiar essa pasta para qualquer hospedagem estática. Os JSON contêm
só agregados por território, fluxos com ≥ 10 nascimentos e leitos por estabelecimento (CNES, público); nunca
microdados, o warehouse ou o `.env`.

1. **Hospedagem estática gratuita** (mais simples). `.\scripts\run.ps1 empacotar` gera `nascer-app.zip`. Em
   https://app.netlify.com/drop arraste a pasta `app\public` (ou o zip descompactado) e receba uma URL `https://...netlify.app`
   em segundos; em Cloudflare Pages ("Upload assets") ou GitHub Pages o resultado é o mesmo. Para atualizar, rode
   `exportar` e arraste de novo. Domínio próprio é opcional nos três.
2. **Sua máquina virtual na nuvem** (Linux com Docker): copie `app\public` e a pasta `deploy` para a VM
   (mesma estrutura: `nascer-pipeline/app/public` e `nascer-pipeline/deploy`), aponte um nome DNS para o IP da VM e rode
   `DOMINIO=nascer.seudominio.org docker compose -f deploy/docker-compose.prod.yml up -d`. O Caddy obtém o certificado
   HTTPS sozinho e serve a pasta; só as portas 80 e 443 ficam abertas (libere apenas essas no firewall da nuvem). Para
   atualizar, copie `app\public` de novo; nada precisa reiniciar.
3. **Demonstração temporária a partir do seu desktop**: com o app no ar (`run.ps1 app`), `cloudflared tunnel --url http://localhost:8080`
   (Cloudflare Tunnel, gratuito, sem abrir portas no roteador) devolve uma URL pública válida enquanto o comando
   estiver rodando. Feche ao terminar: o desktop não deve ficar servindo a internet.

Não publique a pasta `data\` nem o `.env`; o `.gitignore` já os exclui de qualquer repositório.

**Repositório e GitHub Pages.** O projeto vive em https://github.com/chicocard/nascermg. O fluxo de trabalho
`.github\workflows\pages.yml` publica `app\public` em https://chicocard.github.io/nascermg/ a cada `git push` na
branch `main` (uma vez, no GitHub: *Settings → Pages → Build and deployment → Source: GitHub Actions*). Os JSON de
`app\public\data` são versionados de propósito — são os agregados que o site mostra; o que não vai ao repositório
é `data\` (microdados, warehouse) e `.env`. Para atualizar o site: `exportar`, depois
`git add -A; git commit -m "dados atualizados"; git push`.

## 12. Problemas comuns

* **"error during connect: Docker Desktop is not running"** — abra o Docker Desktop e espere ficar verde.
* **FTP lento ou "connection reset"** — o FTP do DATASUS oscila. O `ingest` tenta 3 vezes por arquivo e pode ser
  rodado de novo; nada é baixado duas vezes.
* **"não existe no FTP"** para 2023/2024 — ano ainda não publicado como definitivo; o script também procura nas pastas
  PRELIM. Use anos mais antigos ou aguarde.
* **Falha ao compilar pyreaddbc** na construção da imagem — rode `docker compose build --no-cache ingest`. Se persistir,
  me mande as últimas 30 linhas do erro.
* **"Binder Error: column X not found"** no dbt — uma coluna esperada não existe nos arquivos daquele ano (layouts mudam).
  Me mande o nome; a correção é um `try`/coalesce no modelo silver.
* **Memória** — com 16 GB, mantenha `DUCKDB_MEMORY=8GB` (padrão). Para Brasil inteiro, processe por UF.
* **O app abre em branco ou diz "Não encontrei os dados em data/"** — rode `exportar` antes de `app`. Se a página
  continuar antiga, recarregue com Ctrl+F5 (o navegador guarda os JSON em cache).
* **"port is already allocated"** ao subir o app — outra coisa usa a porta 8080; mude para `"127.0.0.1:8081:80"`
  no `docker-compose.yml` e abra http://localhost:8081.
* **`analise` demora na primeira vez** — a imagem instala PyMC/numba (≈ 1 GB) e o numba compila o modelo na
  primeira execução (1–2 min); as execuções seguintes levam 2–4 min no total.
* **"nenhum ano com SINAN carregado"** no passo `analise` — acrescente `SINAN_SIFC` em `BASES` no `.env`, rode
  `ingest` e `build` de novo. O arquivo é nacional (SIFCBRaa.dbc, ~1 MB) e fica em `data\raw\SINAN_SIFC`.
* **Mapa só com pontos, sem polígonos** — nenhuma malha em `app\public\data`. Rode `malha` com os mapas do TabWin
  em `MALHA_DIR_HOST` (ou `exportar` com internet, que baixa a malha municipal do IBGE).

## 13. Segurança e preservação

* Os microdados públicos do DATASUS são desidentificados; mesmo assim, `data\` fica fora de qualquer repositório
  (`.gitignore`). O único container com porta aberta é o `app`, e só para a própria máquina (`127.0.0.1:8080`);
  os JSON que ele serve são agregados (território, ano, fluxo com ≥ 10 nascimentos), nunca microdados. Para mostrar
  o app a outra máquina da rede local, troque `"127.0.0.1:8080:80"` por `"8080:80"` no `docker-compose.yml`.
* `data\raw\` nunca é modificado pelo pipeline. Faça backup dessa pasta: é o que permite reproduzir tudo.
* Não há senhas nem chaves no projeto. Se algum dia houver (API da ANS, por exemplo), ficam só no `.env`, que também
  está no `.gitignore`.
