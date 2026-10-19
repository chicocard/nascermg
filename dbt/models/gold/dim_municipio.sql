-- Dimensão de municípios: código IBGE de 7 dígitos (o 6 dígitos do DATASUS é o prefixo), nome, UF e,
-- quando a base territorial do DATASUS estiver carregada, região de saúde, macrorregião e coordenadas da sede.
{% set t = var('territorio') %}
{% set rel_ok = arquivo_existe(var('bronze') ~ '/territorio/' ~ t.tabela_rel ~ '.parquet') %}
{% set reg_ok = arquivo_existe(var('bronze') ~ '/territorio/' ~ t.tabela_reg ~ '.parquet') %}
{% set mac_ok = arquivo_existe(var('bronze') ~ '/territorio/rl_municip_macsaud.parquet') and arquivo_existe(var('bronze') ~ '/territorio/tb_macsaud.parquet') %}
{% set mun_ok = arquivo_existe(var('bronze') ~ '/territorio/tb_municip.parquet') %}

with ibge as (
    select
        cod7, cod6, nome, uf_sigla, uf_cod, uf_nome,
        microrregiao, mesorregiao, regiao_imediata, regiao_intermediaria, regiao_nome
    from {{ source('bronze', 'municipios_ibge') }}
)
{% if rel_ok %}
, rel as (
    select lpad(cast({{ t.col_mun }} as varchar), 6, '0') as cod6, cast({{ t.col_reg }} as varchar) as regiao_saude_cod
    from read_parquet('{{ var("bronze") }}/territorio/{{ t.tabela_rel }}.parquet')
    qualify row_number() over (partition by cod6 order by regiao_saude_cod) = 1
)
{% endif %}
{% if reg_ok %}
, reg as (
    -- versões recentes da base trazem o nome com sufixo " - UF"; retirado aqui
    select cast({{ t.col_reg }} as varchar) as regiao_saude_cod,
           regexp_replace(trim(cast({{ t.col_reg_nome }} as varchar)), '\s*-\s*[A-Z]{2}$', '') as regiao_saude_nome
    from read_parquet('{{ var("bronze") }}/territorio/{{ t.tabela_reg }}.parquet')
    qualify row_number() over (partition by regiao_saude_cod order by regiao_saude_nome) = 1
)
{% endif %}
{% if mac_ok %}
, mac as (
    -- versões recentes trazem ds_nome em caixa alta ("TRIANGULO DO NORTE") e a forma legível em ds_abrev
    select lpad(cast(r.co_municip as varchar), 6, '0') as cod6, cast(r.co_macsaud as varchar) as macro_cod,
           case when m.ds_nome = upper(m.ds_nome) and coalesce(m.ds_abrev, '') <> '' then m.ds_abrev else m.ds_nome end as macro_nome
    from read_parquet('{{ var("bronze") }}/territorio/rl_municip_macsaud.parquet') r
    left join read_parquet('{{ var("bronze") }}/territorio/tb_macsaud.parquet') m on cast(m.co_macsaud as varchar) = cast(r.co_macsaud as varchar)
    qualify row_number() over (partition by cod6 order by macro_cod) = 1
)
{% endif %}
{% if mun_ok %}
, sede as (
    select lpad(cast(co_municip as varchar), 6, '0') as cod6,
           try_cast(replace(nu_latitud, ',', '.') as double) as lat,
           try_cast(replace(nu_longit, ',', '.') as double) as lon
    from read_parquet('{{ var("bronze") }}/territorio/tb_municip.parquet')
    qualify row_number() over (partition by cod6 order by lat) = 1
)
{% endif %}
select
    i.cod7,
    i.cod6,
    i.nome,
    i.uf_sigla,
    i.uf_cod,
    i.uf_nome,
    i.microrregiao,
    i.mesorregiao,
    i.regiao_imediata,
    i.regiao_intermediaria,
    i.regiao_nome,
    {% if rel_ok %} rel.regiao_saude_cod {% else %} null::varchar {% endif %} as regiao_saude_cod,
    {% if rel_ok and reg_ok %} reg.regiao_saude_nome {% else %} null::varchar {% endif %} as regiao_saude_nome,
    {% if mac_ok %} mac.macro_cod {% else %} null::varchar {% endif %} as macro_cod,
    {% if mac_ok %} mac.macro_nome {% else %} null::varchar {% endif %} as macro_nome,
    {% if mun_ok %} sede.lat {% else %} null::double {% endif %} as lat,
    {% if mun_ok %} sede.lon {% else %} null::double {% endif %} as lon
from ibge i
{% if rel_ok %} left join rel on rel.cod6 = i.cod6 {% endif %}
{% if rel_ok and reg_ok %} left join reg on reg.regiao_saude_cod = rel.regiao_saude_cod {% endif %}
{% if mac_ok %} left join mac on mac.cod6 = i.cod6 {% endif %}
{% if mun_ok %} left join sede on sede.cod6 = i.cod6 {% endif %}
