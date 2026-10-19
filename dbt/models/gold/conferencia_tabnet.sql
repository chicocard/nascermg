-- Totais para conferir manualmente com o TabNet (tabnet.datasus.gov.br) antes de qualquer análise.
-- Caminhos sugeridos no README. Diferenças pequenas (<1%) são esperadas por atualizações das bases;
-- diferenças grandes indicam arquivo faltando, ano preliminar ou erro de leitura.
with s as (
    select ano, 'SINASC: nascidos vivos (residência)' as item, count(*)::bigint as valor from {{ ref('stg_sinasc') }} group by ano
    union all
    select ano, 'SINASC: cesáreas', sum(case when parto = '2' then 1 else 0 end) from {{ ref('stg_sinasc') }} group by ano
    union all
    select ano, 'SIM: óbitos totais (residência)', count(*) from {{ ref('stg_sim_do') }} group by ano
    union all
    select ano, 'SIM: óbitos < 1 ano', count(*) from {{ ref('stg_sim_do') }} where idade_dias between 0 and 364 group by ano
    union all
    select ano, 'SIM: óbitos 0-6 dias', count(*) from {{ ref('stg_sim_do') }} where idade_dias between 0 and 6 group by ano
    union all
    select ano, 'SIM: óbitos 7-27 dias', count(*) from {{ ref('stg_sim_do') }} where idade_dias between 7 and 27 group by ano
    union all
    select ano, 'SIM: óbitos maternos declarados', sum(obito_materno_declarado) from {{ ref('stg_sim_do') }} group by ano
    union all
    select ano, 'SIH: AIHs (todas)', count(*) from {{ ref('stg_sih_rd') }} group by ano
    union all
    select ano, 'SIH: AIHs de parto', count(*) from {{ ref('fato_internacoes_obst') }} where tipo_episodio = 'parto' group by ano
    union all
    select ano, 'SIH: AIHs de parto cesáreo', sum(cesarea) from {{ ref('fato_internacoes_obst') }} where tipo_episodio = 'parto' group by ano
    union all
    select ano, 'SIH: AIHs de recém-nascidos', count(*) from {{ ref('fato_internacoes_obst') }} where tipo_episodio = 'recem_nascido' group by ano
    union all
    select ano, 'CNES: estabelecimentos na competência', count(*) from {{ ref('dim_estabelecimento') }} group by ano
    union all
    select ano, 'CNES: leitos obstétricos existentes', sum(leitos_obstetricos) from {{ ref('dim_estabelecimento') }} group by ano
    union all
    select ano, 'CNES: leitos de UTI neonatal existentes', sum(leitos_uti_neonatal) from {{ ref('dim_estabelecimento') }} group by ano
)
select ano, item, valor from s order by ano, item
