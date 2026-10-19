-- Matriz origem-destino dos nascimentos: município de residência -> município/estabelecimento do parto.
-- Base do mapa de fluxos. SINASC cobre SUS e não SUS.
select
    ano,
    codmunres7,
    municipio_res,
    regiao_saude_res_cod,
    codmunnasc7,
    municipio_nasc,
    regiao_saude_nasc_cod,
    cnes,
    count(*)                                                  as nascidos_vivos,
    sum(cesarea)                                              as cesareas,
    sum(prematuro)                                            as prematuros,
    sum(baixo_peso)                                           as baixo_peso,
    sum(parto_fora_municipio)                                 as fora_municipio,
    sum(parto_fora_regiao)                                    as fora_regiao,
    max(est_tem_uti_neonatal)                                 as destino_tem_utin
from {{ ref('fato_nascimentos') }}
where codmunres7 is not null
group by all
