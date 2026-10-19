-- GERADO AUTOMATICAMENTE por plano/plano2dbt.py em 2026-10-19 13:36 UTC. Não edite à mão: edite o plano no painel.
-- Plano: tema=gestacao-parto-nascimento | desfecho=d_mni (Mortalidade neonatal precoce (0–6 dias)) | unidade=regiao | período=trienio_movel | base territorial=residencia
-- Modelos conceituais: Modelo hierárquico de análise
-- AVISO: WARN [Proxies]: “Razão de mortalidade materna”: medidas aproximadas; declarar a construção e testar sensibilidade.
-- NÃO IMPLEMENTADA: v_idhm (IDHM, renda e desigualdade municipal) – não implementada nesta fase
-- NÃO IMPLEMENTADA: v_territorio (Região de saúde, macrorregião, urbano/rural) – categórica sem agregação definida; use-a como estratificação
-- NÃO IMPLEMENTADA: d_perinatal (Mortalidade perinatal) – não implementada nesta fase
-- NÃO IMPLEMENTADA: d_dist (Distância e tempo até o local do parto) – não implementada nesta fase
-- NÃO IMPLEMENTADA: v_codestab (Estabelecimento do parto) – categórica sem agregação definida; use-a como estratificação
-- NOTA: v_escmae: CONFERIR códigos ESCMAE2010
-- NOTA: v_idhm: Atlas/PNUD não carregado nesta fase
-- NOTA: v_estciv: código 1 = solteira; CONFERIR
-- NOTA: d_mni: SIM por residência, 0-6 dias por 1.000 NV; sem vínculo individual DN↔DO nos microdados públicos (desfecho só territorial)
-- NOTA: d_perinatal: óbitos fetais ainda não agregados no ind_territorio
-- NOTA: v_kotel: adequado + mais que adequado (códigos 4 e 5 do DATASUS, conferidos nos dados)
-- NOTA: d_dist: roteamento (OSRM) não implementado nesta fase
-- NOTA: d_rmm: sem correção por investigação; pequenos números
{{ config(materialized='table') }}
with
f as (select * from {{ ref('fato_nascimentos') }}),
t as (select * from {{ ref('ind_territorio') }} where nivel = 'regiao_saude')
select
    cast((f.ano // 3) * 3 as varchar) || '-' || cast((f.ano // 3) * 3 + 2 as varchar) as trienio,
    f.regiao_saude_res_cod as cod,
    min(f.regiao_saude_res) as nome,
    count(*) as nascidos_vivos,
    100.0*avg(case when f.escmae2010 in ('0','1','2') then 1 else 0 end) as pct_maes_ate_fundamental,
    100.0*avg(case when f.racacormae in ('2','4') then 1 else 0 end) as pct_maes_pretas_pardas,
    median(f.idademae) as med_idademae,
    median(coalesce(f.qtdfilvivo,0)+coalesce(f.qtdfilmort,0)) as med_paridade,
    100.0*avg(case when f.estcivmae = '1' then 1 else 0 end) as pct_maes_solteiras,
    avg(t.tx_mort_neonatal_precoce) as mni_territorial,
    median(f.peso) as med_peso,
    100.0*avg(f.apgar5_menor_7) as pct_apgar5,
    100.0*avg(f.anomalia) as pct_idanomal,
    100.0*avg(f.gestacao_multipla) as pct_gravidez,
    100.0*avg(f.prenatal_adequado) as pct_kotel,
    100.0*avg(f.cesarea) as pct_parto,
    100.0*avg(f.est_tem_uti_neonatal) as pct_leitos_utin,
    avg(t.leitos_utin_por_1000nv) as leitos_utin_territorial,
    avg(t.rn_uti_por_1000nv) as utin_uso_territorial,
    avg(t.rmm_declarada) as rmm_territorial
from f
left join t on t.ano = f.ano and t.cod = f.regiao_saude_res_cod
where f.regiao_saude_res_cod is not null
group by cast((f.ano // 3) * 3 as varchar) || '-' || cast((f.ano // 3) * 3 + 2 as varchar), f.regiao_saude_res_cod
