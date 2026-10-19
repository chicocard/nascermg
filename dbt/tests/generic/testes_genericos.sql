{% test combinacao_unica(model, combination) %}
select {{ combination | join(', ') }}, count(*) as n
from {{ model }}
group by {{ combination | join(', ') }}
having count(*) > 1
{% endtest %}

{% test entre(model, column_name, minimo, maximo) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ minimo }} or {{ column_name }} > {{ maximo }})
{% endtest %}
