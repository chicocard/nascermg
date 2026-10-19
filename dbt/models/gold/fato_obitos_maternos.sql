-- Óbitos maternos declarados (sem correção por investigação) e óbitos de mulheres de 10 a 49 anos
-- com registro de gravidez/puerpério, para análise de near miss e de subnotificação.
with o as (
    select * from {{ ref('stg_sim_do') }}
    where tipobito = '2' and sexo = '2' and idade_anos between 10 and 49
),
res as (select cod6, cod7, nome, regiao_saude_cod, regiao_saude_nome, macro_cod, macro_nome from {{ ref('dim_municipio') }})
select
    o.contador                                                as id_do,
    o.ano,
    o.dt_obito,
    o.idade_anos,
    o.codmunres6,
    res.cod7                                                  as codmunres7,
    res.nome                                                  as municipio_res,
    res.regiao_saude_cod                                      as regiao_saude_res_cod,
    res.regiao_saude_nome                                     as regiao_saude_res,
    res.macro_cod                                             as macro_res_cod,
    res.macro_nome                                            as macro_res,
    o.codmunocor6,
    o.lococor,
    o.cnes,
    o.causabas,
    o.obito_materno_declarado,
    case
        when o.obito_materno_declarado = 1 and (o.causabas between 'O10' and 'O169') then 'hipertensiva'
        when o.obito_materno_declarado = 1 and (o.causabas between 'O44' and 'O469' or o.causabas between 'O67' and 'O679' or o.causabas between 'O72' and 'O729') then 'hemorragica'
        when o.obito_materno_declarado = 1 and (o.causabas between 'O85' and 'O869') then 'infecciosa puerperal'
        when o.obito_materno_declarado = 1 and (o.causabas between 'O00' and 'O089') then 'aborto'
        when o.obito_materno_declarado = 1 and (o.causabas between 'O98' and 'O999') then 'indireta (O98-O99)'
        when o.obito_materno_declarado = 1 then 'outras diretas/nao classificadas'
        else null
    end                                                       as grupo_causa_materna,
    o.obitograv,
    o.obitopuerp,
    (o.obitograv = '1' or o.obitopuerp in ('1','2'))::integer  as gravidez_ou_puerperio_declarado,
    o.racacor,
    o.esc2010,
    o.tppos                                                   as investigado
from o
left join res on res.cod6 = o.codmunres6
where o.obito_materno_declarado = 1 or o.obitograv = '1' or o.obitopuerp in ('1','2')
