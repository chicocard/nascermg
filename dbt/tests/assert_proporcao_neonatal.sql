-- Sanidade da decodificação da idade no SIM: entre os óbitos de menores de 1 ano,
-- a fração neonatal (0-27 dias) no Brasil recente fica em torno de 0,65-0,75.
-- Falha (linhas retornadas) se algum ano sair de 0,45-0,90: revise a macro sim_idade_dias.
with t as (
    select ano,
           sum(case when idade_dias between 0 and 27 then 1 else 0 end)::double as neonatais,
           sum(case when idade_dias between 0 and 364 then 1 else 0 end)::double as infantis
    from {{ ref('stg_sim_do') }}
    group by ano
)
select ano, neonatais, infantis, neonatais / nullif(infantis, 0) as fracao
from t
where infantis >= 50 and (neonatais / nullif(infantis, 0) < 0.45 or neonatais / nullif(infantis, 0) > 0.90)
