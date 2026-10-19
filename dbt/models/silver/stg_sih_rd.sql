-- Internações SUS (SIH, AIH reduzida). Uma linha por AIH.
with src as (
    select * from {{ source('bronze', 'sih_rd') }}
),
tipado as (
    select
        {{ nome_arquivo('filename') }}                       as arquivo,
        N_AIH                                                as n_aih,
        IDENT                                                as ident,            -- 1 normal, 5 longa permanência
        ANO_CMPT                                             as ano_cmpt,
        MES_CMPT                                             as mes_cmpt,
        ESPEC                                                as espec,
        MUNIC_RES                                            as munic_res6,
        MUNIC_MOV                                            as munic_mov6,
        CNES                                                 as cnes,
        PROC_REA                                             as proc_rea,
        DIAG_PRINC                                           as diag_princ,
        DIAG_SECUN                                           as diag_secun,
        try_strptime(DT_INTER, '%Y%m%d')::date               as dt_inter,
        try_strptime(DT_SAIDA, '%Y%m%d')::date               as dt_saida,
        try_strptime(NASC, '%Y%m%d')::date                   as dt_nasc,
        SEXO                                                 as sexo,
        try_cast(IDADE as integer)                           as idade,
        COD_IDADE                                            as cod_idade,        -- 2 dias, 3 meses, 4 anos
        try_cast(UTI_MES_TO as integer)                      as uti_dias,
        MARCA_UTI                                            as marca_uti,
        try_cast(DIAS_PERM as integer)                       as dias_perm,
        MORTE                                                as morte,
        COBRANCA                                             as cobranca,         -- motivo de saída/permanência
        CAR_INT                                              as car_int,          -- 1 eletivo, 2 urgência
        GESTRISCO                                            as gestrisco,
        try_cast(VAL_TOT as double)                          as val_tot,
        try_cast(VAL_UTI as double)                          as val_uti,
        RACA_COR                                             as raca_cor,
        INSTRU                                               as instru,
        CID_MORTE                                            as cid_morte,
        COMPLEX                                              as complex
    from src
)
select
    *,
    extract(year from dt_inter)::integer                     as ano,
    date_diff('day', dt_nasc, dt_inter)                      as idade_dias_na_internacao
from tipado
where dt_inter is not null
