-- Em todo ano de competência com volume relevante (>= 1.000 AIHs) deve haver AIHs de parto;
-- se não houver, os códigos SIGTAP do seed sigtap_partos estão errados ou o arquivo veio vazio.
with anos as (
    select try_cast(ano_cmpt as integer) as ano, count(*) as aihs
    from {{ ref('stg_sih_rd') }} group by 1 having count(*) >= 1000
),
partos as (
    select try_cast(ano_cmpt as integer) as ano, count(*) as n
    from {{ ref('fato_internacoes_obst') }} where tipo_episodio = 'parto' group by 1
)
select a.ano, a.aihs, coalesce(p.n, 0) as partos
from anos a left join partos p using (ano)
where coalesce(p.n, 0) = 0
