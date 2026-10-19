-- Nascidos vivos (SINASC/DN). Tipagem mínima; códigos permanecem como texto.
-- Colunas que variam entre anos/UFs passam pela macro col(): se não existirem, viram NULL.
-- Os microdados públicos não trazem NUMERODN (identificador); o registro é identificado por CONTADOR.
{% set c = cols_fonte('sinasc') %}
with src as (
    select * from {{ source('bronze', 'sinasc') }}
),
tipado as (
    select
        {{ nome_arquivo('filename') }}                       as arquivo,
        {{ col(c, 'CONTADOR') }}                             as contador,
        {{ col(c, 'NUMERODN') }}                             as numerodn,
        CODMUNRES                                            as codmunres6,
        CODMUNNASC                                           as codmunnasc6,
        LOCNASC                                              as locnasc,
        CODESTAB                                             as cnes,
        try_strptime(DTNASC, '%d%m%Y')::date                 as dt_nasc,
        try_cast(IDADEMAE as integer)                        as idademae,
        ESCMAE                                               as escmae,
        {{ col(c, 'ESCMAE2010') }}                           as escmae2010,
        {{ col(c, 'RACACORMAE') }}                           as racacormae,
        ESTCIVMAE                                            as estcivmae,
        try_cast(QTDFILVIVO as integer)                      as qtdfilvivo,
        try_cast(QTDFILMORT as integer)                      as qtdfilmort,
        try_cast({{ col(c, 'QTDGESTANT') }} as integer)      as qtdgestant,
        try_cast({{ col(c, 'QTDPARTNOR') }} as integer)      as qtdpartnor,
        try_cast({{ col(c, 'QTDPARTCES') }} as integer)      as qtdpartces,
        GESTACAO                                             as gestacao,
        try_cast({{ col(c, 'SEMAGESTAC') }} as integer)      as semagestac,
        GRAVIDEZ                                             as gravidez,
        PARTO                                                as parto,
        try_cast(CONSULTAS as integer)                       as consultas_faixa,  -- faixas: 1 nenhuma, 2 1-3, 3 4-6, 4 7+
        try_cast({{ col(c, 'CONSPRENAT') }} as integer)      as consprenat,
        {{ col(c, 'MESPRENAT') }}                            as mesprenat,
        {{ col(c, 'KOTELCHUCK') }}                           as kotelchuck,
        {{ col(c, 'TPROBSON') }}                             as tprobson,
        {{ col(c, 'STTRABPART') }}                           as sttrabpart,
        {{ col(c, 'STCESPARTO') }}                           as stcesparto,
        {{ col(c, 'TPAPRESENT') }}                           as tpapresent,
        {{ col(c, 'TPNASCASSI') }}                           as tpnascassi,
        try_cast(PESO as integer)                            as peso,
        try_cast(APGAR1 as integer)                          as apgar1,
        try_cast(APGAR5 as integer)                          as apgar5,
        SEXO                                                 as sexo,
        IDANOMAL                                             as idanomal,
        {{ col(c, 'CODANOMAL') }}                            as codanomal
    from src
)
select
    *,
    extract(year from dt_nasc)::integer as ano
from tipado
where dt_nasc is not null
