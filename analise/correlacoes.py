#!/usr/bin/env python3
"""
Sífilis congênita × demais taxas municipais. Lê o warehouse (DuckDB) e grava em main_analise:

  sifc_municipios      base analítica: um registro por município (anos com SINAN carregado), taxas e distâncias
  sifc_correlacoes     correlação de Spearman (rho, IC 95%, p, n) entre a taxa de sífilis congênita e cada variável,
                       por nível (município com NV mínimo, região de saúde, macrorregião)
  sifc_por_tipo        taxa de sífilis congênita por tipo de município (tipologia), com IC 95% (Poisson)
  sifc_regressao       modelo de Poisson com sobredispersão (quase-Poisson) para os casos, com offset ln(NV):
                       razões de taxas ajustadas (IRR) com IC 95%
  sifc_meta            parâmetros, anos, totais
  /data/export/sifc_*.csv  os mesmos dados em CSV

Uso: python correlacoes.py [correlacoes_config.yml]   (variáveis: DUCKDB_PATH, UF_COD, EXPORT_DIR)

Leitura ecológica: as unidades são municípios, não gestantes. Uma correlação entre taxas descreve o território,
não o risco individual (falácia ecológica). As taxas municipais de sífilis congênita dependem também da
capacidade de diagnóstico e notificação: um município com mais casos pode ser o que mais testa.
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
from scipy import stats

DB = os.environ.get("DUCKDB_PATH", "/data/warehouse/nascer.duckdb")
UF_COD = os.environ.get("UF_COD") or {"MG": "31", "SP": "35", "RJ": "33", "ES": "32", "BA": "29", "PR": "41", "SC": "42",
                                       "RS": "43", "GO": "52", "DF": "53", "MT": "51", "MS": "50", "PE": "26", "CE": "23",
                                       "PA": "15", "AM": "13", "MA": "21", "PB": "25", "RN": "24", "AL": "27", "SE": "28",
                                       "PI": "22", "TO": "17", "RO": "11", "AC": "12", "AP": "16", "RR": "14"
                                       }.get(os.environ.get("UF", "MG").upper(), "31")
EXPORT = Path(os.environ.get("EXPORT_DIR", "/data/export"))
CONFIG = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("correlacoes_config.yml"))


def log(m: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


def ic_poisson(casos: float, nv: float) -> tuple[float, float]:
    if nv <= 0:
        return (float("nan"), float("nan"))
    d = max(casos, 0)
    lo = d * (1 - 1 / (9 * d) - 1.96 / (3 * math.sqrt(d))) ** 3 if d > 0 else 0.0
    hi = (d + 1) * (1 - 1 / (9 * (d + 1)) + 1.96 / (3 * math.sqrt(d + 1))) ** 3
    return (1000 * lo / nv, 1000 * hi / nv)


def spearman_ic(x: pd.Series, y: pd.Series) -> dict:
    m = x.notna() & y.notna()
    n = int(m.sum())
    if n < 8:
        return {"n": n, "rho": np.nan, "ic_inf": np.nan, "ic_sup": np.nan, "p": np.nan}
    rho, p = stats.spearmanr(x[m], y[m])
    z = np.arctanh(np.clip(rho, -0.999999, 0.999999))
    se = 1.06 / math.sqrt(n - 3)     # Fieller/Bonett-Wright para Spearman
    return {"n": n, "rho": float(rho), "ic_inf": float(np.tanh(z - 1.96 * se)), "ic_sup": float(np.tanh(z + 1.96 * se)), "p": float(p)}


def base_municipal(con, anos: list[int], tem_tipologia: bool) -> pd.DataFrame:
    anos_sql = ",".join(str(a) for a in anos)
    tip = ""
    if tem_tipologia:
        tip = """
    left join (select cod7, estrato, tipo, rotulo, cluster, dist_utin_km, dist_uti_adulto_km, dist_cesarea_km, dist_parto_km, dist_media_km,
                      pct_invasao_cesareas, razao_atracao, tmnp_eb, tmn_eb
               from main_analise.cluster_municipios) c on c.cod7 = i.cod"""
    sql = f"""
    with i as (
        select cod, min(nome) as nome,
               sum(nascidos_vivos) as nv, sum(casos_sifc) as casos_sifc, sum(sifc_sem_prenatal) as sifc_sem_prenatal,
               sum(sifc_trat_inadequado) as sifc_trat_inadequado, sum(sifc_diag_materno_tardio) as sifc_diag_materno_tardio,
               sum(cesareas) as cesareas, sum(partos_fora_municipio) as fora, sum(partos_fora_regiao) as fora_reg,
               sum(prematuros) as prematuros, sum(baixo_peso) as baixo_peso, sum(prenatal_adequado) as prenatal_adequado,
               sum(prenatal_7mais) as prenatal_7mais, sum(maes_menor_20) as maes_menor_20, sum(maes_ate_fundamental) as maes_ate_fundamental,
               sum(obitos_neonatais) as obitos_neo, sum(obitos_neonatais_precoces) as obitos_neo_prec, sum(partos_sus) as partos_sus,
               sum(partos_em_hospital_com_utin) as partos_utin, max(leitos_obstetricos) as leitos_obst, max(tem_utin) as tem_utin,
               sum(rn_em_uti) as rn_em_uti
        from main_gold.ind_territorio
        where nivel = 'municipio' and ano in ({anos_sql}) and sifc_disponivel = 1
        group by cod
    )
    select i.*, m.regiao_saude_cod, m.regiao_saude_nome, m.macro_cod, m.macro_nome, m.lat, m.lon {", c.* exclude (cod7)" if tem_tipologia else ""}
    from i
    join main_gold.dim_municipio m on m.cod7 = i.cod and m.uf_cod = '{UF_COD}'
    {tip}
    """
    df = con.execute(sql).fetchdf()
    for c in df.columns:
        if c not in ("cod", "nome", "regiao_saude_cod", "regiao_saude_nome", "macro_cod", "macro_nome", "estrato", "tipo", "rotulo"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    n_anos = len(anos)
    for c in ("casos_sifc", "sifc_sem_prenatal", "sifc_trat_inadequado", "sifc_diag_materno_tardio"):
        df[c] = df[c].fillna(0)      # município sem caso notificado = zero casos (anos com SINAN carregado)
    nv = df.nv.replace(0, np.nan)
    df["nv_ano"] = df.nv / n_anos
    df["tx_sifc"] = 1000 * df.casos_sifc.fillna(0) / nv
    df["pct_sifc_sem_prenatal"] = 100 * df.sifc_sem_prenatal / df.casos_sifc.replace(0, np.nan)
    df["pct_sifc_trat_inadequado"] = 100 * df.sifc_trat_inadequado / df.casos_sifc.replace(0, np.nan)
    df["pct_cesarea"] = 100 * df.cesareas / nv
    df["pct_evasao_partos"] = 100 * df.fora / nv
    df["pct_evasao_regiao"] = 100 * df.fora_reg / nv
    df["pct_prematuro"] = 100 * df.prematuros / nv
    df["pct_baixo_peso"] = 100 * df.baixo_peso / nv
    df["pct_prenatal_adequado"] = 100 * df.prenatal_adequado / nv
    df["pct_prenatal_7mais"] = 100 * df.prenatal_7mais / nv
    df["pct_maes_menor_20"] = 100 * df.maes_menor_20 / nv
    df["pct_maes_ate_fundamental"] = 100 * df.maes_ate_fundamental / nv
    df["pct_partos_sus"] = 100 * df.partos_sus.fillna(0) / nv
    df["pct_partos_hospital_com_utin"] = 100 * df.partos_utin / nv
    df["tmnp_bruta"] = 1000 * df.obitos_neo_prec.fillna(0) / nv
    df["tmn_bruta"] = 1000 * df.obitos_neo.fillna(0) / nv
    df["rn_uti_por_1000nv"] = 1000 * df.rn_em_uti.fillna(0) / nv
    df["leitos_obst_por_1000nv"] = 1000 * df.leitos_obst.fillna(0) / df.nv_ano.replace(0, np.nan)
    df["log_nv_ano"] = np.log(df.nv_ano.clip(lower=1))
    return df


def agregar(df: pd.DataFrame, chave: str, nome: str) -> pd.DataFrame:
    g = df.groupby([chave, nome], dropna=True)
    soma = g[["nv", "casos_sifc", "cesareas", "fora", "fora_reg", "prematuros", "baixo_peso", "prenatal_adequado", "prenatal_7mais",
              "maes_menor_20", "maes_ate_fundamental", "obitos_neo", "obitos_neo_prec", "partos_sus", "partos_utin", "rn_em_uti"]].sum()
    nv = soma.nv.replace(0, np.nan)
    out = pd.DataFrame(index=soma.index)
    out["nv"] = soma.nv
    out["tx_sifc"] = 1000 * soma.casos_sifc / nv
    out["pct_cesarea"] = 100 * soma.cesareas / nv
    out["pct_evasao_partos"] = 100 * soma.fora / nv
    out["pct_evasao_regiao"] = 100 * soma.fora_reg / nv
    out["pct_prematuro"] = 100 * soma.prematuros / nv
    out["pct_baixo_peso"] = 100 * soma.baixo_peso / nv
    out["pct_prenatal_adequado"] = 100 * soma.prenatal_adequado / nv
    out["pct_prenatal_7mais"] = 100 * soma.prenatal_7mais / nv
    out["pct_maes_menor_20"] = 100 * soma.maes_menor_20 / nv
    out["pct_maes_ate_fundamental"] = 100 * soma.maes_ate_fundamental / nv
    out["pct_partos_sus"] = 100 * soma.partos_sus / nv
    out["pct_partos_hospital_com_utin"] = 100 * soma.partos_utin / nv
    out["tmnp_bruta"] = 1000 * soma.obitos_neo_prec / nv
    out["tmn_bruta"] = 1000 * soma.obitos_neo / nv
    out["rn_uti_por_1000nv"] = 1000 * soma.rn_em_uti / nv
    # médias ponderadas por NV das distâncias (quando houver tipologia)
    for v in ("dist_utin_km", "dist_cesarea_km", "dist_media_km"):
        if v in df.columns:
            w = df.assign(_w=df.nv * df[v]).groupby([chave, nome], dropna=True)["_w"].sum()
            out[v] = w / nv
    return out.reset_index()


def main() -> int:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    nv_min = int(cfg.get("nv_minimo_municipio", 200))
    variaveis: dict[str, str] = cfg.get("variaveis") or {
        "pct_prenatal_adequado": "Pré-natal adequado (Kotelchuck), %",
        "pct_prenatal_7mais": "Pré-natal com 7+ consultas, %",
        "pct_maes_menor_20": "Mães com menos de 20 anos, %",
        "pct_maes_ate_fundamental": "Mães com até o fundamental, %",
        "pct_partos_sus": "Partos SUS / NV, %",
        "pct_cesarea": "Cesáreas, %",
        "pct_evasao_partos": "Partos fora do município, %",
        "pct_evasao_regiao": "Partos fora da região, %",
        "pct_partos_hospital_com_utin": "Partos em hospital com UTIN, %",
        "dist_utin_km": "Distância à UTI neonatal, km",
        "dist_cesarea_km": "Distância ao município com cesárea, km",
        "dist_media_km": "Distância média percorrida, km",
        "leitos_obst_por_1000nv": "Leitos obstétricos por mil NV",
        "pct_prematuro": "Prematuridade, %",
        "pct_baixo_peso": "Baixo peso, %",
        "tmnp_bruta": "Mortalidade neonatal precoce, por mil NV",
        "tmn_bruta": "Mortalidade neonatal, por mil NV",
        "rn_uti_por_1000nv": "RN internados em UTI (SIH), por mil NV",
        "log_nv_ano": "Nascidos vivos por ano (log)",
    }
    con = duckdb.connect(DB)
    anos = [int(r[0]) for r in con.execute("select distinct ano from main_gold.ind_territorio where nivel='municipio' and sifc_disponivel = 1 order by 1").fetchall()]
    if not anos:
        log("nenhum ano com SINAN (sífilis congênita) carregado; rode ingest com SINAN_SIFC em BASES e build")
        return 1
    tem_tip = bool(con.execute("select 1 from information_schema.tables where table_schema='main_analise' and table_name='cluster_municipios'").fetchall())
    log(f"anos com sífilis congênita: {anos}; tipologia {'disponível' if tem_tip else 'ausente (rode cluster antes para a análise por tipo)'}")
    df = base_municipal(con, anos, tem_tip)
    tot_nv, tot_casos = float(df.nv.sum()), float(df.casos_sifc.fillna(0).sum())
    tx_uf = 1000 * tot_casos / tot_nv
    log(f"{len(df)} municípios; {int(tot_casos)} casos / {int(tot_nv)} NV = {tx_uf:.2f} por mil NV")

    # ---- correlações por nível
    linhas = []
    niveis = [("municipio", df[df.nv >= nv_min].copy(), f"municípios com ≥ {nv_min} NV no período"),
              ("regiao_saude", agregar(df, "regiao_saude_cod", "regiao_saude_nome"), "regiões de saúde"),
              ("macrorregiao", agregar(df, "macro_cod", "macro_nome"), "macrorregiões")]
    for nivel, base, descr in niveis:
        for v, rot in variaveis.items():
            if v not in base.columns:
                continue
            r = spearman_ic(base[v], base["tx_sifc"])
            linhas.append({"nivel": nivel, "descricao": descr, "variavel": v, "rotulo": rot, **r})
    corr = pd.DataFrame(linhas)
    corr["ordem_abs"] = corr.rho.abs()
    corr = corr.sort_values(["nivel", "ordem_abs"], ascending=[True, False]).drop(columns="ordem_abs").reset_index(drop=True)

    # ---- por tipo de município
    por_tipo = pd.DataFrame()
    if tem_tip and "tipo" in df.columns:
        g = df.groupby(["cluster", "tipo", "rotulo", "estrato"], dropna=True)
        por_tipo = g.agg(n_municipios=("cod", "size"), nv=("nv", "sum"), casos=("casos_sifc", "sum"),
                         pct_prenatal_adequado=("pct_prenatal_adequado", "mean")).reset_index()
        por_tipo["tx_sifc"] = 1000 * por_tipo.casos / por_tipo.nv
        ics = [ic_poisson(c, n) for c, n in zip(por_tipo.casos, por_tipo.nv)]
        por_tipo["ic_inf"] = [a for a, _ in ics]
        por_tipo["ic_sup"] = [b for _, b in ics]
        por_tipo["razao_uf"] = por_tipo.tx_sifc / tx_uf
        # municípios sem nenhum caso, por tipo (sinal de subnotificação ou baixa incidência)
        zero = df.assign(z=(df.casos_sifc.fillna(0) == 0).astype(int)).groupby("cluster").z.mean() * 100
        por_tipo["pct_municipios_sem_caso"] = por_tipo.cluster.map(zero)
        por_tipo = por_tipo.sort_values("cluster").reset_index(drop=True)

    # ---- regressão: quase-Poisson, casos ~ covariáveis + offset ln(NV)
    reg = pd.DataFrame()
    try:
        import statsmodels.api as sm
        covs = [c for c in (cfg.get("regressao_covariaveis") or ["pct_prenatal_adequado", "pct_maes_menor_20", "pct_maes_ate_fundamental",
                                                                  "pct_partos_sus", "pct_evasao_regiao", "dist_utin_km", "log_nv_ano"]) if c in df.columns]
        base = df[df.nv >= nv_min].dropna(subset=covs + ["casos_sifc", "nv"]).copy()
        X = base[covs].astype(float)
        # padroniza por desvio-padrão para IRR "por 1 DP" (comparável entre variáveis), e também em unidades naturais
        escala = X.std(ddof=0).replace(0, 1)
        Xz = (X - X.mean()) / escala
        Xz = sm.add_constant(Xz)
        modelo = sm.GLM(base.casos_sifc.fillna(0).astype(float), Xz, family=sm.families.Poisson(), offset=np.log(base.nv.astype(float)))
        res = modelo.fit()
        # correção de sobredispersão (quase-Poisson): erros-padrão × raiz(phi)
        phi = float(res.pearson_chi2 / res.df_resid) if res.df_resid > 0 else 1.0
        se = res.bse * math.sqrt(max(phi, 1.0))
        reg = pd.DataFrame({"variavel": Xz.columns, "rotulo": [variaveis.get(v, v) if v != "const" else "intercepto" for v in Xz.columns],
                            "coef_por_dp": res.params.values, "irr_por_dp": np.exp(res.params.values),
                            "irr_ic_inf": np.exp(res.params.values - 1.96 * se.values), "irr_ic_sup": np.exp(res.params.values + 1.96 * se.values),
                            "p": 2 * (1 - stats.norm.cdf(np.abs(res.params.values / se.values))),
                            "dp_variavel": [np.nan] + [float(escala[v]) for v in covs]})
        reg = reg[reg.variavel != "const"].reset_index(drop=True)
        reg_meta = {"n": int(len(base)), "phi_sobredispersao": round(phi, 2), "covariaveis": covs, "deviance": float(res.deviance)}
        log(f"regressão quase-Poisson: n={len(base)}, phi={phi:.2f}")
    except Exception as e:  # noqa: BLE001
        reg_meta = {"erro": str(e)[:200]}
        log(f"regressão não executada ({e})")

    # ---- grava
    con.execute("create schema if not exists main_analise")
    con.register("df_m", df); con.execute("create or replace table main_analise.sifc_municipios as select * from df_m")
    con.register("df_c", corr); con.execute("create or replace table main_analise.sifc_correlacoes as select * from df_c")
    if len(por_tipo):
        con.register("df_t", por_tipo); con.execute("create or replace table main_analise.sifc_por_tipo as select * from df_t")
    if len(reg):
        con.register("df_r", reg); con.execute("create or replace table main_analise.sifc_regressao as select * from df_r")
    meta = {"gerado_em": datetime.now(timezone.utc).isoformat(), "anos": anos, "nv_minimo_municipio": nv_min, "n_municipios": int(len(df)),
            "casos": int(tot_casos), "nv": int(tot_nv), "tx_uf": round(tx_uf, 3), "variaveis": variaveis, "regressao": reg_meta,
            "pct_sem_prenatal_uf": round(100 * float(df.sifc_sem_prenatal.sum()) / max(tot_casos, 1), 1),
            "pct_trat_inadequado_uf": round(100 * float(df.sifc_trat_inadequado.sum()) / max(tot_casos, 1), 1),
            "pct_diag_tardio_uf": round(100 * float(df.sifc_diag_materno_tardio.sum()) / max(tot_casos, 1), 1)}
    con.execute("create or replace table main_analise.sifc_meta as select ?::json as meta", [json.dumps(meta, ensure_ascii=False)])
    con.close()
    EXPORT.mkdir(parents=True, exist_ok=True)
    df.to_csv(EXPORT / "sifc_municipios.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    corr.to_csv(EXPORT / "sifc_correlacoes.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    if len(por_tipo):
        por_tipo.to_csv(EXPORT / "sifc_por_tipo.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    if len(reg):
        reg.to_csv(EXPORT / "sifc_regressao.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    (EXPORT / "sifc_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    pd.set_option("display.width", 220)
    log("correlações de Spearman com a taxa de sífilis congênita (municípios):")
    print(corr[corr.nivel == "municipio"][["rotulo", "n", "rho", "ic_inf", "ic_sup", "p"]].round(3).to_string(index=False))
    if len(por_tipo):
        log("por tipo de município:")
        print(por_tipo[["tipo", "n_municipios", "nv", "casos", "tx_sifc", "ic_inf", "ic_sup", "pct_municipios_sem_caso"]].round(2).to_string(index=False))
    if len(reg):
        log("quase-Poisson (IRR por 1 desvio-padrão da covariável):")
        print(reg[["rotulo", "irr_por_dp", "irr_ic_inf", "irr_ic_sup", "p"]].round(3).to_string(index=False))
    log(f"gravado: main_analise.sifc_* e {EXPORT}/sifc_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
