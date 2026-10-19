#!/usr/bin/env python3
"""
Tipologia de municípios: agrupamento (k-means) por características de contexto, acesso geográfico, fluxos,
oferta e processo — e, opcionalmente, desfechos. Lê o warehouse (DuckDB) construído pelo dbt e grava:

  main_analise.cluster_municipios   um registro por município: variáveis, grupo (cluster) e rótulo
  main_analise.cluster_perfil       um registro por grupo: médias das variáveis, NV, mortalidade bruta com IC 95%
  main_analise.cluster_meta         parâmetros usados, k escolhido, silhuetas por k, data
  /data/export/cluster_*.csv        os mesmos dados em CSV

Uso: python cluster_municipios.py [cluster_config.yml]   (variáveis: DUCKDB_PATH, UF_COD, EXPORT_DIR)

Método (resumo): variáveis pooladas nos anos do warehouse; distâncias geodésicas (haversine) entre sedes
municipais; taxas de mortalidade estabilizadas por Bayes empírico (prior = taxa da região de saúde, peso em NV);
transformação log1p nas variáveis assimétricas; padronização z; k-means (25 inícios, semente fixa);
k escolhido pela silhueta média. Grupos numerados por nascidos vivos médios (T1 = menores municípios).
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

DB = os.environ.get("DUCKDB_PATH", "/data/warehouse/nascer.duckdb")
UF_COD = os.environ.get("UF_COD") or {"MG": "31", "SP": "35", "RJ": "33", "ES": "32", "BA": "29", "PR": "41", "SC": "42",
                                       "RS": "43", "GO": "52", "DF": "53", "MT": "51", "MS": "50", "PE": "26", "CE": "23",
                                       "PA": "15", "AM": "13", "MA": "21", "PB": "25", "RN": "24", "AL": "27", "SE": "28",
                                       "PI": "22", "TO": "17", "RO": "11", "AC": "12", "AP": "16", "RR": "14"
                                       }.get(os.environ.get("UF", "MG").upper(), "31")
EXPORT = Path(os.environ.get("EXPORT_DIR", "/data/export"))
BRONZE = Path(os.environ.get("BRONZE_DIR", "/data/bronze"))
CONFIG = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("cluster_config.yml"))


def log(m: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


def haversine_km(lat1, lon1, lat2, lon2):
    """Distância geodésica (km) entre pontos; aceita arrays numpy (broadcast)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def dist_mais_proximo(df: pd.DataFrame, mascara: pd.Series) -> np.ndarray:
    """Para cada município, distância ao município mais próximo que satisfaz a máscara (0 se ele próprio)."""
    alvo = df.loc[mascara & df.lat.notna(), ["lat", "lon"]].to_numpy()
    out = np.full(len(df), np.nan)
    if not len(alvo):
        return out
    ok = df.lat.notna().to_numpy()
    pts = df.loc[ok, ["lat", "lon"]].to_numpy()
    d = haversine_km(pts[:, [0]], pts[:, [1]], alvo[None, :, 0], alvo[None, :, 1])   # n x m
    out[ok] = d.min(axis=1)
    out[mascara.to_numpy()] = 0.0
    return out


