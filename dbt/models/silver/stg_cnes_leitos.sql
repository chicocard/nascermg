-- Leitos (CNES-LT) classificados pelos grupos do seed cnes_codleito.
with src as (
    select * from {{ source('bronze', 'cnes_lt') }}
),
leitos as (
    select
        CNES                                                 as cnes,
        CODUFMUN                                             as codufmun6,
        COMPETEN                                             as competen,
        substr(COMPETEN, 1, 4)::integer                      as ano,
        TP_LEITO                                             as tp_leito,
        CODLEITO                                             as codleito,
        try_cast(QT_EXIST as integer)                        as qt_exist,
        try_cast(QT_CONTR as integer)                        as qt_contr,
        try_cast(QT_SUS as integer)                          as qt_sus
    from src
)
select
    l.*,
    c.grupo,
    c.descricao as descricao_leito
from leitos l
left join {{ ref('cnes_codleito') }} c
  on lpad(l.codleito, 2, '0') = lpad(c.codleito, 2, '0')
