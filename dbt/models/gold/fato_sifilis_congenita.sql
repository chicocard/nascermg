-- Casos confirmados de sífilis congênita por município de residência da mãe, com região e macrorregião.
select
    s.*,
    m.cod7                     as codmunres7,
    m.nome                     as municipio_res,
    m.regiao_saude_cod         as regiao_saude_res_cod,
    m.regiao_saude_nome        as regiao_saude_res,
    m.macro_cod                as macro_res_cod,
    m.macro_nome               as macro_res
from {{ ref('stg_sinan_sifc') }} s
left join {{ ref('dim_municipio') }} m on m.cod6 = s.codmunres6
where s.caso_confirmado = 1
