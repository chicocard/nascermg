-- Indicadores por território de RESIDÊNCIA (município e região de saúde) e ano.
-- Denominador: nascidos vivos (SINASC). Óbitos neonatais do SIM por residência.
-- Taxas em áreas pequenas são instáveis: use o campo nascidos_vivos para suprimir/suavizar.
-- depends_on: {{ ref('stg_sinan_sifc') }}
{% set niveis = [
    {'nivel': 'municipio',     'cod': 'codmunres7',           'nome': 'municipio_res'},
    {'nivel': 'regiao_saude',  'cod': 'regiao_saude_res_cod', 'nome': 'regiao_saude_res'},
    {'nivel': 'macrorregiao',  'cod': 'macro_res_cod',        'nome': 'macro_res'},
] %}

{#- anos com arquivo do SINAN carregado (ano de notificação): a taxa de sífilis congênita só é calculada neles;
    nos demais fica nula em vez de zero por ausência de dados -#}
{% if execute %}
  {% set r = run_query("select coalesce(string_agg(distinct cast(ano_notificacao as varchar), ','), '') from " ~ ref('stg_sinan_sifc')) %}
  {% set anos_sifc = r.columns[0].values()[0] %}
{% else %}
  {% set anos_sifc = '' %}
{% endif %}
{% set sifc_anos = "(n.ano in (" ~ anos_sifc ~ "))::integer" if anos_sifc else "0" %}

with
{% for n in niveis %}
nasc_{{ n.nivel }} as (
    select
        ano,
        '{{ n.nivel }}'                                       as nivel,
        {{ n.cod }}                                           as cod,
        min({{ n.nome }})                                     as nome,
        count(*)                                              as nascidos_vivos,
        sum(cesarea)                                          as cesareas,
        sum(cesarea_sem_trabalho_parto)                       as cesareas_sem_tp,
        sum(case when tprobson in ('01','1','02','2','05','5') then cesarea else 0 end) as cesareas_robson_1_2_5,
        sum(parto_fora_municipio)                             as partos_fora_municipio,
        sum(parto_fora_regiao)                                as partos_fora_regiao,
        sum(prematuro)                                        as prematuros,
        sum(baixo_peso)                                       as baixo_peso,
        sum(apgar5_menor_7)                                   as apgar5_menor_7,
        sum(prenatal_adequado)                                as prenatal_adequado,
        sum(prenatal_7mais)                                   as prenatal_7mais,
        sum(case when idademae < 20 then 1 else 0 end)        as maes_menor_20,
        sum(case when escmae2010 in ('0','1','2') then 1 else 0 end) as maes_ate_fundamental,  -- CONFERIR códigos ESCMAE2010
        sum(est_tem_uti_neonatal)                             as partos_em_hospital_com_utin,
        sum(case when locnasc = '1' then 1 else 0 end)        as partos_hospitalares
    from {{ ref('fato_nascimentos') }}
    where {{ n.cod }} is not null
    group by ano, {{ n.cod }}
),
obit_{{ n.nivel }} as (
    select
        ano,
        {{ n.cod }}                                           as cod,
        count(*)                                              as obitos_neonatais,
        sum(neonatal_precoce)                                 as obitos_neonatais_precoces,
        sum(obito_dia_zero)                                   as obitos_dia_zero,
        sum(tem_vinculo_dn)                                   as obitos_com_vinculo_dn,
        sum(case when grupo_causa = 'anomalias congenitas (Q)' then 1 else 0 end) as obitos_anomalias
    from {{ ref('fato_obitos_neonatais') }}
    where {{ n.cod }} is not null
    group by ano, {{ n.cod }}
),
mat_{{ n.nivel }} as (
    select ano, {{ n.cod }} as cod, sum(obito_materno_declarado) as obitos_maternos_declarados
    from {{ ref('fato_obitos_maternos') }}
    where {{ n.cod }} is not null
    group by ano, {{ n.cod }}
),
sifc_{{ n.nivel }} as (
    select
        ano, {{ n.cod }} as cod,
        count(*)                                  as casos_sifc,
        sum(sem_prenatal)                         as sifc_sem_prenatal,
        sum(diag_materno_tardio_ou_nao)           as sifc_diag_materno_tardio,
        sum(tratamento_inadequado_ou_nao)         as sifc_trat_inadequado,
        sum(obito_por_sc)                         as sifc_obitos,
        sum(aborto_ou_natimorto)                  as sifc_abortos_natimortos
    from {{ ref('fato_sifilis_congenita') }}
    where {{ n.cod }} is not null
    group by ano, {{ n.cod }}
),
sih_{{ n.nivel }} as (
    select
        ano, {{ n.cod }} as cod,
        sum(case when tipo_episodio = 'parto' then 1 else 0 end)                    as partos_sus,
        sum(case when tipo_episodio = 'parto' then cesarea else 0 end)              as cesareas_sus,
        sum(case when tipo_episodio = 'parto' then internacao_fora_municipio else 0 end) as partos_sus_fora_municipio,
        sum(case when tipo_episodio in ('parto','obstetrica_nao_parto') then usou_uti else 0 end) as uti_materna,
        sum(case when tipo_episodio in ('parto','obstetrica_nao_parto') then near_miss_proxy else 0 end) as near_miss_proxy,
        sum(case when tipo_episodio in ('parto','obstetrica_nao_parto') then transferencia else 0 end) as transferencias_obst,
        sum(case when tipo_episodio = 'recem_nascido' then 1 else 0 end)            as internacoes_rn,
        sum(case when tipo_episodio = 'recem_nascido' then usou_uti else 0 end)     as rn_em_uti,
        sum(case when tipo_episodio = 'recem_nascido' then internacao_fora_municipio else 0 end) as rn_internado_fora_municipio
    from {{ ref('fato_internacoes_obst') }}
    where {{ n.cod }} is not null
    group by ano, {{ n.cod }}
),
{% endfor %}
leitos_mun as (
    select ano, codmun7 as cod, sum(leitos_obstetricos) as leitos_obstetricos, sum(leitos_uti_neonatal) as leitos_uti_neonatal,
           sum(leitos_uti_adulto) as leitos_uti_adulto, max(tem_uti_neonatal) as tem_utin
    from {{ ref('dim_estabelecimento') }} where codmun7 is not null group by ano, codmun7
),
leitos_reg as (
    select ano, regiao_saude_cod as cod, sum(leitos_obstetricos) as leitos_obstetricos, sum(leitos_uti_neonatal) as leitos_uti_neonatal,
           sum(leitos_uti_adulto) as leitos_uti_adulto, max(tem_uti_neonatal) as tem_utin
    from {{ ref('dim_estabelecimento') }} where regiao_saude_cod is not null group by ano, regiao_saude_cod
),
leitos_mac as (
    select ano, macro_cod as cod, sum(leitos_obstetricos) as leitos_obstetricos, sum(leitos_uti_neonatal) as leitos_uti_neonatal,
           sum(leitos_uti_adulto) as leitos_uti_adulto, max(tem_uti_neonatal) as tem_utin
    from {{ ref('dim_estabelecimento') }} where macro_cod is not null group by ano, macro_cod
),
uniao as (
    {% for n in niveis %}
    select n.*, o.obitos_neonatais, o.obitos_neonatais_precoces, o.obitos_dia_zero, o.obitos_com_vinculo_dn, o.obitos_anomalias,
           m.obitos_maternos_declarados, l.leitos_obstetricos, l.leitos_uti_neonatal, l.leitos_uti_adulto, l.tem_utin,
           s.partos_sus, s.cesareas_sus, s.partos_sus_fora_municipio, s.uti_materna, s.near_miss_proxy, s.transferencias_obst,
           s.internacoes_rn, s.rn_em_uti, s.rn_internado_fora_municipio,
           f.casos_sifc, f.sifc_sem_prenatal, f.sifc_diag_materno_tardio, f.sifc_trat_inadequado, f.sifc_obitos, f.sifc_abortos_natimortos,
           {{ sifc_anos }} as sifc_disponivel
    from nasc_{{ n.nivel }} n
    left join obit_{{ n.nivel }} o using (ano, cod)
    left join mat_{{ n.nivel }} m using (ano, cod)
    left join sih_{{ n.nivel }} s using (ano, cod)
    left join sifc_{{ n.nivel }} f using (ano, cod)
    left join {{ 'leitos_mun' if n.nivel == 'municipio' else ('leitos_mac' if n.nivel == 'macrorregiao' else 'leitos_reg') }} l using (ano, cod)
    {% if not loop.last %} union all {% endif %}
    {% endfor %}
)
select
    *,
    round(100.0 * cesareas / nascidos_vivos, 1)                          as pct_cesarea,
    round(100.0 * cesareas_sem_tp / nascidos_vivos, 1)                   as pct_cesarea_sem_tp,
    round(100.0 * partos_fora_municipio / nascidos_vivos, 1)             as pct_parto_fora_municipio,
    round(100.0 * partos_fora_regiao / nascidos_vivos, 1)                as pct_parto_fora_regiao,
    round(100.0 * prematuros / nascidos_vivos, 1)                        as pct_prematuro,
    round(100.0 * baixo_peso / nascidos_vivos, 1)                        as pct_baixo_peso,
    round(100.0 * prenatal_adequado / nascidos_vivos, 1)                 as pct_prenatal_adequado,
    round(100.0 * prenatal_7mais / nascidos_vivos, 1)                    as pct_prenatal_7mais,
    round(100.0 * partos_em_hospital_com_utin / nascidos_vivos, 1)       as pct_partos_hospital_com_utin,
    round(1000.0 * coalesce(obitos_neonatais, 0) / nascidos_vivos, 2)    as tx_mort_neonatal,
    round(1000.0 * coalesce(obitos_neonatais_precoces, 0) / nascidos_vivos, 2) as tx_mort_neonatal_precoce,
    round(100000.0 * coalesce(obitos_maternos_declarados, 0) / nascidos_vivos, 1) as rmm_declarada,
    round(1000.0 * coalesce(leitos_obstetricos, 0) / nascidos_vivos, 2)  as leitos_obst_por_1000nv,
    round(1000.0 * coalesce(leitos_uti_neonatal, 0) / nascidos_vivos, 2) as leitos_utin_por_1000nv,
    round(100.0 * coalesce(partos_sus, 0) / nascidos_vivos, 1)           as pct_partos_sus,
    round(100.0 * coalesce(cesareas_sus, 0) / nullif(partos_sus, 0), 1)  as pct_cesarea_sus,
    round(1000.0 * coalesce(near_miss_proxy, 0) / nascidos_vivos, 2)     as near_miss_por_1000nv,
    round(1000.0 * coalesce(uti_materna, 0) / nascidos_vivos, 2)         as uti_materna_por_1000nv,
    round(1000.0 * coalesce(rn_em_uti, 0) / nascidos_vivos, 2)           as rn_uti_por_1000nv,
    round(1000.0 * coalesce(transferencias_obst, 0) / nascidos_vivos, 2) as transferencias_obst_por_1000nv,
    case when sifc_disponivel = 1 then round(1000.0 * coalesce(casos_sifc, 0) / nascidos_vivos, 2) end as tx_sifc_por_1000nv,
    round(100.0 * sifc_sem_prenatal / nullif(casos_sifc, 0), 1)          as pct_sifc_sem_prenatal,
    round(100.0 * sifc_diag_materno_tardio / nullif(casos_sifc, 0), 1)   as pct_sifc_diag_materno_tardio,
    round(100.0 * sifc_trat_inadequado / nullif(casos_sifc, 0), 1)       as pct_sifc_trat_inadequado,
    (nascidos_vivos < 500)::integer                                      as area_pequena  -- sinal para suprimir/suavizar taxas
from uniao
