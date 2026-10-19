-- Internações SUS relevantes ao tema: partos, outras internações obstétricas (capítulo XV)
-- e internações de recém-nascidos (até 27 dias na admissão).
with a as (
    select * from {{ ref('stg_sih_rd') }}
),
proc as (select * from {{ ref('sigtap_partos') }}),
nm as (select distinct replace(cid_prefixo, '.', '') as cid, condicao from {{ ref('cid_near_miss') }}),
res as (select cod6, cod7, nome, regiao_saude_cod, regiao_saude_nome, macro_cod, macro_nome, lat, lon from {{ ref('dim_municipio') }}),
mov as (select cod6, cod7, nome, regiao_saude_cod, macro_cod, lat, lon from {{ ref('dim_municipio') }}),
est as (select cnes, ano, tem_uti_neonatal, tem_uti_adulto, leitos_uti_neonatal, leitos_uti_adulto, nat_jur from {{ ref('dim_estabelecimento') }}),
base as (
    select
        a.*,
        p.tipo_parto,
        p.alto_risco                                              as parto_alto_risco,
        (p.proc_rea is not null)::integer                         as internacao_parto,
        (a.diag_princ between 'O00' and 'O999')::integer          as diag_obstetrico,
        (a.idade_dias_na_internacao between 0 and 27
          or (a.cod_idade = '2' and a.idade between 0 and 27))::integer as recem_nascido,
        (a.diag_princ between 'P00' and 'P969')::integer          as diag_perinatal
    from a
    left join proc p on p.proc_rea = a.proc_rea
)
select
    b.n_aih,
    b.ano,
    b.ano_cmpt,
    b.mes_cmpt,
    b.dt_inter,
    b.dt_saida,
    b.dias_perm,
    -- tipo de episódio
    case
        when b.internacao_parto = 1 then 'parto'
        when b.recem_nascido = 1 then 'recem_nascido'
        when b.diag_obstetrico = 1 then 'obstetrica_nao_parto'
        else 'outra'
    end                                                           as tipo_episodio,
    b.tipo_parto,
    b.parto_alto_risco,
    (b.tipo_parto = 'cesarea')::integer                           as cesarea,
    b.recem_nascido,
    b.diag_perinatal,
    -- território e fluxo residência -> hospital
    b.munic_res6,
    res.cod7                                                      as codmunres7,
    res.nome                                                      as municipio_res,
    res.regiao_saude_cod                                          as regiao_saude_res_cod,
    res.regiao_saude_nome                                         as regiao_saude_res,
    res.macro_cod                                                 as macro_res_cod,
    res.macro_nome                                                as macro_res,
    b.munic_mov6,
    mov.cod7                                                      as codmunmov7,
    mov.nome                                                      as municipio_mov,
    mov.regiao_saude_cod                                          as regiao_saude_mov_cod,
    mov.macro_cod                                                 as macro_mov_cod,
    (b.munic_res6 <> b.munic_mov6)::integer                       as internacao_fora_municipio,
    (res.regiao_saude_cod is not null and mov.regiao_saude_cod is not null
        and res.regiao_saude_cod <> mov.regiao_saude_cod)::integer as internacao_fora_regiao,
    b.cnes,
    est.nat_jur                                                   as est_nat_jur,
    coalesce(est.tem_uti_neonatal, 0)                             as est_tem_uti_neonatal,
    coalesce(est.tem_uti_adulto, 0)                               as est_tem_uti_adulto,
    -- clínica
    b.diag_princ,
    b.diag_secun,
    b.gestrisco,
    b.car_int,
    (b.car_int = '2')::integer                                    as urgencia,
    -- UTI
    coalesce(b.uti_dias, 0)                                       as uti_dias,
    (coalesce(b.uti_dias, 0) > 0)::integer                        as usou_uti,
    b.marca_uti,
    case when b.marca_uti in ('80','81','82') then 'neonatal'
         when b.marca_uti in ('74','75','76') then 'adulto'
         when b.marca_uti in ('77','78','79') then 'pediatrica'
         when coalesce(b.uti_dias, 0) > 0 then 'outra' end        as uti_tipo,
    -- saída
    b.morte,
    (b.morte = '1')::integer                                      as obito_hospitalar,
    b.cobranca,
    -- motivo de saída (seed sih_cobranca): transferência = 31/32; desfechos obstétricos = 61-67
    (b.cobranca like '3%')::integer                               as transferencia,
    cb.grupo                                                      as motivo_saida_grupo,
    (b.cobranca in ('62','67'))::integer                          as rn_permaneceu_internado,
    (b.cobranca = '63')::integer                                  as rn_obito_na_alta_materna,
    (b.cobranca = '64')::integer                                  as obito_fetal_registrado,
    (b.cobranca in ('65','66','67'))::integer                     as obito_materno_registrado,
    -- near miss (proxy): condição sentinela no diagnóstico principal ou UTI ou óbito em internação obstétrica
    (b.diag_obstetrico = 1 or b.internacao_parto = 1)::integer    as episodio_obstetrico,
    (select min(nm.condicao) from nm where b.diag_princ like nm.cid || '%') as condicao_sentinela,
    case when (b.diag_obstetrico = 1 or b.internacao_parto = 1)
          and (exists (select 1 from nm where b.diag_princ like nm.cid || '%')
               or coalesce(b.uti_dias, 0) > 0 or b.morte = '1')
         then 1 else 0 end                                        as near_miss_proxy,
    -- recém-nascido
    b.dt_nasc                                                     as dt_nasc_paciente,
    b.idade_dias_na_internacao,
    b.sexo,
    b.raca_cor,
    b.val_tot,
    b.val_uti
from base b
left join {{ ref('sih_cobranca') }} cb on cb.cobranca = b.cobranca
left join res on res.cod6 = b.munic_res6
left join mov on mov.cod6 = b.munic_mov6
left join est on est.cnes = b.cnes and est.ano = b.ano
where b.internacao_parto = 1 or b.diag_obstetrico = 1 or b.recem_nascido = 1
