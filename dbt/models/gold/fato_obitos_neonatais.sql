-- Óbitos de 0 a 27 dias (por residência), com vínculo à DN quando NUMERODN estiver preenchido.
with o as (
    select * from {{ ref('stg_sim_do') }}
    where tipobito = '2' and idade_dias between 0 and 27
),
res as (select cod6, cod7, nome, regiao_saude_cod, regiao_saude_nome, macro_cod, macro_nome from {{ ref('dim_municipio') }}),
occ as (select cod6, cod7, nome from {{ ref('dim_municipio') }}),
est as (select cnes, ano, tem_uti_neonatal, tem_uti_adulto, leitos_uti_neonatal from {{ ref('dim_estabelecimento') }})
select
    o.contador                                                as id_do,
    o.numerodo,
    o.numerodn,
    (o.numerodn is not null and length(o.numerodn) >= 8)::integer as tem_vinculo_dn,
    o.ano,
    o.dt_obito,
    o.dt_nasc,
    o.idade_dias,
    o.faixa_infantil,
    (o.faixa_infantil = 'neonatal_precoce')::integer          as neonatal_precoce,
    (o.idade_dias = 0)::integer                               as obito_dia_zero,
    o.codmunres6,
    res.cod7                                                  as codmunres7,
    res.nome                                                  as municipio_res,
    res.regiao_saude_cod                                      as regiao_saude_res_cod,
    res.regiao_saude_nome                                     as regiao_saude_res,
    res.macro_cod                                             as macro_res_cod,
    res.macro_nome                                            as macro_res,
    o.codmunocor6,
    occ.cod7                                                  as codmunocor7,
    o.lococor,
    (o.lococor in ('1','2'))::integer                         as obito_em_estabelecimento,  -- 1 hospital, 2 outro estab.
    o.cnes,
    coalesce(est.tem_uti_neonatal, 0)                         as est_obito_tem_utin,
    o.causabas,
    substr(o.causabas, 1, 1)                                  as causabas_capitulo_letra,
    case
        when o.causabas between 'P00' and 'P969' then 'perinatal (P)'
        when o.causabas between 'Q00' and 'Q999' then 'anomalias congenitas (Q)'
        when o.causabas between 'A00' and 'B999' then 'infecciosas (A-B)'
        when o.causabas between 'J00' and 'J999' then 'respiratorias (J)'
        when o.causabas between 'R00' and 'R999' then 'mal definidas (R)'
        else 'outras'
    end                                                       as grupo_causa,
    o.obitoparto,
    o.peso,
    (o.peso < 2500)::integer                                  as baixo_peso,
    o.gestacao,
    o.gravidez,
    o.parto,
    o.idademae,
    o.escmae2010,
    o.racacor,
    o.sexo,
    o.tppos                                                   as investigado,
    o.assistmed
from o
left join res on res.cod6 = o.codmunres6
left join occ on occ.cod6 = o.codmunocor6
left join est on est.cnes = o.cnes and est.ano = o.ano
