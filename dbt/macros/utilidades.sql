{#- Idade do SIM (campo IDADE, 3 dígitos: unidade + quantidade) convertida em dias.
    Unidade: 0 = minutos, 1 = horas, 2 = dias, 3 = meses, 4 = anos, 5 = 100 anos ou mais.
    Meses e anos são aproximações (30 e 365 dias) e servem só para classificar faixas.
    CONFERIR com o dicionário do SIM e com o teste assert_proporcao_neonatal. -#}
{% macro sim_idade_dias(col) -%}
case substr({{ col }}, 1, 1)
  when '0' then 0
  when '1' then 0
  when '2' then try_cast(substr({{ col }}, 2, 2) as integer)
  when '3' then try_cast(substr({{ col }}, 2, 2) as integer) * 30
  when '4' then try_cast(substr({{ col }}, 2, 2) as integer) * 365
  when '5' then (100 + try_cast(substr({{ col }}, 2, 2) as integer)) * 365
  else null
end
{%- endmacro %}

{#- Idade do SIM em anos completos (para óbitos maternos). -#}
{% macro sim_idade_anos(col) -%}
case substr({{ col }}, 1, 1)
  when '4' then try_cast(substr({{ col }}, 2, 2) as integer)
  when '5' then 100 + try_cast(substr({{ col }}, 2, 2) as integer)
  else 0
end
{%- endmacro %}

{#- True se existir ao menos um arquivo no caminho (glob). Avaliado em tempo de execução. -#}
{% macro arquivo_existe(caminho) -%}
  {%- if execute -%}
    {%- set r = run_query("select count(*) as n from glob('" ~ caminho ~ "')") -%}
    {{- return(r.columns[0].values()[0] > 0) -}}
  {%- else -%}
    {{- return(false) -}}
  {%- endif -%}
{%- endmacro %}

{#- Nome do arquivo de origem sem caminho e extensão. -#}
{% macro nome_arquivo(col) -%}
regexp_extract({{ col }}, '([^/\\]+)\.parquet$', 1)
{%- endmacro %}

{#- Lista (em maiúsculas) das colunas existentes numa fonte bronze. Consulta só os metadados do Parquet. -#}
{% macro cols_fonte(nome) -%}
  {%- if execute -%}
    {%- set r = run_query("describe select * from " ~ source('bronze', nome)) -%}
    {%- set cols = [] -%}
    {%- for row in r.rows -%}{%- do cols.append((row[0] | string | upper)) -%}{%- endfor -%}
    {{- return(cols) -}}
  {%- else -%}
    {{- return([]) -}}
  {%- endif -%}
{%- endmacro %}

{#- Referencia a coluna se existir na fonte; caso contrário devolve NULL do tipo indicado.
    Evita quebrar quando o layout muda entre anos/UFs (ex.: NUMERODN só existe em bases identificadas). -#}
{% macro col(cols, nome, tipo='varchar') -%}
  {%- if (nome | upper) in cols -%}{{ nome }}{%- else -%}null::{{ tipo }}{%- endif -%}
{%- endmacro %}

{#- Data do SINAN: campo de data (AAAA-MM-DD, já convertido) ou texto DDMMAAAA. -#}
{% macro dt_sinan(col) -%}
coalesce(try_cast({{ col }} as date), try_strptime({{ col }}, '%d%m%Y')::date)
{%- endmacro %}
