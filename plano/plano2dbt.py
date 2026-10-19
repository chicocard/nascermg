#!/usr/bin/env python3
"""
Lê o plano de análise exportado do painel "Modelos do Nascer" (JSON) e gera o modelo dbt
dbt/models/plano/plano_analise.sql com as variáveis declaradas, na unidade de análise escolhida.

Uso:
  python plano2dbt.py plano_analise.json [--saida /dbt/models/plano/plano_analise.sql] [--forcar]

Regras:
  - Se a verificação metodológica do painel contiver itens do tipo "bad", o gerador para
    (código de saída 2) e lista os problemas. --forcar gera mesmo assim, registrando o aviso no SQL.
  - Variáveis não mensuráveis ou ainda não implementadas são listadas como comentário no SQL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
MAPA_PADRAO = AQUI / "mapa_variaveis.yml"
SAIDA_PADRAO = Path("/dbt/models/plano/plano_analise.sql")

UNIDADES = {
    "individuo": None,
    "municipio": {"cod": "f.codmunres7", "nome": "f.municipio_res", "nivel_t": "municipio"},
    "regiao": {"cod": "f.regiao_saude_res_cod", "nome": "f.regiao_saude_res", "nivel_t": "regiao_saude"},
    "macro": {"cod": "f.macro_res_cod", "nome": "f.macro_res", "nivel_t": "macrorregiao"},
    "estab": {"cod": "f.cnes", "nome": "f.cnes", "nivel_t": None},
}
ESTRATOS = {
    "": None,
    "ctx": ("estrato_escolaridade", "f.escmae2010"),
    "raca": ("estrato_raca_cor", "f.racacormae"),
    "regiao": ("estrato_regiao_saude", "f.regiao_saude_res_cod"),
    "sus": None,  # SINASC não registra pagador: só no nível territorial (pct_partos_sus)
}


def ident(s: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    return s[:60]


def base_nome(vid: str) -> str:
    """Nome de coluna sem o prefixo de tipo do painel (v_, d_, n_)."""
    return ident(re.sub(r"^(v_|d_|n_)", "", vid))


def regiao_disponivel() -> bool:
    """True se a relação município -> região de saúde existir na camada bronze (mesma regra do dbt)."""
    try:
        proj = yaml.safe_load((AQUI.parent / "dbt" / "dbt_project.yml").read_text(encoding="utf-8")) or {}
        v = proj.get("vars", {})
        bronze = Path(os.environ.get("DATA_DIR", "/data")) / "bronze"
        if v.get("bronze") and Path(v["bronze"]).exists():
            bronze = Path(v["bronze"])
        tab = (v.get("territorio") or {}).get("tabela_rel", "rl_municip_regsaud")
        return (bronze / "territorio" / f"{tab}.parquet").exists()
    except Exception:  # noqa: BLE001
        return True


def usa_alias(alias: str, expr: str | None) -> bool:
    """True se a expressão referencia o alias (f., o., t., nl.)."""
    return bool(expr) and re.search(rf"(^|[^a-z0-9_]){alias}\.", expr) is not None


def agg_padrao(tipo: str, expr: str) -> str | None:
    if tipo == "binario":
        return f"100.0*avg({expr})"
    if tipo == "continuo":
        return f"median({expr})"
    if tipo == "contagem":
        return f"sum({expr})"
    return None  # categórico sem agg explícita: não agregável


def gerar(plano: dict, mapa: dict, forcar: bool) -> tuple[str, list[str], int]:
    avisos: list[str] = []
    ruins = [v for v in plano.get("verificacao", []) if v.get("tipo") == "bad"]
    if ruins and not forcar:
        for v in ruins:
            avisos.append(f"BLOQUEIO [{v.get('titulo')}]: {v.get('texto')}")
        return "", avisos, 2
    for v in plano.get("verificacao", []):
        if v.get("tipo") in ("bad", "warn"):
            avisos.append(f"{v.get('tipo').upper()} [{v.get('titulo')}]: {v.get('texto')}")

    unidade = plano.get("unidade_analise", "regiao")
    periodo = plano.get("periodo", "trienio_movel")
    estrato = plano.get("estratificacao") or ""
    if unidade in ("regiao", "macro") and not regiao_disponivel():
        avisos.append("Tabela município → região de saúde ainda não carregada (bronze/territorio): usando MUNICÍPIO como unidade. "
                      "Rode o serviço 'territorio' e confira bronze/territorio/_ftp_territorio.txt.")
        unidade = "municipio"
    if unidade not in UNIDADES:
        avisos.append(f"Unidade desconhecida '{unidade}': usando região de saúde.")
        unidade = "regiao"
    individual = unidade == "individuo"
    u = UNIDADES[unidade]

    desfecho = plano.get("desfecho", {}) or {}
    if individual and desfecho.get("id") and mapa.get(desfecho["id"], {}).get("ind") is None and mapa.get(desfecho["id"], {}).get("terr"):
        avisos.append(f"Desfecho '{desfecho.get('nome')}' só existe no nível territorial (não há vínculo DN↔DO nos microdados públicos): "
                      "no plano individual ele entra como taxa do município de residência, não como evento do recém-nascido.")
    variaveis = list(plano.get("variaveis", []))
    ids = [v["id"] for v in variaveis]
    if desfecho.get("id") and desfecho["id"] not in ids:
        variaveis.insert(0, {"id": desfecho["id"], "nome": desfecho.get("nome"), "papel": "desfecho"})

    colunas: list[str] = []          # expressões "expr as nome"
    group_extra: list[str] = []      # colunas territoriais (any_value) no agregado
    lacunas: list[str] = []
    nao_impl: list[str] = []
    notas: list[str] = []
    usa_o = False
    usa_t = False
    usa_nl = False

    for v in variaveis:
        vid = v["id"]
        m = mapa.get(vid)
        papel = v.get("papel", "")
        if not m:
            nao_impl.append(f"{vid} ({v.get('nome','')}) – sem entrada no mapa_variaveis.yml")
            continue
        if m.get("nao_mensuravel"):
            lacunas.append(f"{vid} ({v.get('nome','')}) – não mensurável nas bases administrativas [papel: {papel}]")
            continue
        if m.get("nota"):
            notas.append(f"{vid}: {m['nota']}")
        ind, terr, tipo = m.get("ind"), m.get("terr"), m.get("tipo")
        if individual:
            if ind:
                colunas.append(f"{ind} as {base_nome(vid)}")
                usa_o = usa_o or usa_alias("o", ind)
                usa_t = usa_t or usa_alias("t", ind)
            elif terr:
                colunas.append(f"{terr} as {base_nome(vid)}_territorial")
                usa_t = usa_t or usa_alias("t", terr)
                usa_nl = usa_nl or usa_alias("nl", terr)
            else:
                nao_impl.append(f"{vid} ({v.get('nome','')}) – não implementada nesta fase")
        else:
            feito = False
            if ind:
                agg = m.get("agg") or agg_padrao(tipo, ind)
                if agg:
                    nome = m.get("agg_nome") or (("pct_" if tipo == "binario" else "med_" if tipo == "continuo" else "") + base_nome(vid))
                    colunas.append(f"{agg} as {ident(nome)}")
                    usa_o = usa_o or usa_alias("o", ind) or usa_alias("o", agg)
                    feito = True
                elif not terr:
                    nao_impl.append(f"{vid} ({v.get('nome','')}) – categórica sem agregação definida; use-a como estratificação")
            if terr and u.get("nivel_t"):
                colunas.append(f"avg({terr}) as {base_nome(vid)}_territorial")  # média dos anos do período
                usa_t = usa_t or usa_alias("t", terr)
                usa_nl = usa_nl or usa_alias("nl", terr)
                feito = True
            elif terr and not u.get("nivel_t") and not feito:
                nao_impl.append(f"{vid} ({v.get('nome','')}) – indicador territorial não se aplica à unidade estabelecimento")
            if not feito and not ind and not terr:
                nao_impl.append(f"{vid} ({v.get('nome','')}) – não implementada nesta fase")

    # estratificação
    estrato_sql = None
    if estrato:
        e = ESTRATOS.get(estrato)
        if e:
            estrato_sql = e
        elif estrato == "sus":
            avisos.append("Estratificação SUS/não SUS não existe no nível individual (SINASC não registra pagador); use pct_partos_sus no territorial.")
        else:
            avisos.append(f"Estratificação '{estrato}' desconhecida; ignorada.")

    # período
    if individual or periodo in ("ano", "serie_anual"):
        periodo_expr, periodo_nome = "f.ano", "ano"
    else:
        periodo_expr, periodo_nome = "cast((f.ano // 3) * 3 as varchar) || '-' || cast((f.ano // 3) * 3 + 2 as varchar)", "trienio"

    gerado_em = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cab = [
        "-- GERADO AUTOMATICAMENTE por plano/plano2dbt.py em " + gerado_em + ". Não edite à mão: edite o plano no painel.",
        f"-- Plano: tema={plano.get('tema')} | desfecho={desfecho.get('id')} ({desfecho.get('nome')}) | unidade={plano.get('unidade_analise')} | período={periodo} | base territorial={plano.get('base_territorial')}",
        "-- Modelos conceituais: " + "; ".join(m.get("nome", m.get("id", "")) for m in plano.get("modelos_conceituais", [])),
    ]
    for a in avisos:
        cab.append("-- AVISO: " + a)
    for l in lacunas:
        cab.append("-- LACUNA DECLARADA: " + l)
    for n in nao_impl:
        cab.append("-- NÃO IMPLEMENTADA: " + n)
    for n in notas:
        cab.append("-- NOTA: " + n)
    cab.append("{{ config(materialized='table') }}")

    ctes = ["f as (select * from {{ ref('fato_nascimentos') }})"]
    if usa_o:
        ctes.append("o as (select numerodn, neonatal_precoce, idade_dias, causabas, obitoparto, lococor, codmunocor7, assistmed, tem_vinculo_dn, investigado "
                    "from {{ ref('fato_obitos_neonatais') }} where numerodn is not null qualify row_number() over (partition by numerodn order by dt_obito) = 1)")
    nivel_t = "municipio" if individual else (u.get("nivel_t") or "municipio")
    if usa_t:
        ctes.append(f"t as (select * from {{{{ ref('ind_territorio') }}}} where nivel = '{nivel_t}')")
    if usa_nl:
        ctes.append(f"nl as (select * from {{{{ ref('necessidade_leitos') }}}} where nivel = '{nivel_t}')")

    joins = []
    if usa_o:
        joins.append("left join o on o.numerodn = f.numerodn")
    chave = "f.codmunres7" if individual else u["cod"]
    if usa_t:
        joins.append(f"left join t on t.ano = f.ano and t.cod = {chave}")
    if usa_nl:
        joins.append(f"left join nl on nl.ano = f.ano and nl.cod = {chave}")

    if individual:
        sel = ["f.id_dn", "f.ano", "f.dt_nasc", "f.codmunres7", "f.municipio_res", "f.regiao_saude_res_cod", "f.codmunnasc7", "f.cnes"]
        if estrato_sql:
            sel.append(f"{estrato_sql[1]} as {estrato_sql[0]}")
        sel += colunas
        sql = "select\n    " + ",\n    ".join(sel) + "\nfrom f\n" + "\n".join(joins)
    else:
        grp = [f"{periodo_expr} as {periodo_nome}", f"{u['cod']} as cod", f"min({u['nome']}) as nome"]
        gb = [periodo_expr, u["cod"]]
        if estrato_sql:
            grp.append(f"{estrato_sql[1]} as {estrato_sql[0]}")
            gb.append(estrato_sql[1])
        sel = grp + ["count(*) as nascidos_vivos"] + colunas
        sql = ("select\n    " + ",\n    ".join(sel) + "\nfrom f\n" + "\n".join(joins)
               + f"\nwhere {u['cod']} is not null\ngroup by " + ", ".join(gb))

    corpo = "\n".join(cab) + "\nwith\n" + ",\n".join(ctes) + "\n" + sql + "\n"
    return corpo, avisos, 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plano")
    ap.add_argument("--saida", default=str(SAIDA_PADRAO))
    ap.add_argument("--mapa", default=str(MAPA_PADRAO))
    ap.add_argument("--forcar", action="store_true")
    args = ap.parse_args()

    plano = json.loads(Path(args.plano).read_text(encoding="utf-8"))
    mapa = yaml.safe_load(Path(args.mapa).read_text(encoding="utf-8")) or {}
    sql, avisos, rc = gerar(plano, mapa, args.forcar)
    for a in avisos:
        print(a)
    if rc:
        print("\nPlano bloqueado pela verificação metodológica. Corrija no painel ou rode com --forcar.")
        return rc
    saida = Path(args.saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(sql, encoding="utf-8")
    print(f"\nModelo gerado: {saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
