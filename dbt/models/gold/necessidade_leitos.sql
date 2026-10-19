-- Necessidade de leitos obstétricos, neonatais e de UTI por território (município e região de saúde) e ano,
-- segundo duas fontes de parâmetros:
--   (A) Rede Cegonha (Portaria SAS/MS 650/2011; Caderno 1/2017, Quadro 5): fórmulas fixas sobre nascidos vivos;
--   (B) Equações 1 e 3 do Caderno 1 (Seção V; Projeto Parâmetros NESCON/UFMG): NL = NI x TMP / (365 x rho) x Fnr,
--       com NI = Pop x TI x FR, usando valores OBSERVADOS no SIH (TMP, TI, proporção com UTI) limitados às
--       faixas de referência (P25-P75 das capitais).
-- Oferta existente: CNES (leitos localizados no território). Necessidade: população RESIDENTE.
-- Fnr corrige a necessidade pela proporção de não residentes atendidos nos hospitais do território.
{% set p = var('parametros') %}
{% set niveis = [
    {'nivel': 'municipio',    'res': 'codmunres7',           'mov': 'codmunmov7',          'est': 'codmun7'},
    {'nivel': 'regiao_saude', 'res': 'regiao_saude_res_cod', 'mov': 'regiao_saude_mov_cod', 'est': 'regiao_saude_cod'},
    {'nivel': 'macrorregiao', 'res': 'macro_res_cod',        'mov': 'macro_mov_cod',        'est': 'macro_cod'},
] %}

