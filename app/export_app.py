#!/usr/bin/env python3
"""
Exporta do warehouse (DuckDB) os arquivos JSON que o app lê, em app/public/data/:
  meta.json          totais estaduais por ano, versão das bases
  territorios.json   indicadores por município, região de saúde e macrorregião (por ano) + necessidade de leitos
  fluxos.json        principais fluxos residência -> município do parto (por ano), com coordenadas
  hospitais.json     estabelecimentos com leitos obstétricos ou UTI neonatal (por ano)
  cenas.json         roteiro do modo exposição (autoplay), com os números reais já inseridos
  clusters.json      tipologia dos municípios (perfil dos grupos), se o passo "analise" tiver rodado
  sifilis.json       sífilis congênita × outras taxas (correlações, por tipo, regressão, pontos), idem
  espacial.json      áreas quentes (BYM2): metadados e listas por nível/desfecho; valores por território em territorios.json
  malha_mg.geojson   malha municipal do IBGE (baixada uma vez; se falhar, o app usa pontos nas sedes)

Uso: python export_app.py  (variáveis: DUCKDB_PATH, DATA_DIR, UF, UF_COD)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import duckdb

DB = os.environ.get("DUCKDB_PATH", "/data/warehouse/nascer.duckdb")
OUT = Path(os.environ.get("APP_DATA", "/app/public/data"))
UF = os.environ.get("UF", "MG").upper()
UF_CODIGOS = {"RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
              "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27", "SE": "28", "BA": "29",
              "MG": "31", "ES": "32", "RJ": "33", "SP": "35", "PR": "41", "SC": "42", "RS": "43",
              "MS": "50", "MT": "51", "GO": "52", "DF": "53"}
UF_COD = os.environ.get("UF_COD") or UF_CODIGOS.get(UF, "31")
MALHA_URL = (f"https://servicodados.ibge.gov.br/api/v3/malhas/estados/{UF_COD}"
             "?formato=application/vnd.geo+json&intrarregiao=municipio&qualidade=minima")


def log(m: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


def fmt(n: float | None, d: int = 1) -> str:
    """Número no formato brasileiro (1.234,5)."""
    if n is None:
        return "—"
    s = f"{n:,.{d}f}" if d else f"{int(round(n)):,}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _py(v):
    """Converte tipos do DuckDB (Decimal, int128, numpy) para tipos JSON."""
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, int):
        return int(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, (_py(v) for v in r))) for r in cur.fetchall()]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(DB, read_only=True)

    anos = [int(r["ano"]) for r in rows(con, "select distinct ano from main_gold.ind_territorio where nivel='municipio' order by ano")]
    ano_ref = anos[-1]
    log(f"anos no warehouse: {anos}; ano de referência: {ano_ref}")

    # ---------- territórios (indicadores + necessidade) ----------
    ind = rows(con, f"""
        select i.ano, i.nivel, i.cod, i.nome, i.nascidos_vivos, i.pct_cesarea, i.pct_cesarea_sem_tp,
               i.pct_parto_fora_municipio, i.pct_parto_fora_regiao, i.pct_prematuro, i.pct_baixo_peso,
               i.pct_prenatal_adequado, i.pct_partos_hospital_com_utin, i.tx_mort_neonatal, i.tx_mort_neonatal_precoce,
               i.obitos_neonatais, i.obitos_neonatais_precoces, i.rmm_declarada, i.obitos_maternos_declarados,
               i.leitos_obst_por_1000nv, i.leitos_utin_por_1000nv, i.pct_partos_sus, i.near_miss_por_1000nv,
               i.rn_uti_por_1000nv, i.area_pequena,
               i.casos_sifc, i.tx_sifc_por_1000nv, i.pct_sifc_sem_prenatal, i.pct_sifc_trat_inadequado, i.sifc_disponivel,
               n.leitos_obst_exist, n.leitos_obst_nec_rc, n.saldo_obst_rc, n.leitos_utin_exist, n.leitos_utin_nec_rc,
               n.leitos_utin_sus_nec_eq3, n.saldo_utin_rc, n.fnr, n.pir, n.tmp_obst_obs, n.p_uti_neonat_obs,
               n.gestantes_estimadas, n.hospitais_com_obstetricia, n.hospitais_com_utin
        from main_gold.ind_territorio i
        left join main_gold.necessidade_leitos n using (ano, nivel, cod)
        order by i.nivel, i.cod, i.ano""")
    dim = {r["cod7"]: r for r in rows(con, "select cod7, nome, lat, lon, regiao_saude_cod, regiao_saude_nome, macro_cod, macro_nome from main_gold.dim_municipio where uf_cod = '" + UF_COD + "'")}
    terr: dict[str, dict] = {}
    for r in ind:
        k = f"{r['nivel']}:{r['cod']}"
        t = terr.setdefault(k, {"nivel": r["nivel"], "cod": r["cod"], "nome": r["nome"], "anos": {}})
        if r["nivel"] == "municipio" and r["cod"] in dim:
            d = dim[r["cod"]]
            t.update({"lat": d["lat"], "lon": d["lon"], "reg": d["regiao_saude_cod"], "reg_nome": d["regiao_saude_nome"],
                      "macro": d["macro_cod"], "macro_nome": d["macro_nome"]})
        t["anos"][str(int(r["ano"]))] = {k2: v for k2, v in r.items() if k2 not in ("nivel", "cod", "nome", "ano")}
    # ---------- tipologia (clusters), se o passo "cluster" já rodou ----------
    tem_cluster = bool(rows(con, "select 1 as x from information_schema.tables where table_schema = 'main_analise' and table_name = 'cluster_municipios'"))
    clusters = None
    if tem_cluster:
        cm = rows(con, "select cod7, cluster, tipo, rotulo, estrato, dist_utin_km, dist_cesarea_km, dist_media_km, pct_evasao_partos, pct_evasao_regiao, pct_invasao_cesareas, tmnp_eb from main_analise.cluster_municipios")
        perfil = rows(con, "select * from main_analise.cluster_perfil order by cluster")
        cmeta = json.loads(rows(con, "select meta from main_analise.cluster_meta")[0]["meta"])
        for r in cm:
            t = terr.get(f"municipio:{r['cod7']}")
            if t:
                t["cluster"] = int(r["cluster"]); t["tipo"] = r["tipo"]
                t["tipologia"] = {k2: r[k2] for k2 in ("dist_utin_km", "dist_cesarea_km", "dist_media_km", "pct_evasao_partos", "pct_evasao_regiao", "pct_invasao_cesareas", "tmnp_eb")}
        clusters = {"meta": cmeta, "perfil": perfil}
        (OUT / "clusters.json").write_text(json.dumps(clusters, ensure_ascii=False), encoding="utf-8")
        log(f"clusters.json: {len(perfil)} tipos de município ({cmeta.get('modo')}, estratos {list((cmeta.get('estratos') or {}).keys())})")
    else:
        log("tipologia não encontrada (rode o passo 'analise' para incluí-la no app)")
        if (OUT / "clusters.json").exists():
            (OUT / "clusters.json").unlink()
    # ---------- sífilis congênita × outras taxas (passo "analise") ----------
    sifilis = None
    if rows(con, "select 1 as x from information_schema.tables where table_schema = 'main_analise' and table_name = 'sifc_correlacoes'"):
        smeta = json.loads(rows(con, "select meta from main_analise.sifc_meta")[0]["meta"])
        pontos = rows(con, f"""select cod, nome, nv, casos_sifc, tx_sifc, {", ".join(smeta["variaveis"].keys())}
                               {", estrato, tipo" if tem_cluster else ""}
                               from main_analise.sifc_municipios where nv >= {smeta['nv_minimo_municipio']}""")
        sifilis = {"meta": smeta, "correlacoes": rows(con, "select * from main_analise.sifc_correlacoes"), "pontos": pontos,
                   "por_tipo": rows(con, "select * from main_analise.sifc_por_tipo order by cluster") if rows(con, "select 1 as x from information_schema.tables where table_schema='main_analise' and table_name='sifc_por_tipo'") else [],
                   "regressao": rows(con, "select * from main_analise.sifc_regressao") if rows(con, "select 1 as x from information_schema.tables where table_schema='main_analise' and table_name='sifc_regressao'") else []}
        (OUT / "sifilis.json").write_text(json.dumps(sifilis, ensure_ascii=False), encoding="utf-8")
        log(f"sifilis.json: {len(pontos)} municípios na correlação; {len(sifilis['correlacoes'])} correlações; {len(sifilis['por_tipo'])} tipos")
    else:
        log("análise de sífilis congênita não encontrada (rode o passo 'analise')")
        if (OUT / "sifilis.json").exists():
            (OUT / "sifilis.json").unlink()
    # ---------- análise espacial bayesiana (áreas quentes), passo "analise" ----------
    espacial = None
    CLASSES_ESP = {"fria": 1, "indefinida": 2, "quente": 3, "quente forte": 4}
    if rows(con, "select 1 as x from information_schema.tables where table_schema = 'main_analise' and table_name = 'espacial'"):
        emeta = json.loads(rows(con, "select meta from main_analise.espacial_meta")[0]["meta"])
        reg_e = rows(con, "select nivel, desfecho, cod, nome, nv, obs, esp, smr, smr_inf, smr_sup, rr, rr_inf, rr_sup, prob, classe, lisa, lisa_p, taxa_por_1000 from main_analise.espacial")
        for r in reg_e:
            t = terr.get(f"{r['nivel']}:{r['cod']}")
            if t:
                t.setdefault("esp", {})[r["desfecho"]] = {"rr": r["rr"], "inf": r["rr_inf"], "sup": r["rr_sup"], "prob": r["prob"], "classe": CLASSES_ESP.get(r["classe"], 2),
                                                           "lisa": r["lisa"], "obs": r["obs"], "esp": r["esp"], "smr": r["smr"], "taxa": r["taxa_por_1000"]}
        quentes = {}
        for r in reg_e:
            if r["classe"] in ("quente", "quente forte"):
                quentes.setdefault(r["nivel"], {}).setdefault(r["desfecho"], []).append({"cod": r["cod"], "nome": r["nome"], "rr": r["rr"], "inf": r["rr_inf"], "sup": r["rr_sup"], "prob": r["prob"], "obs": r["obs"], "esp": r["esp"], "classe": r["classe"], "lisa": r["lisa"]})
        for nv_ in quentes.values():
            for lst in nv_.values():
                lst.sort(key=lambda x: (-x["prob"], -x["rr"]))
        espacial = {"meta": emeta, "quentes": quentes}
        (OUT / "espacial.json").write_text(json.dumps(espacial, ensure_ascii=False), encoding="utf-8")
        log(f"espacial.json: {len(reg_e)} registros; desfechos {list((emeta.get('niveis') or {}).get('municipio', {}).keys())}")
    else:
        log("análise espacial não encontrada (rode o passo 'analise')")
        if (OUT / "espacial.json").exists():
            (OUT / "espacial.json").unlink()
    (OUT / "territorios.json").write_text(json.dumps(list(terr.values()), ensure_ascii=False), encoding="utf-8")
    log(f"territorios.json: {len(terr)} territórios")

    # ---------- fluxos residência -> município do parto ----------
    fl = rows(con, f"""
        select o.ano, o.codmunres7 as origem, o.codmunnasc7 as destino, min(o.municipio_res) as origem_nome,
               min(o.municipio_nasc) as destino_nome, sum(o.nascidos_vivos) as n, sum(o.cesareas) as cesareas,
               max(o.destino_tem_utin) as destino_utin
        from main_gold.od_partos o
        where o.codmunnasc7 is not null and o.codmunres7 <> o.codmunnasc7
        group by o.ano, o.codmunres7, o.codmunnasc7
        having sum(o.nascidos_vivos) >= 10
        order by o.ano, n desc""")
    fluxos = []
    for r in fl:
        a, b = dim.get(r["origem"]), dim.get(r["destino"])
        if not a or not b or a["lat"] is None or b["lat"] is None:
            continue
        fluxos.append({"ano": int(r["ano"]), "o": r["origem"], "d": r["destino"], "on": r["origem_nome"], "dn": r["destino_nome"],
                       "olat": a["lat"], "olon": a["lon"], "dlat": b["lat"], "dlon": b["lon"], "n": int(r["n"]),
                       "ces": int(r["cesareas"]), "utin": int(r["destino_utin"] or 0)})
    (OUT / "fluxos.json").write_text(json.dumps(fluxos, ensure_ascii=False), encoding="utf-8")
    log(f"fluxos.json: {len(fluxos)} fluxos (>= 10 nascimentos, entre municípios diferentes)")

    # ---------- hospitais ----------
    hosp = rows(con, """
        select cnes, ano, codmun7, municipio, regiao_saude_cod, macro_cod, lat_municipio as lat, lon_municipio as lon,
               nat_jur, leitos_obstetricos, leitos_obstetricos_sus, leitos_uti_neonatal, leitos_uci_neonatal, leitos_uti_adulto
        from main_gold.dim_estabelecimento
        where (leitos_obstetricos > 0 or leitos_uti_neonatal > 0) and lat_municipio is not null""")
    partos_h = {(r["cnes"], r["ano"]): r for r in rows(con, """
        select cnes, ano, count(*) as nascimentos, sum(cesarea) as cesareas, sum(parto_fora_municipio) as de_fora
        from main_gold.fato_nascimentos where cnes is not null group by cnes, ano""")}
    for h in hosp:
        p = partos_h.get((h["cnes"], h["ano"]), {})
        h.update({"nascimentos": int(p.get("nascimentos", 0) or 0), "cesareas": int(p.get("cesareas", 0) or 0),
                  "de_fora": int(p.get("de_fora", 0) or 0)})
    for h in hosp:
        h["ano"] = int(h["ano"])
    (OUT / "hospitais.json").write_text(json.dumps(hosp, ensure_ascii=False), encoding="utf-8")
    log(f"hospitais.json: {len(hosp)} estabelecimento-ano")

    # ---------- totais estaduais e cenas ----------
    tot = {str(int(r["ano"])): r for r in rows(con, """
        select ano, sum(nascidos_vivos) nv, round(100.0*sum(cesareas)/sum(nascidos_vivos),1) pct_ces,
               round(100.0*sum(cesareas_sem_tp)/sum(nascidos_vivos),1) pct_ces_sem_tp,
               round(100.0*sum(partos_fora_municipio)/sum(nascidos_vivos),1) pct_fora,
               round(100.0*sum(partos_fora_regiao)/sum(nascidos_vivos),1) pct_fora_reg,
               round(1000.0*sum(obitos_neonatais)/sum(nascidos_vivos),2) tmn,
               round(1000.0*sum(obitos_neonatais_precoces)/sum(nascidos_vivos),2) tmnp,
               sum(obitos_neonatais) obitos_neo, sum(obitos_neonatais_precoces) obitos_neo_prec,
               sum(obitos_maternos_declarados) obitos_mat, round(100000.0*sum(obitos_maternos_declarados)/sum(nascidos_vivos),1) rmm,
               round(100.0*sum(prenatal_adequado)/sum(nascidos_vivos),1) pct_pn,
               round(100.0*sum(partos_sus)/sum(nascidos_vivos),1) pct_sus,
               sum(leitos_uti_neonatal) utin, sum(leitos_obstetricos) leitos_obst, sum(rn_em_uti) rn_uti
        from main_gold.ind_territorio where nivel='municipio' group by ano order by ano""")}
    T = tot[str(ano_ref)]
    mac = [r for r in ind if r["nivel"] == "macrorregiao" and r["ano"] == ano_ref]
    reg = [r for r in ind if r["nivel"] == "regiao_saude" and r["ano"] == ano_ref and (r["nascidos_vivos"] or 0) >= 500]
    mun = [r for r in ind if r["nivel"] == "municipio" and r["ano"] == ano_ref and (r["nascidos_vivos"] or 0) >= 500]
    nivel_cmp, cmp_rows = ("macrorregiao", mac) if len(mac) >= 3 else (("regiao_saude", reg) if len(reg) >= 3 else ("municipio", mun))
    nome_nivel = {"macrorregiao": "macrorregião", "regiao_saude": "região de saúde", "municipio": "município"}[nivel_cmp]

    def extremos(chave, maior=True):
        xs = [r for r in cmp_rows if r.get(chave) is not None]
        xs.sort(key=lambda r: r[chave], reverse=maior)
        return xs[0], xs[-1]

    top_fl = [f for f in fluxos if f["ano"] == ano_ref][:5]
    def_utin = sorted([r for r in cmp_rows if r.get("saldo_utin_rc") is not None], key=lambda r: r["saldo_utin_rc"])
    ces_hi, ces_lo = extremos("pct_cesarea")
    mni_hi, mni_lo = extremos("tx_mort_neonatal_precoce")
    fora_hi, fora_lo = extremos("pct_parto_fora_municipio")
    pn_hi, pn_lo = extremos("pct_prenatal_adequado")

    cenas = [
        {"id": "fluxos", "titulo": "Onde nascem as crianças de " + UF, "variavel": "pct_parto_fora_municipio", "nivel": "municipio",
         "camadas": ["coropleta", "fluxos"], "duracao": 22,
         "texto": f"Em {ano_ref}, {fmt(T['nv'],0)} crianças nasceram de mães residentes em {UF}. {fmt(T['pct_fora'])}% nasceram fora do município de residência da mãe e {fmt(T['pct_fora_reg'])}% fora da própria região de saúde. As linhas mostram os deslocamentos de ao menos dez nascimentos no ano; os maiores: "
                  + "; ".join(f"{f['on']} → {f['dn']} ({fmt(f['n'],0)})" for f in top_fl) + ".",
         "modelo": "Três Demoras (Thaddeus e Maine, 1994): a segunda demora é chegar ao serviço. A distância aqui é entre sedes municipais; o tempo real de viagem é a Fase 1 seguinte.",
         "fonte": "SINASC (residência da mãe e estabelecimento do parto); coordenadas das sedes municipais (base territorial do DATASUS)."},
        {"id": "cesarea", "titulo": "Cesárea: duas Minas", "variavel": "pct_cesarea", "nivel": nivel_cmp, "camadas": ["coropleta"], "duracao": 18,
         "texto": f"{fmt(T['pct_ces'])}% dos partos de {ano_ref} foram cesáreas; em {fmt(T['pct_ces_sem_tp'])}% de todos os nascimentos a cesárea ocorreu antes de qualquer trabalho de parto. Por {nome_nivel}, a taxa vai de {fmt(ces_lo['pct_cesarea'])}% ({ces_lo['nome']}) a {fmt(ces_hi['pct_cesarea'])}% ({ces_hi['nome']}).",
         "modelo": "Classificação de Robson (OMS, 2015): a comparação válida é dentro de cada grupo obstétrico; a taxa bruta mistura perfil de risco e prática assistencial. Transição obstétrica (Souza et al., 2014): excesso de intervenção e falta de acesso coexistem.",
         "fonte": "SINASC: PARTO e STCESPARTO."},
        {"id": "mortalidade", "titulo": "Mortalidade neonatal precoce", "variavel": "tx_mort_neonatal_precoce", "nivel": nivel_cmp, "camadas": ["coropleta"], "duracao": 18,
         "texto": f"{fmt(T['obitos_neo_prec'],0)} recém-nascidos morreram nos primeiros seis dias de vida em {ano_ref}: {fmt(T['tmnp'],2)} por mil nascidos vivos ({fmt(T['tmn'],2)} até 27 dias). Por {nome_nivel}, de {fmt(mni_lo['tx_mort_neonatal_precoce'],2)} ({mni_lo['nome']}) a {fmt(mni_hi['tx_mort_neonatal_precoce'],2)} ({mni_hi['nome']}). Taxas de áreas pequenas são instáveis: o mapa usa a residência da mãe, nunca o local do óbito.",
         "modelo": "Modelo hierárquico (Victora et al., 1997): o efeito da assistência sobre o óbito passa pelo peso ao nascer e pela prematuridade; ajustar por eles apaga o que se quer medir. Lista de causas evitáveis (Malta et al., 2007).",
         "fonte": "SIM (óbitos de 0 a 6 dias, por residência) e SINASC (denominador)."},
        {"id": "utin", "titulo": "UTI neonatal: oferta e necessidade", "variavel": "saldo_utin_rc", "nivel": nivel_cmp, "camadas": ["coropleta", "hospitais"], "duracao": 20,
         "texto": f"{UF} tinha {fmt(T['utin'],0)} leitos de UTI neonatal em dezembro de {ano_ref}. O parâmetro da Rede Cegonha (2 leitos por mil nascidos vivos) pede {fmt(T['nv']*2/1000,0)}. O saldo, porém, é desigual: "
                  + "; ".join(f"{r['nome']} {('+' if r['saldo_utin_rc']>=0 else '')}{fmt(r['saldo_utin_rc'])}" for r in (def_utin[:3] + def_utin[-2:])) + ". Os círculos são hospitais com UTIN, proporcionais aos leitos.",
         "modelo": "Parâmetros de necessidade (Caderno 1, NESCON/UFMG, 2017): necessidade pela população residente, oferta pela localização, corrigida pelo fator de não residentes (Fnr). Donabedian: estrutura sem processo não explica resultado.",
         "fonte": "CNES (leitos, competência de dezembro) e SINASC (nascidos vivos)."},
        {"id": "prenatal", "titulo": "Pré-natal adequado", "variavel": "pct_prenatal_adequado", "nivel": nivel_cmp, "camadas": ["coropleta"], "duracao": 16,
         "texto": f"{fmt(T['pct_pn'])}% das gestantes de {ano_ref} tiveram pré-natal adequado ou mais que adequado pelo índice de Kotelchuck (início até o 4º mês e número de consultas compatível com a duração da gestação). Por {nome_nivel}: de {fmt(pn_lo['pct_prenatal_adequado'])}% ({pn_lo['nome']}) a {fmt(pn_hi['pct_prenatal_adequado'])}% ({pn_hi['nome']}).",
         "modelo": "Kotelchuck (1994) mede utilização, não conteúdo. Continuum do cuidado (Kerber et al., 2007): a lacuna entre pré-natal e parto é o que se procura.",
         "fonte": "SINASC: KOTELCHUCK (calculado pelo DATASUS)."},
    ]
    if clusters:
        pf = clusters["perfil"]; cmeta = clusters["meta"]
        anos_txt = "-".join(str(a) for a in cmeta.get("anos", anos))
        nA = sum(1 for p in pf if p["estrato"] == "A"); nB = sum(1 for p in pf if p["estrato"] == "B")
        pior = max(pf, key=lambda p: p["tmnp_bruta"]); melhor = min(pf, key=lambda p: p["tmnp_bruta"])
        descr = "; ".join(f"{p['tipo']} ({fmt(p['n_municipios'],0)} municípios, {fmt(p['pct_nv_uf'])}% dos NV): {p['rotulo']}" for p in pf)
        cenas.append({"id": "tipologia", "titulo": "Tipos de município: acesso e organização", "variavel": "cluster", "nivel": "municipio", "camadas": ["coropleta"], "duracao": 26,
            "texto": f"Agrupamento dos {fmt(cmeta['n_municipios'],0)} municípios ({anos_txt}) por tamanho, distância às UTI neonatal e adulto, distância ao município que realiza cesáreas, distância média percorrida, partos fora do município e da região, cesáreas recebidas de outros municípios, leitos, pré-natal, SUS e cesárea — mortalidade não entra no agrupamento, é descrita por tipo. Antes do agrupamento, os municípios são separados em sem (A, {nA} tipos) e com (B, {nB} tipos) serviço de parto local. {descr}. Mortalidade neonatal precoce: de {fmt(melhor['tmnp_bruta'],2)} ({melhor['tipo']}) a {fmt(pior['tmnp_bruta'],2)} ({pior['tipo']}) por mil NV; a média estadual é {fmt(cmeta['tmnp_uf'],2)}.",
            "modelo": "Tipologia como etapa do modelo hierárquico (Victora et al., 1997): contexto e acesso (nível distal) descritos antes de qualquer associação com o desfecho. Três Demoras (Thaddeus e Maine, 1994) para as distâncias; Andersen (1995) para oferta e financiamento. k-means sobre variáveis padronizadas com blocos equilibrados; k pela silhueta; taxas estabilizadas por Bayes empírico.",
            "fonte": "SINASC, SIM, SIH, CNES e base territorial (coordenadas das sedes). Distâncias geodésicas entre sedes, não rodoviárias."})
    anos_sifc = sorted({int(a) for r in ind if r["nivel"] == "municipio" and r.get("sifc_disponivel") == 1 for a in [r["ano"]]})
    if anos_sifc:
        ano_s = anos_sifc[-1]
        rows_s = [r for r in ind if r["nivel"] == nivel_cmp and r["ano"] == ano_s and r.get("tx_sifc_por_1000nv") is not None and (r["nascidos_vivos"] or 0) >= 500]
        rows_s.sort(key=lambda r: r["tx_sifc_por_1000nv"], reverse=True)
        casos_uf = sum((r.get("casos_sifc") or 0) for r in ind if r["nivel"] == "municipio" and r["ano"] == ano_s)
        nv_uf = sum((r.get("nascidos_vivos") or 0) for r in ind if r["nivel"] == "municipio" and r["ano"] == ano_s)
        txt = (f"Em {ano_s}, {fmt(casos_uf,0)} casos de sífilis congênita foram notificados em residentes de {UF}: {fmt(1000*casos_uf/max(nv_uf,1),1)} por mil nascidos vivos. "
               f"Por {nome_nivel}, de {fmt(rows_s[-1]['tx_sifc_por_1000nv'],1)} ({rows_s[-1]['nome']}) a {fmt(rows_s[0]['tx_sifc_por_1000nv'],1)} ({rows_s[0]['nome']}).")
        if sifilis:
            sm = sifilis["meta"]
            txt += (f" Em {fmt(sm['pct_sem_prenatal_uf'])}% dos casos a mãe não fez pré-natal; em {fmt(sm['pct_trat_inadequado_uf'])}% o tratamento materno foi inadequado ou não feito; "
                    f"em {fmt(sm['pct_diag_tardio_uf'])}% a sífilis materna só foi diagnosticada no parto, depois dele ou não foi.")
            cm_ = [c for c in sifilis["correlacoes"] if c["nivel"] == "municipio" and c.get("rho") is not None]
            cm_.sort(key=lambda c: abs(c["rho"]), reverse=True)
            if cm_:
                txt += " Correlações de Spearman com as demais taxas municipais (municípios com ≥ " + fmt(sm["nv_minimo_municipio"],0) + " NV): " + "; ".join(
                    f"{c['rotulo'].split(',')[0]} ρ = {fmt(c['rho'],2)}" for c in cm_[:4]) + "."
            rg = {r["variavel"]: r for r in sifilis["regressao"]}
            if rg:
                partes = []
                for v, nome_v in (("pct_prenatal_adequado", "pré-natal adequado"), ("pct_evasao_regiao", "partos fora da região"), ("dist_utin_km", "distância à UTIN"), ("log_nv_ano", "tamanho do município")):
                    if v in rg:
                        partes.append(f"{nome_v} IRR {fmt(rg[v]['irr_por_dp'],2)} [{fmt(rg[v]['irr_ic_inf'],2)}–{fmt(rg[v]['irr_ic_sup'],2)}]")
                txt += " Ajustadas entre si (quase-Poisson, por desvio-padrão): " + "; ".join(partes) + "."
            if sifilis["por_tipo"]:
                pt = sorted(sifilis["por_tipo"], key=lambda p: p["tx_sifc"])
                txt += f" Por tipo de município, de {fmt(pt[0]['tx_sifc'],1)} ({pt[0]['tipo']}) a {fmt(pt[-1]['tx_sifc'],1)} ({pt[-1]['tipo']}) por mil NV; {fmt(pt[0]['pct_municipios_sem_caso'],0)}% dos municípios {pt[0]['tipo']} não notificaram caso algum."
        txt += " Leitura ecológica: as unidades são municípios; taxas altas também refletem capacidade de testar e notificar."
        cenas.append({"id": "sifilis", "titulo": "Sífilis congênita: o pré-natal que falhou", "variavel": "tx_sifc_por_1000nv", "nivel": nivel_cmp, "camadas": ["coropleta"], "duracao": 30,
            "texto": txt,
            "modelo": "Evento sentinela da qualidade do pré-natal (OMS/MS: diagnóstico e tratamento na gestação evitam o caso). Modelo hierárquico (Victora et al., 1997): escolaridade e idade materna no nível distal, pré-natal no intermediário. Associações entre taxas municipais são ecológicas (Morgenstern, 1995): descrevem territórios, não gestantes.",
            "fonte": "SINAN (sífilis congênita, por residência, ano do diagnóstico), SINASC (denominador), tipologia e distâncias do passo 'analise'."})
    if espacial:
        em = espacial["meta"]; nv_e = em.get("niveis") or {}
        def lista_quentes(nivel, k, n=6):
            q = (espacial["quentes"].get(nivel) or {}).get(k) or []
            return "; ".join(f"{x['nome']} (RR {fmt(x['rr'],2)}, P {fmt(x['prob'],2)})" for x in q[:n])
        def cena_quente(k, titulo, modelo):
            im = nv_e.get("municipio", {}).get(k); ir = nv_e.get("regiao_saude", {}).get(k)
            if not im: return None
            mor = im.get("moran") or {}
            txt = (f"{im['rotulo']}: {fmt(im['obs'],0)} casos em {'-'.join(str(a) for a in (em.get('anos_sinan') if k == 'sifc' else em.get('anos')))}. "
                   f"O modelo bayesiano espacial (BYM2) estima, para cada município, o risco relativo em relação à média estadual e a probabilidade de ele ser maior que 1, emprestando força dos vizinhos onde há poucos nascimentos. "
                   f"Áreas quentes (P ≥ {fmt(100*em['limiares']['quente'],0)}%): {im['quentes']} municípios ({im['quentes_fortes']} com P ≥ {fmt(100*em['limiares']['forte'],0)}%)")
            if ir: txt += f" e {ir['quentes']} regiões de saúde: {lista_quentes('regiao_saude', k, 6)}."
            else: txt += "."
            txt += f" Municípios com maior probabilidade: {lista_quentes('municipio', k, 6)}."
            if mor.get("I") is not None: txt += f" Autocorrelação espacial (Moran I sobre o risco sem suavização espacial) = {fmt(mor['I'],2)} (p {fmt(mor['p'],3)}); aglomerados alto-alto (LISA): {im['alto_alto']} municípios."
            txt += f" Vizinhança: {em['vizinhanca']}."
            return {"id": f"quente_{k}", "titulo": titulo, "variavel": f"esp:{k}:classe", "nivel": "municipio", "camadas": ["coropleta"], "duracao": 28, "texto": txt, "modelo": modelo,
                    "fonte": ("SINAN" if k == "sifc" else "SIM") + " (numerador, por residência) e SINASC (denominador); esperados por padronização indireta pela taxa estadual."}
        c1 = cena_quente("sifc", "Áreas quentes: sífilis congênita", "Mapeamento de doenças (Besag, York e Mollié, 1991; Riebler et al., 2016): o risco de cada área é estimado com informação dos vizinhos; a decisão por probabilidade de excesso de risco segue Richardson et al. (2004). Moran (1950) e LISA (Anselin, 1995) para aglomerados.")
        c2 = cena_quente("neonatal_precoce", "Áreas quentes: mortalidade neonatal precoce", "Mesmo modelo; a mortalidade neonatal precoce é o desfecho mais sensível à assistência ao parto e ao recém-nascido (Lansky et al., 2014). Mortalidade materna: eventos raros demais para áreas quentes municipais; veja o nível região de saúde na análise livre.")
        for c_ in (c1, c2):
            if c_: cenas.append(c_)
    cenas += [
        {"id": "sus", "titulo": "Quem financia o parto", "variavel": "pct_partos_sus", "nivel": nivel_cmp, "camadas": ["coropleta"], "duracao": 14,
         "texto": f"As internações de parto do SUS corresponderam a {fmt(T['pct_sus'])}% dos nascidos vivos de {ano_ref}; o restante nasceu no setor privado ou suplementar. A proporção SUS muda o denominador correto de qualquer parâmetro de leitos.",
         "modelo": "Andersen (1995): recursos capacitantes. Equação 1 do Caderno 1: população de referência = nascidos vivos × proporção sem plano de saúde.",
         "fonte": "SIH/SUS (AIHs de parto, por residência) e SINASC."},
    ]
    meta = {
        "uf": UF, "uf_cod": UF_COD, "anos": anos, "ano_ref": ano_ref, "gerado_em": datetime.now(timezone.utc).isoformat(),
        "nivel_comparacao": nivel_cmp, "totais": tot,
        "variaveis": {
            "pct_parto_fora_municipio": {"nome": "Partos fora do município de residência", "unid": "%", "paleta": "seq", "desc": "Nascidos vivos cujo município do parto difere do de residência da mãe."},
            "pct_parto_fora_regiao": {"nome": "Partos fora da região de saúde", "unid": "%", "paleta": "seq"},
            "pct_cesarea": {"nome": "Taxa de cesárea", "unid": "%", "paleta": "seq"},
            "pct_cesarea_sem_tp": {"nome": "Cesárea sem trabalho de parto", "unid": "% dos nascimentos", "paleta": "seq"},
            "tx_mort_neonatal_precoce": {"nome": "Mortalidade neonatal precoce (0-6 dias)", "unid": "por mil NV", "paleta": "seq", "dec": 2},
            "tx_mort_neonatal": {"nome": "Mortalidade neonatal (0-27 dias)", "unid": "por mil NV", "paleta": "seq", "dec": 2},
            "pct_prenatal_adequado": {"nome": "Pré-natal adequado (Kotelchuck)", "unid": "%", "paleta": "seq_inv"},
            "pct_prematuro": {"nome": "Prematuridade (< 37 semanas)", "unid": "%", "paleta": "seq"},
            "pct_baixo_peso": {"nome": "Baixo peso ao nascer (< 2.500 g)", "unid": "%", "paleta": "seq"},
            "pct_partos_sus": {"nome": "Partos SUS / nascidos vivos", "unid": "%", "paleta": "seq"},
            "leitos_utin_por_1000nv": {"nome": "Leitos de UTI neonatal por mil NV (localização)", "unid": "por mil NV", "paleta": "seq_inv", "dec": 2},
            "saldo_utin_rc": {"nome": "Saldo de leitos de UTIN (existente − necessário, Rede Cegonha)", "unid": "leitos", "paleta": "div", "dec": 1},
            "saldo_obst_rc": {"nome": "Saldo de leitos obstétricos (existente − necessário)", "unid": "leitos", "paleta": "div", "dec": 1},
            "rmm_declarada": {"nome": "Razão de mortalidade materna declarada", "unid": "por 100 mil NV", "paleta": "seq"},
            "near_miss_por_1000nv": {"nome": "Near miss materno (proxy SIH)", "unid": "por mil NV", "paleta": "seq", "dec": 2},
            "rn_uti_por_1000nv": {"nome": "Recém-nascidos em UTI (SIH) por mil NV", "unid": "por mil NV", "paleta": "seq"},
            "fnr": {"nome": "Fator de não residentes (Fnr)", "unid": "", "paleta": "seq", "dec": 2},
            "tx_sifc_por_1000nv": {"nome": "Sífilis congênita (casos por mil NV)", "unid": "por mil NV", "paleta": "seq", "dec": 1, "correlacao": True,
                                   "desc": "Casos notificados no SINAN por município de residência da mãe, no ano do diagnóstico; só nos anos com arquivo do SINAN carregado."},
            "pct_sifc_sem_prenatal": {"nome": "Sífilis congênita: mãe sem pré-natal", "unid": "% dos casos", "paleta": "seq"},
            "pct_sifc_trat_inadequado": {"nome": "Sífilis congênita: tratamento materno inadequado ou não feito", "unid": "% dos casos", "paleta": "seq"},
        },
    }
    if espacial:
        em = espacial["meta"]; CORES = {1: ["#86b6ef", "#3987e5"], 2: ["#d3dae1", "#46525f"], 3: ["#f0a3a3", "#8a3b3b"], 4: ["#e34948", "#e66767"]}
        ROT = {1: f"risco abaixo do estadual (P ≤ {fmt(100*em['limiares']['fria'],0)}%)", 2: "indefinida", 3: f"área quente (P ≥ {fmt(100*em['limiares']['quente'],0)}%)", 4: f"quente, alta confiança (P ≥ {fmt(100*em['limiares']['forte'],0)}%)"}
        for k, info in (em.get("niveis") or {}).get("municipio", {}).items():
            rot = info["rotulo"]
            cats = [{"id": i, "tipo": ["", "fria", "indef.", "quente", "quente+"][i], "rotulo": ROT[i], "cor": CORES[i][0], "cor_escura": CORES[i][1], "n": 0} for i in (4, 3, 2, 1)]
            meta["variaveis"][f"esp:{k}:classe"] = {"nome": f"Áreas quentes: {rot.lower()}", "unid": "", "paleta": "cat", "esp": k, "campo": "classe", "categorias": cats, "anos": em.get("anos_sinan") if k == "sifc" else em.get("anos"),
                                                     "desc": f"Probabilidade a posteriori de o risco relativo suavizado (modelo BYM2) ser maior que 1. {rot}, por residência."}
            meta["variaveis"][f"esp:{k}:rr"] = {"nome": f"Risco relativo suavizado: {rot.lower()}", "unid": "RR (1 = média estadual)", "paleta": "div_inv", "esp": k, "campo": "rr", "dec": 2, "quebras": [0.67, 0.9, 1.1, 1.5], "anos": em.get("anos_sinan") if k == "sifc" else em.get("anos")}
            meta["variaveis"][f"esp:{k}:prob"] = {"nome": f"P(RR > 1): {rot.lower()}", "unid": "probabilidade", "paleta": "seq", "esp": k, "campo": "prob", "dec": 2, "quebras": [0.2, 0.5, 0.8, 0.95], "anos": em.get("anos_sinan") if k == "sifc" else em.get("anos")}
    if clusters:
        meta["variaveis"]["cluster"] = {"nome": "Tipologia dos municípios (agrupamento)", "unid": "", "paleta": "cat", "so_municipio": True,
                                        "desc": "Grupos de municípios por tamanho, distâncias (UTI neonatal, UTI adulto, cesárea, parto), fluxos, oferta e processo; A = sem serviço de parto local, B = com serviço.",
                                        "categorias": [{"id": int(p["cluster"]), "tipo": p["tipo"], "estrato": p["estrato"], "rotulo": p["rotulo"], "n": int(p["n_municipios"]),
                                                        "pct_nv": p["pct_nv_uf"], "tmnp": p["tmnp_bruta"], "tmnp_ic": [p["tmnp_ic_inf"], p["tmnp_ic_sup"]],
                                                        "nv_ano": p["nv_ano"], "dist_utin_km": p["dist_utin_km"], "dist_cesarea_km": p["dist_cesarea_km"],
                                                        "pct_evasao_partos": p["pct_evasao_partos"], "pct_evasao_regiao": p["pct_evasao_regiao"],
                                                        "pct_invasao_cesareas": p["pct_invasao_cesareas"], "pct_prenatal_adequado": p["pct_prenatal_adequado"],
                                                        "pct_cesarea": p["pct_cesarea"], "distintivas": p["distintivas"]} for p in clusters["perfil"]],
                                        "tmnp_uf": clusters["meta"].get("tmnp_uf"), "anos": clusters["meta"].get("anos")}
        # variáveis contínuas da tipologia (só no nível municipal; valores pooled nos anos do agrupamento)
        meta["variaveis"].update({
            "tip:dist_utin_km": {"nome": "Distância à UTI neonatal mais próxima", "unid": "km", "paleta": "seq", "so_municipio": True, "tipologia": "dist_utin_km", "dec": 0},
            "tip:dist_cesarea_km": {"nome": "Distância ao município que realiza cesáreas", "unid": "km", "paleta": "seq", "so_municipio": True, "tipologia": "dist_cesarea_km", "dec": 0},
            "tip:dist_media_km": {"nome": "Distância média percorrida para o parto", "unid": "km", "paleta": "seq", "so_municipio": True, "tipologia": "dist_media_km", "dec": 0},
            "tip:pct_invasao_cesareas": {"nome": "Cesáreas realizadas em mães de outros municípios", "unid": "%", "paleta": "seq", "so_municipio": True, "tipologia": "pct_invasao_cesareas"},
            "tip:tmnp_eb": {"nome": "Mortalidade neonatal precoce estabilizada (Bayes empírico)", "unid": "por mil NV", "paleta": "seq", "so_municipio": True, "tipologia": "tmnp_eb", "dec": 2},
        })
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "cenas.json").write_text(json.dumps(cenas, ensure_ascii=False, indent=1), encoding="utf-8")
    log("meta.json e cenas.json gravados")

    # ---------- malha do IBGE ----------
    malha = OUT / f"malha_{UF.lower()}.geojson"
    if malha.exists() and malha.stat().st_size > 10000:
        log(f"malha já existe: {malha.name}")
    else:
        try:
            log("IBGE: baixando malha municipal (qualidade mínima)")
            req = urllib.request.Request(MALHA_URL, headers={"User-Agent": "nascer-pipeline/0.1"})
            with urllib.request.urlopen(req, timeout=180) as r:
                dados = r.read()
            gj = json.loads(dados)
            # mantém só o código do município nas propriedades
            for f in gj.get("features", []):
                cod = f.get("properties", {}).get("codarea")
                f["properties"] = {"cod7": str(cod)}
            malha.write_text(json.dumps(gj, separators=(",", ":")), encoding="utf-8")
            log(f"  {len(gj.get('features', []))} polígonos, {malha.stat().st_size/1e6:.1f} MB")
        except Exception as e:  # noqa: BLE001
            log(f"  malha não baixada ({e}); o app usará pontos nas sedes municipais")
    return 0


if __name__ == "__main__":
    sys.exit(main())
