-- Estabelecimentos por ano (foto da competência carregada), com leitos por grupo.
with est as (
    select * from {{ ref('stg_cnes') }}
    qualify row_number() over (partition by cnes, ano order by competen desc) = 1
),
leitos_ultima_comp as (
    select * from {{ ref('stg_cnes_leitos') }}
    qualify competen = max(competen) over (partition by cnes, ano)
),
leitos as (
    select
        cnes, ano,
        sum(case when grupo = 'obst_cirurgica' then qt_exist else 0 end)   as leitos_obst_cirurgica,
        sum(case when grupo = 'obst_clinica'   then qt_exist else 0 end)   as leitos_obst_clinica,
        sum(case when grupo in ('obst_cirurgica','obst_clinica') then qt_exist else 0 end) as leitos_obstetricos,
        sum(case when grupo in ('obst_cirurgica','obst_clinica') then qt_sus   else 0 end) as leitos_obstetricos_sus,
        sum(case when grupo = 'uti_neonatal'   then qt_exist else 0 end)   as leitos_uti_neonatal,
        sum(case when grupo = 'uti_neonatal'   then qt_sus   else 0 end)   as leitos_uti_neonatal_sus,
        sum(case when grupo = 'uci_neonatal'   then qt_exist else 0 end)   as leitos_uci_neonatal,
        sum(case when grupo = 'uti_adulto'     then qt_exist else 0 end)   as leitos_uti_adulto,
        sum(case when grupo = 'uti_pediatrica' then qt_exist else 0 end)   as leitos_uti_pediatrica,
        sum(qt_exist)                                                        as leitos_total
    from leitos_ultima_comp
    group by cnes, ano
)
select
    e.cnes,
    e.ano,
    e.competen,
    e.codufmun6,
    m.cod7                                                   as codmun7,
    m.nome                                                   as municipio,
    m.regiao_saude_cod,
    m.regiao_saude_nome,
    m.macro_cod,
    m.macro_nome,
    m.lat                                                    as lat_municipio,
    m.lon                                                    as lon_municipio,
    e.tp_unid,
    e.nat_jur,
    e.vinc_sus,
    e.tpgestao,
    coalesce(l.leitos_obst_cirurgica, 0)                     as leitos_obst_cirurgica,
    coalesce(l.leitos_obst_clinica, 0)                       as leitos_obst_clinica,
    coalesce(l.leitos_obstetricos, 0)                        as leitos_obstetricos,
    coalesce(l.leitos_obstetricos_sus, 0)                    as leitos_obstetricos_sus,
    coalesce(l.leitos_uti_neonatal, 0)                       as leitos_uti_neonatal,
    coalesce(l.leitos_uti_neonatal_sus, 0)                   as leitos_uti_neonatal_sus,
    coalesce(l.leitos_uci_neonatal, 0)                       as leitos_uci_neonatal,
    coalesce(l.leitos_uti_adulto, 0)                         as leitos_uti_adulto,
    coalesce(l.leitos_uti_pediatrica, 0)                     as leitos_uti_pediatrica,
    coalesce(l.leitos_total, 0)                              as leitos_total,
    (coalesce(l.leitos_obstetricos, 0) > 0)::integer         as tem_leito_obstetrico,
    (coalesce(l.leitos_uti_neonatal, 0) > 0)::integer        as tem_uti_neonatal,
    (coalesce(l.leitos_uti_adulto, 0) > 0)::integer          as tem_uti_adulto
from est e
left join leitos l on l.cnes = e.cnes and l.ano = e.ano
left join {{ ref('dim_municipio') }} m on m.cod6 = e.codufmun6