with internacoes as (
    select * from {{ ref('fato_internacoes_obst') }}
),
nv as (
    select ano, nivel, cod, nome, nascidos_vivos
    from {{ ref('ind_territorio') }}
),
{% for n in niveis %}
res_{{ n.nivel }} as (
    -- demanda dos residentes (SIH)
    select
        ano, {{ n.res }} as cod,
        sum(case when tipo_episodio = 'parto' then 1 else 0 end)                              as partos_sus,
        avg(case when tipo_episodio = 'parto' then dias_perm end)                            as tmp_obst_obs,
        avg(case when tipo_episodio in ('parto','obstetrica_nao_parto') then usou_uti end)   as p_uti_obst_obs,
        avg(case when tipo_episodio in ('parto','obstetrica_nao_parto') and usou_uti = 1 then uti_dias end) as tmp_uti_obst_obs,
        sum(case when tipo_episodio = 'recem_nascido' then 1 else 0 end)                      as internacoes_rn,
        avg(case when tipo_episodio = 'recem_nascido' then dias_perm end)                    as tmp_neonat_obs,
        avg(case when tipo_episodio = 'recem_nascido' then usou_uti end)                     as p_uti_neonat_obs,
        avg(case when tipo_episodio = 'recem_nascido' and usou_uti = 1 then uti_dias end)    as tmp_uti_neonat_obs
    from internacoes
    where {{ n.res }} is not null
    group by ano, {{ n.res }}
),
loc_{{ n.nivel }} as (
    -- internações ocorridas nos hospitais do território: proporção de residentes (PIr) -> Fnr = 100/PIr
    select
        ano, {{ n.mov }} as cod,
        count(*) as internacoes_no_territorio,
        avg(case when {{ n.res }} = {{ n.mov }} then 1.0 else 0.0 end) as pir
    from internacoes
    where {{ n.mov }} is not null and tipo_episodio in ('parto','obstetrica_nao_parto','recem_nascido')
    group by ano, {{ n.mov }}
),
est_{{ n.nivel }} as (
    select
        ano, {{ n.est }} as cod,
        sum(leitos_obstetricos)      as leitos_obst_exist,
        sum(leitos_obstetricos_sus)  as leitos_obst_sus_exist,
        sum(leitos_uti_neonatal)     as leitos_utin_exist,
        sum(leitos_uti_neonatal_sus) as leitos_utin_sus_exist,
        sum(leitos_uci_neonatal)     as leitos_ucin_exist,
        sum(leitos_uti_adulto)       as leitos_uti_adulto_exist,
        count(*) filter (where tem_leito_obstetrico = 1) as hospitais_com_obstetricia,
        count(*) filter (where tem_uti_neonatal = 1)     as hospitais_com_utin
    from {{ ref('dim_estabelecimento') }}
    where {{ n.est }} is not null
    group by ano, {{ n.est }}
),
{% endfor %}
base as (
    {% for n in niveis %}
    select v.ano, v.nivel, v.cod, v.nome, v.nascidos_vivos,
           r.partos_sus, r.tmp_obst_obs, r.p_uti_obst_obs, r.tmp_uti_obst_obs,
           r.internacoes_rn, r.tmp_neonat_obs, r.p_uti_neonat_obs, r.tmp_uti_neonat_obs,
           l.internacoes_no_territorio, l.pir,
           e.leitos_obst_exist, e.leitos_obst_sus_exist, e.leitos_utin_exist, e.leitos_utin_sus_exist,
           e.leitos_ucin_exist, e.leitos_uti_adulto_exist, e.hospitais_com_obstetricia, e.hospitais_com_utin
    from nv v
    left join res_{{ n.nivel }} r using (ano, cod)
    left join loc_{{ n.nivel }} l using (ano, cod)
    left join est_{{ n.nivel }} e using (ano, cod)
    where v.nivel = '{{ n.nivel }}'
    {% if not loop.last %} union all {% endif %}
    {% endfor %}
),
calc as (
    select
        *,
        -- populações-alvo (Quadro 1)
        nascidos_vivos * {{ p.fator_subregistro }}                                   as gestantes_estimadas,
        nascidos_vivos * {{ p.fator_subregistro }} * {{ p.prop_alto_risco }}        as gestantes_alto_risco,
        nascidos_vivos * {{ p.fator_subregistro }} * {{ p.prop_alto_risco_hospitalar }} as gestantes_alto_risco_hospitalar,
        -- proporção SUS: parâmetro normativo (prop_sem_plano) ou observada (partos SUS / NV), limitada a 1
        {% if p.prop_sem_plano %} {{ p.prop_sem_plano }} {% else %} least(1.0, coalesce(partos_sus, 0) * 1.0 / nullif(nascidos_vivos, 0)) {% endif %} as prop_sus,
        -- valores observados limitados às faixas de referência (fallback = ponto médio da faixa)
        least({{ p.tmp_obst_max }}, greatest({{ p.tmp_obst_min }}, coalesce(tmp_obst_obs, ({{ p.tmp_obst_min }} + {{ p.tmp_obst_max }}) / 2)))                 as tmp_obst_usado,
        least({{ p.ti_neonat_max }}, greatest({{ p.ti_neonat_min }}, coalesce(1000.0 * internacoes_rn / nullif(partos_sus, 0), ({{ p.ti_neonat_min }} + {{ p.ti_neonat_max }}) / 2))) as ti_neonat_usada,
        least({{ p.tmp_neonat_max }}, greatest({{ p.tmp_neonat_min }}, coalesce(tmp_neonat_obs, ({{ p.tmp_neonat_min }} + {{ p.tmp_neonat_max }}) / 2)))         as tmp_neonat_usado,
        least({{ p.p_uti_neonat_max }}, greatest({{ p.p_uti_neonat_min }}, coalesce(p_uti_neonat_obs, ({{ p.p_uti_neonat_min }} + {{ p.p_uti_neonat_max }}) / 2))) as p_uti_neonat_usada,
        least({{ p.tmp_uti_neonat_max }}, greatest({{ p.tmp_uti_neonat_min }}, coalesce(tmp_uti_neonat_obs, ({{ p.tmp_uti_neonat_min }} + {{ p.tmp_uti_neonat_max }}) / 2))) as tmp_uti_neonat_usado,
        least({{ p.p_uti_obst_max }}, greatest({{ p.p_uti_obst_min }}, coalesce(p_uti_obst_obs, ({{ p.p_uti_obst_min }} + {{ p.p_uti_obst_max }}) / 2)))         as p_uti_obst_usada,
        least({{ p.tmp_uti_obst_max }}, greatest({{ p.tmp_uti_obst_min }}, coalesce(tmp_uti_obst_obs, ({{ p.tmp_uti_obst_min }} + {{ p.tmp_uti_obst_max }}) / 2))) as tmp_uti_obst_usado,
        -- Fnr = 100 / PIr; sem hospitais no território, Fnr = 1 (a necessidade existe, mas é atendida fora)
        case when pir is null or pir = 0 then 1.0 else 1.0 / pir end                         as fnr
    from base
),
final as (
    select
        *,
        -- (A) Rede Cegonha
        ((nascidos_vivos * {{ p.fator_subregistro }}) * {{ p.rc_tmp_obst }}) / (365.0 * {{ p.rc_ocupacao }}) * {{ p.rc_ajuste_outros_proc }} as leitos_obst_nec_rc,
        nascidos_vivos * {{ p.rc_utin_por_1000nv }} / 1000.0                                  as leitos_utin_nec_rc,
        nascidos_vivos * {{ p.rc_ucin_por_1000nv }} / 1000.0                                  as leitos_ucin_nec_rc,
        nascidos_vivos * {{ p.rc_canguru_por_1000nv }} / 1000.0                               as leitos_canguru_nec_rc,
        -- (B) Equação 1: obstetrícia SUS (TI = 1,0)
        (gestantes_estimadas * prop_sus * 1.0 * {{ p.fator_recusa }}) * tmp_obst_usado / (365.0 * {{ p.ocupacao_esperada }}) * fnr as leitos_obst_nec_eq1,
        -- (B) Equação 1: neonatologia SUS
        (nascidos_vivos * {{ p.fator_subregistro }} * prop_sus * ti_neonat_usada / 1000.0 * {{ p.fator_recusa }}) * tmp_neonat_usado / (365.0 * {{ p.ocupacao_esperada }}) * fnr as leitos_neonat_nec_eq1,
        -- (B) Equação 3: UTI neonatal e UTI adulto (obstetrícia)
        (nascidos_vivos * {{ p.fator_subregistro }} * prop_sus * ti_neonat_usada / 1000.0 * {{ p.fator_recusa }}) * p_uti_neonat_usada * tmp_uti_neonat_usado
            / (365.0 * {{ p.ocupacao_esperada }} * {{ p.disponibilidade_uti }}) * fnr          as leitos_utin_nec_eq3,
        (gestantes_estimadas * prop_sus * {{ p.fator_recusa }}) * p_uti_obst_usada * tmp_uti_obst_usado
            / (365.0 * {{ p.ocupacao_esperada }} * {{ p.disponibilidade_uti }}) * fnr          as leitos_uti_adulto_obst_nec_eq3
    from calc
)
select
    ano, nivel, cod, nome, nascidos_vivos, partos_sus, internacoes_rn,
    round(gestantes_estimadas, 0)               as gestantes_estimadas,
    round(gestantes_alto_risco, 0)              as gestantes_alto_risco,
    round(gestantes_alto_risco_hospitalar, 0)   as gestantes_alto_risco_hospitalar,
    round(prop_sus, 3)                          as prop_sus,
    round(pir, 3)                               as pir,
    round(fnr, 3)                               as fnr,
    hospitais_com_obstetricia, hospitais_com_utin,
    -- parâmetros efetivamente usados (observado limitado à faixa)
    round(tmp_obst_obs, 2) as tmp_obst_obs, round(tmp_obst_usado, 2) as tmp_obst_usado,
    round(1000.0 * internacoes_rn / nullif(partos_sus, 0), 1) as ti_neonat_obs, round(ti_neonat_usada, 1) as ti_neonat_usada,
    round(tmp_neonat_obs, 2) as tmp_neonat_obs, round(tmp_neonat_usado, 2) as tmp_neonat_usado,
    round(p_uti_neonat_obs, 4) as p_uti_neonat_obs, round(p_uti_neonat_usada, 4) as p_uti_neonat_usada,
    round(tmp_uti_neonat_obs, 2) as tmp_uti_neonat_obs, round(tmp_uti_neonat_usado, 2) as tmp_uti_neonat_usado,
    round(p_uti_obst_obs, 4) as p_uti_obst_obs, round(p_uti_obst_usada, 4) as p_uti_obst_usada,
    -- oferta existente (CNES, localização)
    coalesce(leitos_obst_exist, 0)       as leitos_obst_exist,
    coalesce(leitos_obst_sus_exist, 0)   as leitos_obst_sus_exist,
    coalesce(leitos_utin_exist, 0)       as leitos_utin_exist,
    coalesce(leitos_utin_sus_exist, 0)   as leitos_utin_sus_exist,
    coalesce(leitos_ucin_exist, 0)       as leitos_ucin_exist,
    coalesce(leitos_uti_adulto_exist, 0) as leitos_uti_adulto_exist,
    -- necessidade
    round(leitos_obst_nec_rc, 1)         as leitos_obst_nec_rc,
    round(leitos_obst_nec_rc * {{ p.rc_leitos_gar_prop }}, 1) as leitos_gar_nec_rc,
    round(leitos_obst_nec_rc * {{ p.rc_uti_adulto_prop }}, 1) as leitos_uti_adulto_nec_rc,
    round(leitos_utin_nec_rc, 1)         as leitos_utin_nec_rc,
    round(leitos_ucin_nec_rc, 1)         as leitos_ucin_nec_rc,
    round(leitos_canguru_nec_rc, 1)      as leitos_canguru_nec_rc,
    round(leitos_obst_nec_eq1, 1)        as leitos_obst_sus_nec_eq1,
    round(leitos_neonat_nec_eq1, 1)      as leitos_neonat_sus_nec_eq1,
    round(leitos_utin_nec_eq3, 1)        as leitos_utin_sus_nec_eq3,
    round(leitos_uti_adulto_obst_nec_eq3, 1) as leitos_uti_adulto_obst_sus_nec_eq3,
    -- saldos (existente - necessário): negativo = déficit
    round(coalesce(leitos_obst_exist, 0) - leitos_obst_nec_rc, 1)          as saldo_obst_rc,
    round(coalesce(leitos_obst_sus_exist, 0) - leitos_obst_nec_eq1, 1)     as saldo_obst_sus_eq1,
    round(coalesce(leitos_utin_exist, 0) - leitos_utin_nec_rc, 1)          as saldo_utin_rc,
    round(coalesce(leitos_utin_sus_exist, 0) - leitos_utin_nec_eq3, 1)     as saldo_utin_sus_eq3,
    round(coalesce(leitos_ucin_exist, 0) - leitos_ucin_nec_rc, 1)          as saldo_ucin_rc,
    -- razões oferta/necessidade (1 = equilíbrio)
    round(coalesce(leitos_obst_exist, 0) / nullif(leitos_obst_nec_rc, 0), 2)      as razao_obst_rc,
    round(coalesce(leitos_utin_exist, 0) / nullif(leitos_utin_nec_rc, 0), 2)      as razao_utin_rc,
    round(coalesce(leitos_utin_sus_exist, 0) / nullif(leitos_utin_nec_eq3, 0), 2) as razao_utin_sus_eq3
from final
