-- A taxa de cesárea estadual no Brasil recente fica entre ~40% e ~70%. Fora disso, suspeite do campo PARTO.
select ano, pct
from (
    select ano, 100.0 * sum(case when parto = '2' then 1 else 0 end) / count(*) as pct
    from {{ ref('stg_sinasc') }} group by ano
)
where pct < 30 or pct > 80
