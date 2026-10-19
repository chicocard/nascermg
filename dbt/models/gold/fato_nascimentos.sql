-- Um registro por nascido vivo, com município (7 dígitos), região de saúde, estrutura do
-- estabelecimento do parto e as variáveis derivadas usadas pelos modelos conceituais.
with n as (
    select * from {{ ref('stg_sinasc') }}
),
res as (select cod6, cod7, nome, regiao_saude_cod, regiao_saude_nome, macro_cod, macro_nome, uf_sigla, lat, lon from {{ ref('dim_municipio') }}),
occ as (select cod6, cod7, nome, regiao_saude_cod, regiao_saude_nome, macro_cod, lat, lon from {{ ref('dim_municipio') }}),
est as (select * from {{ ref('dim_estabelecimento') }})
select
    n.contador                                               as id_dn,          -- identificador do registro (público)
    n.numerodn,                                              -- só existe em bases identificadas
    n.ano,
    n.dt_nasc,
    n.arquivo,
    -- território
    n.codmunres6,
    res.cod7                                                 as codmunres7,
    res.nome                                                 as municipio_res,
    res.regiao_saude_cod                                     as regiao_saude_res_cod,
    res.regiao_saude_nome                                    as regiao_saude_res,
    res.macro_cod                                            as macro_res_cod,
    res.macro_nome                                           as macro_res,
    res.uf_sigla                                             as uf_res,
    res.lat                                                  as lat_res,
    res.lon                                                  as lon_res,
    n.codmunnasc6,
    occ.cod7                                                 as codmunnasc7,
    occ.nome                                                 as municipio_nasc,
    occ.regiao_saude_cod                                     as regiao_saude_nasc_cod,
    occ.macro_cod                                            as macro_nasc_cod,
    occ.lat                                                  as lat_nasc,
    occ.lon                                                  as lon_nasc,
    n.cnes,
    n.locnasc,
    (n.codmunres6 is not null and n.codmunnasc6 is not null and n.codmunres6 <> n.codmunnasc6)::integer as parto_fora_municipio,
    (res.regiao_saude_cod is not null and occ.regiao_saude_cod is not null
        and res.regiao_saude_cod <> occ.regiao_saude_cod)::integer                              as parto_fora_regiao,
    -- mãe
    n.idademae,
    case when n.idademae < 20 then '<20' when n.idademae between 20 and 34 then '20-34'
         when n.idademae >= 35 then '35+' end               as faixa_idademae,
    n.escmae2010,
    n.racacormae,
    n.estcivmae,
    n.qtdfilvivo, n.qtdfilmort, n.qtdgestant, n.qtdpartnor, n.qtdpartces,
    (coalesce(n.qtdpartnor, 0) + coalesce(n.qtdpartces, 0) = 0)::integer as nulipara,
    (coalesce(n.qtdpartces, 0) > 0)::integer                 as cesarea_previa,
    -- gestação e parto
    n.gravidez,
    (n.gravidez in ('2','3'))::integer                       as gestacao_multipla,
    n.semagestac,
    (n.semagestac < 37)::integer                             as prematuro,
    n.parto,
    (n.parto = '2')::integer                                 as cesarea,
    case when n.tprobson in ('11','99') then null else n.tprobson end as tprobson,   -- 11 = não classificado
    n.sttrabpart,
    n.stcesparto,
    (n.stcesparto = '1')::integer                            as cesarea_sem_trabalho_parto,
    n.tpapresent,
    n.tpnascassi,
    -- pré-natal
    n.consprenat,
    n.mesprenat,
    n.kotelchuck,
    -- codificação do DATASUS conferida nos dados (MG 2023): 1 não fez pré-natal (0 consultas), 2 inadequado,
    -- 3 intermediário, 4 adequado, 5 mais que adequado, 9 não classificado
    case n.kotelchuck
        when '1' then 'sem_prenatal'  when '2' then 'inadequado'
        when '3' then 'intermediario' when '4' then 'adequado'
        when '5' then 'mais_que_adequado' else null end     as kotelchuck_cat,
    (n.kotelchuck in ('4','5'))::integer                     as prenatal_adequado,
    (n.kotelchuck = '1')::integer                            as sem_prenatal,
    (n.consprenat >= 7)::integer                             as prenatal_7mais,
    -- recém-nascido
    n.peso,
    (n.peso < 2500)::integer                                 as baixo_peso,
    (n.peso < 1500)::integer                                 as muito_baixo_peso,
    n.apgar1, n.apgar5,
    (n.apgar5 < 7)::integer                                  as apgar5_menor_7,
    n.sexo,
    n.idanomal,
    (n.idanomal = '1')::integer                              as anomalia,
    -- estrutura do hospital do parto (CNES, foto do ano)
    est.tp_unid                                              as est_tp_unid,
    est.nat_jur                                              as est_nat_jur,
    est.leitos_obstetricos                                   as est_leitos_obstetricos,
    est.leitos_uti_neonatal                                  as est_leitos_uti_neonatal,
    est.leitos_uti_adulto                                    as est_leitos_uti_adulto,
    coalesce(est.tem_uti_neonatal, 0)                        as est_tem_uti_neonatal,
    coalesce(est.tem_uti_adulto, 0)                          as est_tem_uti_adulto
from n
left join res on res.cod6 = n.codmunres6
left join occ on occ.cod6 = n.codmunnasc6
left join est on est.cnes = n.cnes and est.ano = n.ano
