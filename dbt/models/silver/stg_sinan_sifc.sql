-- Sífilis congênita (SINAN, arquivo nacional SIFCBRaa) filtrada para a UF do piloto pelo município de RESIDÊNCIA.
-- Dicionário (DIC_DADOS_NET - Sífilis congênita, SINAN): ANT_PRE_NA pré-natal (1 sim, 2 não, 9 ign);
-- ANTSIFIL_N momento do diagnóstico materno (1 pré-natal, 2 parto/curetagem, 3 após o parto, 4 não realizado, 9 ign);
-- TRA_ESQUEM tratamento materno (1 adequado, 2 inadequado, 3 não realizado, 9 ign); ANT_TRATAD parceiro tratado (1 sim, 2 não, 9);
-- EVOLUCAO (1 vivo, 2 óbito por SC, 3 óbito outras causas, 4 aborto, 5 natimorto, 9 ign);
-- EVO_DIAG_N diagnóstico final (1 SC recente, 2 SC tardia, 3 aborto, 4 natimorto, 5 descartado).
-- Datas do SINAN chegam como AAAA-MM-DD (campo de data) ou DDMMAAAA (texto): as duas formas são aceitas.
{% if arquivo_existe(var('bronze') ~ '/sinan_sifc/*.parquet') %}
{% set c = cols_fonte('sinan_sifc') %}
with src as (
    select * from {{ source('bronze', 'sinan_sifc') }}
)
select
    {{ nome_arquivo('filename') }}                                   as arquivo,
    try_cast(NU_ANO as integer)                                      as ano_notificacao,
    {{ dt_sinan('DT_NOTIFIC') }}                                     as dt_notificacao,
    {{ dt_sinan('DT_DIAG') }}                                        as dt_diag,
    coalesce(extract(year from {{ dt_sinan('DT_DIAG') }}), try_cast(NU_ANO as integer))::integer as ano,
    {{ dt_sinan('DT_NASC') }}                                        as dt_nasc,
    ID_MN_RESI                                                       as codmunres6,
    SG_UF                                                            as uf_res,
    ID_MUNICIP                                                       as codmunnotif6,
    NU_IDADE_N                                                       as idade_cod,
    CS_SEXO                                                          as sexo,
    CS_RACA                                                          as racacor,
    try_cast({{ col(c, 'ANT_IDADE') }} as integer)                   as idademae,
    {{ col(c, 'ESCOLMAE') }}                                         as escmae,
    {{ col(c, 'ANT_PRE_NA') }}                                       as prenatal,
    {{ col(c, 'ANTSIFIL_N') }}                                       as momento_diag_materno,
    {{ col(c, 'TRA_ESQUEM') }}                                       as tratamento_materno,
    {{ col(c, 'ANT_TRATAD') }}                                       as parceiro_tratado,
    {{ col(c, 'LAB_PARTO') }}                                        as teste_nao_trep_parto,
    EVOLUCAO                                                         as evolucao,
    {{ col(c, 'EVO_DIAG_N') }}                                       as diag_final,
    ({{ col(c, 'EVO_DIAG_N') }} is distinct from '5')::integer       as caso_confirmado,
    (EVOLUCAO = '2')::integer                                        as obito_por_sc,
    (EVOLUCAO in ('4','5'))::integer                                 as aborto_ou_natimorto,
    ({{ col(c, 'ANT_PRE_NA') }} = '2')::integer                      as sem_prenatal,
    ({{ col(c, 'ANTSIFIL_N') }} in ('2','3','4'))::integer           as diag_materno_tardio_ou_nao,
    ({{ col(c, 'TRA_ESQUEM') }} in ('2','3'))::integer               as tratamento_inadequado_ou_nao,
    ({{ col(c, 'ANT_TRATAD') }} = '1')::integer                      as parceiro_tratado_sim
from src
where substr(ID_MN_RESI, 1, 2) = '{{ var("uf_cod") }}'
{% else %}
-- Arquivos do SINAN ausentes na camada bronze: modelo vazio com o mesmo esquema.
select
    null::varchar as arquivo, null::integer as ano_notificacao, null::date as dt_notificacao, null::date as dt_diag, null::integer as ano,
    null::date as dt_nasc, null::varchar as codmunres6, null::varchar as uf_res, null::varchar as codmunnotif6, null::varchar as idade_cod,
    null::varchar as sexo, null::varchar as racacor, null::integer as idademae, null::varchar as escmae, null::varchar as prenatal,
    null::varchar as momento_diag_materno, null::varchar as tratamento_materno, null::varchar as parceiro_tratado,
    null::varchar as teste_nao_trep_parto, null::varchar as evolucao, null::varchar as diag_final, null::integer as caso_confirmado,
    null::integer as obito_por_sc, null::integer as aborto_ou_natimorto, null::integer as sem_prenatal,
    null::integer as diag_materno_tardio_ou_nao, null::integer as tratamento_inadequado_ou_nao, null::integer as parceiro_tratado_sim
where false
{% endif %}
