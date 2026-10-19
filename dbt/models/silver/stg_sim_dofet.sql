-- Óbitos fetais (DOFET, arquivo nacional) filtrados para a UF do piloto pelo município de residência.
{% if arquivo_existe(var('bronze') ~ '/sim_dofet/*.parquet') %}
{% set c = cols_fonte('sim_dofet') %}
with src as (
    select * from {{ source('bronze', 'sim_dofet') }}
)
select
    {{ nome_arquivo('filename') }}                           as arquivo,
    {{ col(c, 'CONTADOR') }}                                 as contador,
    {{ col(c, 'NUMERODO') }}                                 as numerodo,
    try_strptime(DTOBITO, '%d%m%Y')::date                    as dt_obito,
    extract(year from try_strptime(DTOBITO, '%d%m%Y'))::integer as ano,
    CODMUNRES                                                as codmunres6,
    CODMUNOCOR                                               as codmunocor6,
    CODESTAB                                                 as cnes,
    CAUSABAS                                                 as causabas,
    try_cast(PESO as integer)                                as peso,
    GESTACAO                                                 as gestacao,
    try_cast({{ col(c, 'SEMAGESTAC') }} as integer)          as semagestac,
    GRAVIDEZ                                                 as gravidez,
    PARTO                                                    as parto,
    OBITOPARTO                                               as obitoparto,
    try_cast(IDADEMAE as integer)                            as idademae
from src
where substr(CODMUNRES, 1, 2) = '{{ var("uf_cod") }}'
{% else %}
-- Arquivo DOFET ausente na camada bronze: modelo vazio com o mesmo esquema.
select
    null::varchar as arquivo, null::varchar as contador, null::varchar as numerodo, null::date as dt_obito, null::integer as ano,
    null::varchar as codmunres6, null::varchar as codmunocor6, null::varchar as cnes, null::varchar as causabas,
    null::integer as peso, null::varchar as gestacao, null::integer as semagestac, null::varchar as gravidez,
    null::varchar as parto, null::varchar as obitoparto, null::integer as idademae
where false
{% endif %}