def montar_base(con, cfg) -> tuple[pd.DataFrame, list[int]]:
    anos = [int(a) for a in (cfg.get("anos") or [])]
    if not anos:
        anos = [r[0] for r in con.execute("select distinct ano from main_gold.ind_territorio order by 1").fetchall()]
    anos_sql = ",".join(str(a) for a in anos)
    n_anos = len(anos)
    ano_ref = max(anos)
    lim_ces = float(cfg.get("limiar_cesareas_ano", 30)) * n_anos
    lim_par = float(cfg.get("limiar_partos_ano", 30)) * n_anos

    sql = f"""
    with m as (
        select cod7, nome, lat, lon, regiao_saude_cod, regiao_saude_nome, macro_cod, macro_nome
        from main_gold.dim_municipio where uf_cod = '{UF_COD}'
    ),
    t as (
        select cod,
               sum(nascidos_vivos)              as nv,
               sum(cesareas)                    as cesareas,
               sum(partos_fora_municipio)       as fora,
               sum(partos_fora_regiao)          as fora_reg,
               sum(prematuros)                  as prematuros,
               sum(baixo_peso)                  as baixo_peso,
               sum(prenatal_adequado)           as prenatal_adequado,
               sum(obitos_neonatais)            as obitos_neo,
               sum(obitos_neonatais_precoces)   as obitos_neo_prec,
               sum(partos_sus)                  as partos_sus
        from main_gold.ind_territorio where nivel = 'municipio' and ano in ({anos_sql}) group by cod
    ),
    sup as (
        select cod, leitos_obstetricos, leitos_uti_neonatal, leitos_uti_adulto
        from main_gold.ind_territorio where nivel = 'municipio' and ano = {ano_ref}
    ),
    occ as (
        select codmunnasc7 as cod,
               count(*)                                                   as partos_ocorridos,
               sum(cesarea)                                               as ces_ocorridas,
               sum(parto_fora_municipio)                                  as partos_recebidos,
               sum(case when cesarea = 1 and parto_fora_municipio = 1 then 1 else 0 end) as ces_recebidas
        from main_gold.fato_nascimentos where ano in ({anos_sql}) and codmunnasc7 is not null group by 1
    ),
    dist as (
        select codmunres7 as cod,
               avg(2 * 6371 * asin(sqrt(
                   power(sin(radians(lat_nasc - lat_res) / 2), 2)
                   + cos(radians(lat_res)) * cos(radians(lat_nasc)) * power(sin(radians(lon_nasc - lon_res) / 2), 2)
               ))) as dist_media_km
        from main_gold.fato_nascimentos
        where ano in ({anos_sql}) and lat_res is not null and lat_nasc is not null group by 1
    ),
    reg as (
        select cod as regiao_saude_cod,
               sum(obitos_neonatais_precoces) * 1000.0 / nullif(sum(nascidos_vivos), 0) as tmnp_reg,
               sum(obitos_neonatais)          * 1000.0 / nullif(sum(nascidos_vivos), 0) as tmn_reg
        from main_gold.ind_territorio where nivel = 'regiao_saude' and ano in ({anos_sql}) group by cod
    ),
    uf as (
        select sum(obitos_neonatais_precoces) * 1000.0 / nullif(sum(nascidos_vivos), 0) as tmnp_uf,
               sum(obitos_neonatais)          * 1000.0 / nullif(sum(nascidos_vivos), 0) as tmn_uf
        from main_gold.ind_territorio where nivel = 'municipio' and ano in ({anos_sql})
    )
    select m.*, t.nv, t.cesareas, t.fora, t.fora_reg, t.prematuros, t.baixo_peso, t.prenatal_adequado,
           t.obitos_neo, t.obitos_neo_prec, t.partos_sus,
           coalesce(sup.leitos_obstetricos, 0) as leitos_obstetricos,
           coalesce(sup.leitos_uti_neonatal, 0) as leitos_uti_neonatal,
           coalesce(sup.leitos_uti_adulto, 0) as leitos_uti_adulto,
           coalesce(occ.partos_ocorridos, 0) as partos_ocorridos, coalesce(occ.ces_ocorridas, 0) as ces_ocorridas,
           coalesce(occ.partos_recebidos, 0) as partos_recebidos, coalesce(occ.ces_recebidas, 0) as ces_recebidas,
           dist.dist_media_km,
           coalesce(reg.tmnp_reg, uf.tmnp_uf) as tmnp_reg, coalesce(reg.tmn_reg, uf.tmn_uf) as tmn_reg,
           uf.tmnp_uf, uf.tmn_uf
    from m
    left join t    on t.cod = m.cod7
    left join sup  on sup.cod = m.cod7
    left join occ  on occ.cod = m.cod7
    left join dist on dist.cod = m.cod7
    left join reg  on reg.regiao_saude_cod = m.regiao_saude_cod
    cross join uf
    order by m.cod7
    """
    df = con.execute(sql).fetchdf()
    for c in ["nv", "cesareas", "fora", "fora_reg", "prematuros", "baixo_peso", "prenatal_adequado", "obitos_neo",
              "obitos_neo_prec", "partos_sus"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(float)
    for c in ["leitos_obstetricos", "leitos_uti_neonatal", "leitos_uti_adulto", "partos_ocorridos", "ces_ocorridas",
              "partos_recebidos", "ces_recebidas"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(float)

    # população IBGE (opcional): bronze/territorio/populacao_ibge.parquet com cod7, ano, populacao
    pop = BRONZE / "territorio" / "populacao_ibge.parquet"
    if pop.exists():
        p = con.execute(f"select cod7, populacao from read_parquet('{pop}') qualify row_number() over (partition by cod7 order by ano desc) = 1").fetchdf()
        df = df.merge(p, on="cod7", how="left")
        log(f"população IBGE: {p.populacao.notna().sum()} municípios")
    else:
        df["populacao"] = np.nan
        log("população IBGE não disponível (bronze/territorio/populacao_ibge.parquet); nascidos vivos fazem o papel de tamanho")

    nv = df.nv.replace(0, np.nan)
    df["nv_ano"] = df.nv / n_anos
    df["pct_evasao_partos"] = 100 * df.fora / nv
    df["pct_evasao_regiao"] = 100 * df.fora_reg / nv
    df["pct_cesarea"] = 100 * df.cesareas / nv
    df["pct_prematuro"] = 100 * df.prematuros / nv
    df["pct_baixo_peso"] = 100 * df.baixo_peso / nv
    df["pct_prenatal_adequado"] = 100 * df.prenatal_adequado / nv
    df["pct_partos_sus"] = 100 * df.partos_sus / nv
    df["razao_atracao"] = df.partos_ocorridos / nv
    df["pct_invasao_cesareas"] = np.where(df.ces_ocorridas >= lim_ces, 100 * df.ces_recebidas / df.ces_ocorridas.replace(0, np.nan), 0.0)
    df["leitos_obst_por_1000nv"] = 1000 * df.leitos_obstetricos / df.nv_ano.replace(0, np.nan)
    df["tmnp_bruta"] = 1000 * df.obitos_neo_prec / nv
    df["tmn_bruta"] = 1000 * df.obitos_neo / nv
    k = float(cfg.get("peso_prior_nv", 500))
    df["tmnp_eb"] = 1000 * (df.obitos_neo_prec + k * df.tmnp_reg / 1000) / (df.nv + k)
    df["tmn_eb"] = 1000 * (df.obitos_neo + k * df.tmn_reg / 1000) / (df.nv + k)

    df["dist_utin_km"] = dist_mais_proximo(df, df.leitos_uti_neonatal > 0)
    df["dist_uti_adulto_km"] = dist_mais_proximo(df, df.leitos_uti_adulto > 0)
    df["dist_cesarea_km"] = dist_mais_proximo(df, df.ces_ocorridas >= lim_ces)
    df["dist_parto_km"] = dist_mais_proximo(df, df.partos_ocorridos >= lim_par)
    df["realiza_cesareas"] = (df.ces_ocorridas >= lim_ces).astype(int)
    df["realiza_partos"] = (df.partos_ocorridos >= lim_par).astype(int)
    df["tem_utin"] = (df.leitos_uti_neonatal > 0).astype(int)
    return df, anos


def transformar(df: pd.DataFrame, vars_cfg: dict, equilibrar: bool) -> tuple[np.ndarray, list[str], dict[str, float]]:
    """Seleciona, transforma (log1p), padroniza (z) e pondera por bloco as variáveis de um estrato."""
    usadas, cols = [], []
    for nome, spec in vars_cfg.items():
        if nome not in df.columns or df[nome].notna().sum() < 0.9 * len(df):
            log(f"    variável '{nome}' ignorada (ausente ou com muitos vazios)")
            continue
        v = df[nome].astype(float)
        v = v.fillna(v.median())
        if (spec or {}).get("transform") == "log1p":
            v = np.log1p(v.clip(lower=0))
        if v.std(ddof=0) < 1e-9:
            log(f"    variável '{nome}' ignorada (constante neste estrato)")
            continue
        cols.append(v.to_numpy())
        usadas.append(nome)
    Z = StandardScaler().fit_transform(np.column_stack(cols))
    pesos = {v: 1.0 for v in usadas}
    if equilibrar:
        blocos = {}
        for v in usadas:
            blocos.setdefault((vars_cfg[v] or {}).get("bloco", v), []).append(v)
        for b, vs in blocos.items():
            for v in vs:
                pesos[v] = 1.0 / math.sqrt(len(vs))
        Z = Z * np.array([pesos[v] for v in usadas])
    return Z, usadas, pesos


def agrupar(Z: np.ndarray, cfg) -> tuple[int, float, np.ndarray, dict[int, float]]:
    k_cfg = cfg.get("k", "auto")
    ks = [int(k_cfg)] if isinstance(k_cfg, int) else list(range(int(cfg.get("k_min", 2)), int(cfg.get("k_max", 4)) + 1))
    ks = [k for k in ks if 2 <= k < len(Z)]
    melhor, sil = None, {}
    tam_min = int(cfg.get("tamanho_minimo", 15))
    for k in ks:
        km = KMeans(n_clusters=k, n_init=25, random_state=42).fit(Z)
        s = float(silhouette_score(Z, km.labels_))
        tamanhos = sorted(np.bincount(km.labels_).tolist(), reverse=True)
        sil[k] = round(s, 4)
        ok = tamanhos[-1] >= tam_min
        log(f"    k={k}: silhueta {s:.3f}; tamanhos {tamanhos}{'' if ok else f' (descartado: grupo < {tam_min})'}")
        if ok and (melhor is None or s > melhor[1]):
            melhor = (k, s, km.labels_)
    if melhor is None:   # todos descartados pelo tamanho: fica com o menor k
        km = KMeans(n_clusters=ks[0], n_init=25, random_state=42).fit(Z)
        melhor = (ks[0], sil[ks[0]], km.labels_)
    return melhor[0], melhor[1], melhor[2], sil


def rotular(r: pd.Series) -> str:
    """Rótulo descritivo de um grupo a partir das médias (regras simples, legíveis)."""
    partes = []
    nv = r.get("nv_ano", np.nan)
    partes.append("pequenos" if nv < 100 else "médios" if nv < 400 else "médios-grandes" if nv < 1500 else "grandes")
    if r.get("realiza_partos", 0) < 0.5:
        partes.append("sem serviço de parto")
    elif r.get("razao_atracao", 0) >= 1.1 and r.get("pct_invasao_cesareas", 0) >= 15:
        partes.append("polo de partos e cesáreas")
    elif r.get("realiza_cesareas", 0) >= 0.5:
        partes.append("com parto e cesárea local")
    else:
        partes.append("com parto local sem cesárea")
    if r.get("tem_utin", 0) >= 0.5:
        partes.append("com UTI neonatal")
    elif r.get("dist_utin_km", 0) < 40:
        partes.append("UTIN até 40 km")
    elif r.get("dist_utin_km", 0) < 100:
        partes.append("UTIN a 40-100 km")
    else:
        partes.append("UTIN a mais de 100 km")
    ev = r.get("pct_evasao_regiao", np.nan)
    if ev >= 50:
        partes.append("maioria dos partos fora da região")
    elif ev >= 20:
        partes.append("parte dos partos fora da região")
    return ", ".join(partes)


def ic_poisson(obitos: float, nv: float) -> tuple[float, float]:
    """IC 95% aproximado (Byar) para taxa por mil."""
    if nv <= 0:
        return (float("nan"), float("nan"))
    d = max(obitos, 0)
    lo = d * (1 - 1 / (9 * d) - 1.96 / (3 * math.sqrt(d))) ** 3 if d > 0 else 0.0
    hi = (d + 1) * (1 - 1 / (9 * (d + 1)) + 1.96 / (3 * math.sqrt(d + 1))) ** 3
    return (1000 * lo / nv, 1000 * hi / nv)


def main() -> int:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    modo = cfg.get("modo", "determinantes")
    con = duckdb.connect(DB)
    log(f"warehouse: {DB}; UF {UF_COD}; modo {modo}")
    df, anos = montar_base(con, cfg)
    df = df[df.nv > 0].reset_index(drop=True)
    log(f"{len(df)} municípios com nascidos vivos em {anos}")

    vars_cfg = dict(cfg["variaveis"]["determinantes"])
    if modo == "completo":
        vars_cfg.update(cfg["variaveis"]["desfechos"])
    equilibrar = str(cfg.get("equilibrar_blocos", "sim")).lower() in ("sim", "s", "true", "1", "yes")
    estrato_col = cfg.get("estratificar_por", "realiza_partos")
    if estrato_col in (None, "", "nenhuma", "none"):
        estratos = [("T", "todos os municípios", pd.Series(True, index=df.index))]
    else:
        estratos = [("A", "sem serviço de parto local", df[estrato_col] == 0), ("B", "com serviço de parto local", df[estrato_col] == 1)]

    df["cluster"] = 0
    df["tipo"] = ""
    df["estrato"] = ""
    zcols: dict[str, pd.DataFrame] = {}
    meta_estratos = {}
    usadas_tot: dict[str, str] = {}
    prox = 1
    for prefixo, descr, mascara in estratos:
        sub = df[mascara]
        log(f"estrato {prefixo} ({descr}): {len(sub)} municípios")
        if len(sub) < 10:
            df.loc[mascara, ["cluster", "tipo", "estrato"]] = [prox, f"{prefixo}1", prefixo]
            prox += 1
            continue
        Z, usadas, pesos = transformar(sub, vars_cfg, equilibrar)
        for v in usadas:
            usadas_tot[v] = vars_cfg[v].get("rotulo", v)
        log(f"  variáveis ({len(usadas)}): {', '.join(usadas)}")
        k, sil, labels, sils = agrupar(Z, cfg)
        log(f"  k escolhido: {k} (silhueta {sil:.3f})")
        # ordem dos grupos dentro do estrato: A por distância à UTIN (mais perto primeiro); B por tamanho
        tmp = sub.assign(_g=labels)
        chave = "dist_utin_km" if prefixo == "A" else "nv_ano"
        ordem = tmp.groupby("_g")[chave].mean().sort_values().index.tolist()
        mapa = {g: i for i, g in enumerate(ordem)}
        df.loc[mascara, "cluster"] = [prox + mapa[g] for g in labels]
        df.loc[mascara, "tipo"] = [f"{prefixo}{mapa[g] + 1}" for g in labels]
        df.loc[mascara, "estrato"] = prefixo
        zcols[prefixo] = pd.DataFrame(Z, columns=usadas, index=sub.index).assign(_c=df.loc[mascara, "cluster"].to_numpy())
        meta_estratos[prefixo] = {"descricao": descr, "n": int(len(sub)), "k": k, "silhueta": round(sil, 4), "silhuetas_por_k": sils,
                                  "variaveis": usadas, "pesos": {v: round(w, 3) for v, w in pesos.items()}}
        prox += k
    df["cluster"] = df.cluster.astype(int)

    cols_perfil = ["nv_ano", "populacao", "dist_utin_km", "dist_uti_adulto_km", "dist_cesarea_km", "dist_parto_km",
                   "dist_media_km", "pct_evasao_partos", "pct_evasao_regiao", "pct_invasao_cesareas", "razao_atracao",
                   "leitos_obst_por_1000nv", "pct_prenatal_adequado", "pct_partos_sus", "pct_cesarea", "pct_prematuro",
                   "pct_baixo_peso", "tmnp_eb", "tmn_eb", "tem_utin", "realiza_cesareas", "realiza_partos"]
    perfil = df.groupby("cluster")[cols_perfil].mean(numeric_only=True)
    perfil["tipo"] = df.groupby("cluster").tipo.first()
    perfil["estrato"] = df.groupby("cluster").estrato.first()
    perfil["n_municipios"] = df.groupby("cluster").size()
    perfil["nv_total"] = df.groupby("cluster").nv.sum()
    perfil["pct_nv_uf"] = 100 * perfil.nv_total / df.nv.sum()
    perfil["obitos_neo_prec"] = df.groupby("cluster").obitos_neo_prec.sum()
    perfil["obitos_neo"] = df.groupby("cluster").obitos_neo.sum()
    perfil["tmnp_bruta"] = 1000 * perfil.obitos_neo_prec / perfil.nv_total
    perfil["tmn_bruta"] = 1000 * perfil.obitos_neo / perfil.nv_total
    ics = [ic_poisson(o, n) for o, n in zip(perfil.obitos_neo_prec, perfil.nv_total)]
    perfil["tmnp_ic_inf"] = [a for a, _ in ics]
    perfil["tmnp_ic_sup"] = [b for _, b in ics]
    tmnp_uf = 1000 * df.obitos_neo_prec.sum() / df.nv.sum()
    perfil["tmnp_razao_uf"] = perfil.tmnp_bruta / tmnp_uf
    perfil["rotulo"] = [rotular(r) for _, r in perfil.iterrows()]
    df["rotulo"] = df.cluster.map(perfil.rotulo)
    # variáveis mais distintivas de cada grupo (|z| da média do grupo dentro do estrato)
    dist = {}
    for prefixo, zdf in zcols.items():
        zm = zdf.groupby("_c").mean()
        for c in zm.index:
            top = zm.loc[c].abs().sort_values(ascending=False).index[:3]
            dist[int(c)] = ", ".join(f"{v} {'↑' if zm.loc[c, v] > 0 else '↓'}" for v in top)
    perfil["distintivas"] = perfil.index.map(lambda c: dist.get(int(c), ""))
    perfil = perfil.reset_index()
    k_total = int(df.cluster.nunique())

    # grava
    con.execute("create schema if not exists main_analise")
    saida = df[["cod7", "nome", "regiao_saude_cod", "regiao_saude_nome", "macro_cod", "macro_nome", "lat", "lon", "nv", "nv_ano",
                "populacao", "estrato", "cluster", "tipo", "rotulo"] + [c for c in cols_perfil if c not in ("nv_ano", "populacao")]
               + ["tmnp_bruta", "tmn_bruta", "obitos_neo_prec", "obitos_neo", "partos_ocorridos", "ces_ocorridas", "ces_recebidas"]].copy()
    con.register("saida_df", saida)
    con.execute("create or replace table main_analise.cluster_municipios as select * from saida_df")
    con.register("perfil_df", perfil)
    con.execute("create or replace table main_analise.cluster_perfil as select * from perfil_df")
    meta = {"gerado_em": datetime.now(timezone.utc).isoformat(), "modo": modo, "anos": anos, "estratificar_por": estrato_col,
            "equilibrar_blocos": equilibrar, "k_total": k_total, "estratos": meta_estratos, "rotulos_variaveis": usadas_tot,
            "peso_prior_nv": cfg.get("peso_prior_nv", 500), "limiar_cesareas_ano": cfg.get("limiar_cesareas_ano", 30),
            "limiar_partos_ano": cfg.get("limiar_partos_ano", 30), "n_municipios": int(len(df)), "tmnp_uf": round(float(tmnp_uf), 3),
            "tipos": {str(r.tipo): {"cluster": int(r.cluster), "rotulo": r.rotulo, "n": int(r.n_municipios)} for _, r in perfil.iterrows()}}
    con.execute("create or replace table main_analise.cluster_meta as select ?::json as meta", [json.dumps(meta, ensure_ascii=False)])
    con.close()
    EXPORT.mkdir(parents=True, exist_ok=True)
    saida.to_csv(EXPORT / "cluster_municipios.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    perfil.to_csv(EXPORT / "cluster_perfil.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    (EXPORT / "cluster_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # resumo
    log("perfil dos grupos (médias):")
    pd.set_option("display.width", 200)
    mostra = perfil[["tipo", "n_municipios", "pct_nv_uf", "nv_ano", "dist_utin_km", "dist_cesarea_km", "dist_media_km", "pct_evasao_partos",
                     "pct_evasao_regiao", "pct_invasao_cesareas", "pct_prenatal_adequado", "pct_cesarea", "tmnp_bruta", "tmnp_ic_inf", "tmnp_ic_sup"]].round(1)
    print(mostra.to_string(index=False))
    for _, r in perfil.iterrows():
        print(f"  {r.tipo}: {r.rotulo}  [{r.distintivas}]")
    log(f"gravado: main_analise.cluster_municipios / cluster_perfil / cluster_meta e {EXPORT}/cluster_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
