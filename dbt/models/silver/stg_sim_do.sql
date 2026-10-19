-- Óbitos (SIM/DO por residência). Idade convertida em dias/anos pelas macros.
-- Microdados públicos: sem NUMERODO/NUMERODN (identificadores); registro identificado por CONTADOR.
{% set c = cols_fonte('sim_do') %}
with src as (
    select * from {{ source('bronze', 'sim_do') }}
),
tipado as (
    select
        {{ nome_arquivo('filename') }}                       as arquivo,
        {{ col(c, 'CONTADOR') }}                             as contador,
        {{ col(c, 'NUMERODO') }}                             as numerodo,
        TIPOBITO                                             as tipobito,          -- 1 fetal, 2 não fetal
        try_strptime(DTOBITO, '%d%m%Y')::date                as dt_obito,
        try_strptime(DTNASC, '%d%m%Y')::date                 as dt_nasc,
        IDADE                                                as idade_cod,
        {{ sim_idade_dias('IDADE') }}                        as idade_dias,
        {{ sim_idade_anos('IDADE') }}                        as idade_anos,
        SEXO                                                 as sexo,
        RACACOR                                              as racacor,
        {{ col(c, 'ESC2010') }}                              as esc2010,
        CODMUNRES                                            as codmunres6,
        CODMUNOCOR                                           as codmunocor6,
        LOCOCOR                                              as lococor,
        CODESTAB                                             as cnes,
        CAUSABAS                                             as causabas,
        ASSISTMED                                            as assistmed,
        NECROPSIA                                            as necropsia,
        OBITOGRAV                                            as obitograv,
        OBITOPUERP                                           as obitopuerp,
        OBITOPARTO                                           as obitoparto,
        try_cast(PESO as integer)                            as peso,
        GESTACAO                                             as gestacao,
        GRAVIDEZ                                             as gravidez,
        PARTO                                                as parto,
        try_cast(IDADEMAE as integer)                        as idademae,
        {{ col(c, 'ESCMAE2010') }}                           as escmae2010,
        {{ col(c, 'NUMERODN') }}                             as numerodn,
        {{ col(c, 'TPPOS') }}                                as tppos,             -- óbito investigado (S/N)
        {{ col(c, 'FONTEINV') }}                             as fonteinv
    from src
)
select
    *,
    extract(year from dt_obito)::integer                     as ano,
    -- faixa etária útil para o tema
    case
        when idade_dias is null then null
        when idade_dias between 0 and 6   then 'neonatal_precoce'
        when idade_dias between 7 and 27  then 'neonatal_tardio'
        when idade_dias between 28 and 364 then 'pos_neonatal'
        else 'outra'
    end                                                      as faixa_infantil,
    -- óbito materno declarado (sem correção por investigação): mulher de 10 a 49 anos,
    -- causa básica do capítulo XV (exceto O96/O97, óbitos tardios) ou causas específicas incluídas pelo MS.
    case
        when sexo = '2' and idade_anos between 10 and 49 and (
                 (causabas between 'O00' and 'O959')
              or causabas like 'A34%' or causabas like 'D392%' or causabas like 'E230%'
              or causabas like 'F53%' or causabas like 'M830%'
             )
        then 1 else 0
    end                                                      as obito_materno_declarado
from tipado
where dt_obito is not null
