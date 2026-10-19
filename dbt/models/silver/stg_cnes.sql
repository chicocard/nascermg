-- Cadastro de estabelecimentos (CNES-ST), uma linha por estabelecimento e competência carregada.
with src as (
    select * from {{ source('bronze', 'cnes_st') }}
)
select
    {{ nome_arquivo('filename') }}                           as arquivo,
    CNES                                                     as cnes,
    CODUFMUN                                                 as codufmun6,
    COMPETEN                                                 as competen,
    substr(COMPETEN, 1, 4)::integer                          as ano,
    TP_UNID                                                  as tp_unid,
    NAT_JUR                                                  as nat_jur,
    VINC_SUS                                                 as vinc_sus,
    TPGESTAO                                                 as tpgestao
from src
