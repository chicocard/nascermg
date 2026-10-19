#!/usr/bin/env python3
"""
Análise espacial bayesiana dos desfechos (sífilis congênita, mortalidade neonatal, neonatal precoce e materna)
por município e região de saúde: risco relativo suavizado, probabilidade de excesso de risco e áreas quentes.

Método
  1. Observados O_i (SIM/SINAN, por residência) e esperados E_i = NV_i × taxa estadual (padronização indireta).
  2. SMR = O/E com IC 95% (Byar); Bayes empírico Poisson-gama (método dos momentos) como referência sem espaço.
  3. Modelo BYM2 (Riebler et al., 2016; implementação segundo Morris et al., 2019):
         O_i ~ Poisson(E_i · exp(β0 + b_i)),  b = σ (√(ρ/s) φ + √(1−ρ) θ),
         φ ~ ICAR(W) (efeito espacial estruturado), θ ~ N(0,1) (não estruturado), s = fator de escala do grafo,
         ρ ~ Beta(0,5; 0,5), σ ~ HalfNormal(1), β0 ~ N(0, 2).  Amostragem NUTS (PyMC + nutpie).
     RR_i = exp(β0 + b_i); P_i = P(RR_i > 1 | dados) = probabilidade de excesso de risco.
  4. Classes: quente forte (P ≥ 0,95), quente (P ≥ 0,80), fria (P ≤ 0,20), indefinida (Richardson et al., 2004).
  5. Moran global (permutação) e LISA (Anselin, 1995) sobre o log do risco com suavização não espacial (EB),
     para não medir a autocorrelação que o próprio BYM2 induz: aglomerados alto-alto / baixo-baixo.

Vizinhança: polígonos do IBGE (contiguidade rainha), ou triangulação de Delaunay das sedes podada, ou k vizinhos.
Regiões de saúde: vizinhas se algum par de municípios é vizinho. Grafo tornado conexo (arestas mínimas).

Grava main_analise.espacial, espacial_meta e /data/export/espacial_*.csv.
Uso: python espacial.py [espacial_config.yml]   (variáveis: DUCKDB_PATH, UF, UF_COD, EXPORT_DIR, APP_DATA)
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml
from scipy import stats
from scipy.sparse.csgraph import connected_components
from scipy.spatial import Delaunay
from scipy.spatial.distance import cdist

DB = os.environ.get("DUCKDB_PATH", "/data/warehouse/nascer.duckdb")
UF = os.environ.get("UF", "MG").upper()
UF_CODIGOS = {"RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17", "MA": "21", "PI": "22", "CE": "23",
              "RN": "24", "PB": "25", "PE": "26", "AL": "27", "SE": "28", "BA": "29", "MG": "31", "ES": "32", "RJ": "33", "SP": "35",
              "PR": "41", "SC": "42", "RS": "43", "MS": "50", "MT": "51", "GO": "52", "DF": "53"}
UF_COD = os.environ.get("UF_COD") or UF_CODIGOS.get(UF, "31")
EXPORT = Path(os.environ.get("EXPORT_DIR", "/data/export"))
APP_DATA = Path(os.environ.get("APP_DATA", "/app/public/data"))
CONFIG = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("espacial_config.yml"))
MALHA_URL = (f"https://servicodados.ibge.gov.br/api/v3/malhas/estados/{UF_COD}"
             "?formato=application/vnd.geo+json&intrarregiao=municipio&qualidade=minima")


def log(m: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


# ------------------------------------------------------------------ vizinhança
def malha_geojson() -> dict | None:
    p = APP_DATA / f"malha_{UF.lower()}.geojson"
    if p.exists() and p.stat().st_size > 10000:
        return json.loads(p.read_text(encoding="utf-8"))
    try:
        log("IBGE: baixando malha municipal para a vizinhança por polígonos")
        req = urllib.request.Request(MALHA_URL, headers={"User-Agent": "nascer-pipeline/0.1"})
        with urllib.request.urlopen(req, timeout=180) as r:
            gj = json.loads(r.read())
        for f in gj.get("features", []):
            f["properties"] = {"cod7": str(f.get("properties", {}).get("codarea"))}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(gj, separators=(",", ":")), encoding="utf-8")
        return gj
    except Exception as e:  # noqa: BLE001
        log(f"  malha não disponível ({str(e)[:80]}); vizinhança pela triangulação das sedes")
        return None


def projetar(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    x = np.radians(lon) * np.cos(np.radians(np.nanmean(lat))) * 6371.0
    y = np.radians(lat) * 6371.0
    return np.c_[x, y]


def vizinhanca_poligonos(cods: list[str], gj: dict) -> np.ndarray | None:
    try:
        from shapely.geometry import shape
        from shapely.strtree import STRtree
    except ImportError:
        log("  shapely não instalado; vizinhança pela triangulação das sedes")
        return None
    geoms = {}
    for f in gj.get("features", []):
        c = str(f.get("properties", {}).get("cod7"))
        try:
            geoms[c] = shape(f["geometry"]).buffer(0)
        except Exception:  # noqa: BLE001
            pass
    faltam = [c for c in cods if c not in geoms]
    if len(faltam) > 0.05 * len(cods):
        log(f"  {len(faltam)} municípios sem polígono; vizinhança pela triangulação das sedes")
        return None
    idx = {c: i for i, c in enumerate(cods)}
    gl = [geoms.get(c) for c in cods]
    n = len(cods)
    W = np.zeros((n, n))
    # tolerância de ~50 m: malhas simplificadas (TabWin, IBGE "mínima") deixam frestas entre vizinhos reais
    tol = 0.0005
    gb = [g.buffer(tol) if g is not None else None for g in gl]
    arvore = STRtree([g for g in gb if g is not None])
    pos = [i for i, g in enumerate(gb) if g is not None]
    for i in pos:
        g = gb[i]
        for k in arvore.query(g, predicate="intersects"):
            j = pos[k]
            if j != i and g.intersection(gb[j]).area > 0:
                W[i, j] = W[j, i] = 1
    return W


def vizinhanca_delaunay(pts: np.ndarray, poda: float) -> np.ndarray:
    n = len(pts)
    W = np.zeros((n, n))
    if n < 4:
        W[:] = 1; np.fill_diagonal(W, 0); return W
    tri = Delaunay(pts)
    arestas = set()
    for s in tri.simplices:
        for a, b in ((0, 1), (1, 2), (0, 2)):
            i, j = sorted((int(s[a]), int(s[b])))
            arestas.add((i, j))
    comp = {e: float(np.hypot(*(pts[e[0]] - pts[e[1]]))) for e in arestas}
    lim = poda * float(np.median(list(comp.values())))
    for (i, j), d in comp.items():
        if d <= lim:
            W[i, j] = W[j, i] = 1
    return W


def vizinhanca_knn(pts: np.ndarray, k: int) -> np.ndarray:
    n = len(pts); D = cdist(pts, pts); W = np.zeros((n, n))
    for i in range(n):
        for j in np.argsort(D[i])[1:k + 1]:
            W[i, j] = W[j, i] = 1
    return W


def tornar_conexo(W: np.ndarray, pts: np.ndarray) -> np.ndarray:
    D = cdist(pts, pts)
    for i in np.where(W.sum(1) == 0)[0]:
        j = int(np.argsort(D[i])[1]); W[i, j] = W[j, i] = 1
    nc, lab = connected_components(W, directed=False)
    while nc > 1:
        melhor = None
        for a in range(nc):
            ia = np.where(lab == a)[0]; ib = np.where(lab != a)[0]
            sub = D[np.ix_(ia, ib)]; k = np.unravel_index(int(sub.argmin()), sub.shape)
            if melhor is None or sub[k] < melhor[0]:
                melhor = (sub[k], int(ia[k[0]]), int(ib[k[1]]))
        W[melhor[1], melhor[2]] = W[melhor[2], melhor[1]] = 1
        nc, lab = connected_components(W, directed=False)
    return W


def fator_escala(W: np.ndarray) -> float:
    """Fator de escala do ICAR (Riebler et al., 2016): variância marginal generalizada do grafo."""
    n = len(W)
    Q = np.diag(W.sum(1)) - W
    Qp = Q + np.eye(n) * Q.diagonal().max() * math.sqrt(np.finfo(float).eps)
    Qi = np.linalg.inv(Qp)
    um = np.ones((n, 1))
    Qc = Qi - Qi @ um @ np.linalg.inv(um.T @ Qi @ um) @ um.T @ Qi
    return float(np.exp(np.mean(np.log(np.diag(Qc)))))


# ------------------------------------------------------------------ estatística
def ic_byar(o: float, e: float) -> tuple[float, float]:
    if e <= 0:
        return (float("nan"), float("nan"))
    d = max(o, 0.0)
    lo = d * (1 - 1 / (9 * d) - 1.96 / (3 * math.sqrt(d))) ** 3 if d > 0 else 0.0
    hi = (d + 1) * (1 - 1 / (9 * (d + 1)) + 1.96 / (3 * math.sqrt(d + 1))) ** 3
    return (lo / e, hi / e)


def eb_poisson_gama(O: np.ndarray, E: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Bayes empírico global (Clayton & Kaldor, 1987, método dos momentos): RR_i ~ Gama(a, b) a priori;
    a posteriori Gama(a + O_i, b + E_i). Devolve média a posteriori e P(RR > 1)."""
    smr = np.where(E > 0, O / np.maximum(E, 1e-9), np.nan)
    m = float(np.nansum(O) / np.nansum(E))
    w = E / np.nansum(E)
    var_smr = float(np.nansum(w * (smr - m) ** 2))
    var_prior = max(var_smr - m / float(np.nanmean(E)), 1e-6)
    a = m ** 2 / var_prior; b = m / var_prior
    a_post = a + O; b_post = b + E
    rr = a_post / b_post
    p = 1 - stats.gamma.cdf(1.0, a_post, scale=1 / b_post)
    return rr, p, {"a_prior": round(a, 4), "b_prior": round(b, 4), "media": round(m, 4), "var_prior": round(var_prior, 6)}


def bym2(O: np.ndarray, E: np.ndarray, W: np.ndarray, cfg: dict) -> dict | None:
    try:
        import arviz as az
        import nutpie
        import pymc as pm
    except ImportError as e:
        log(f"  PyMC/nutpie indisponível ({e}); usando Bayes empírico")
        return None
    n = len(O)
    sf = fator_escala(W)
    with pm.Model() as m:
        b0 = pm.Normal("b0", 0, 2)
        sigma = pm.HalfNormal("sigma", 1)
        rho = pm.Beta("rho", 0.5, 0.5)
        phi = pm.ICAR("phi", W=W, sigma=1)
        theta = pm.Normal("theta", 0, 1, shape=n)
        b = pm.Deterministic("b", sigma * (pm.math.sqrt(rho / sf) * phi + pm.math.sqrt(1 - rho) * theta))
        pm.Poisson("O", mu=E * pm.math.exp(b0 + b), observed=O)
    compilado = nutpie.compile_pymc_model(m, backend="numba")
    tr = nutpie.sample(compilado, draws=int(cfg.get("amostras", 1000)), tune=int(cfg.get("aquecimento", 1000)),
                       chains=int(cfg.get("cadeias", 2)), cores=int(cfg.get("cadeias", 2)), seed=int(cfg.get("semente", 42)),
                       progress_bar=False)
    post = tr.posterior
    lt = (post["b0"].values[..., None] + post["b"].values).reshape(-1, n)
    rr = np.exp(lt)
    resumo = az.summary(tr, var_names=["b0", "sigma", "rho"])
    diag = {"fator_escala": round(sf, 4), "divergencias": int(tr.sample_stats["diverging"].sum()),
            "rhat_max": float(resumo["r_hat"].max()), "ess_min": float(resumo["ess_bulk"].min()),
            "sigma": float(resumo.loc["sigma", "mean"]), "rho": float(resumo.loc["rho", "mean"]), "b0": float(resumo.loc["b0", "mean"])}
    return {"rr": rr.mean(0), "inf": np.percentile(rr, 2.5, 0), "sup": np.percentile(rr, 97.5, 0), "prob": (rr > 1).mean(0), "diag": diag}


def moran_global(x: np.ndarray, W: np.ndarray, perm: int, semente: int) -> dict:
    z = x - x.mean(); s0 = W.sum()
    I = float(len(x) / s0 * (z @ W @ z) / (z @ z))
    rng = np.random.default_rng(semente)
    sims = np.array([(len(x) / s0) * (zp @ W @ zp) / (zp @ zp) for zp in (rng.permutation(z) for _ in range(perm))])
    p = (np.sum(sims >= I) + 1) / (perm + 1) if I >= sims.mean() else (np.sum(sims <= I) + 1) / (perm + 1)
    return {"I": round(I, 4), "p": round(float(p), 4), "esperado": round(-1 / (len(x) - 1), 4)}


def lisa(x: np.ndarray, W: np.ndarray, perm: int, alfa: float, semente: int) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Moran local (Anselin, 1995) com permutação condicional; devolve I_i, p_i e categoria."""
    n = len(x); z = (x - x.mean()) / (x.std(ddof=0) or 1.0)
    Wr = W / np.maximum(W.sum(1, keepdims=True), 1)   # padronizada por linha
    lag = Wr @ z
    Ii = z * lag
    rng = np.random.default_rng(semente)
    p = np.ones(n)
    viz = [np.where(W[i] > 0)[0] for i in range(n)]
    for i in range(n):
        k = len(viz[i])
        if k == 0:
            continue
        outros = np.delete(z, i)
        amostras = rng.choice(outros, size=(perm, k), replace=True)
        sims = z[i] * amostras.mean(1)
        p[i] = (np.sum(np.abs(sims) >= abs(Ii[i])) + 1) / (perm + 1)
    cat = []
    for i in range(n):
        if p[i] > alfa or len(viz[i]) == 0:
            cat.append("ns")
        elif z[i] > 0 and lag[i] > 0: cat.append("alto-alto")
        elif z[i] < 0 and lag[i] < 0: cat.append("baixo-baixo")
        elif z[i] > 0: cat.append("alto-baixo")
        else: cat.append("baixo-alto")
    return Ii, p, cat


# ------------------------------------------------------------------ dados
def carregar(con, cfg) -> tuple[pd.DataFrame, list[int], list[int]]:
    anos = [int(r[0]) for r in con.execute("select distinct ano from main_gold.ind_territorio where nivel='municipio' order by 1").fetchall()]
    anos_sinan = [int(r[0]) for r in con.execute("select distinct ano from main_gold.ind_territorio where nivel='municipio' and sifc_disponivel=1 order by 1").fetchall()]
    nums = {k: v["numerador"] for k, v in cfg["desfechos"].items()}
    cols = []
    for k, v in cfg["desfechos"].items():
        if str(v.get("so_anos_sinan", "")).lower() in ("sim", "true", "1", "s"):
            cols.append(f"sum(case when i.sifc_disponivel = 1 then i.{v['numerador']} end) as o_{k}, sum(case when i.sifc_disponivel = 1 then i.nascidos_vivos end) as nv_{k}")
        else:
            cols.append(f"sum(i.{v['numerador']}) as o_{k}, sum(i.nascidos_vivos) as nv_{k}")
    sql = f"""
    select m.cod7, m.nome, m.lat, m.lon, m.regiao_saude_cod, m.regiao_saude_nome, m.macro_cod, m.macro_nome, sum(i.nascidos_vivos) as nv,
           {", ".join(cols)}
    from main_gold.dim_municipio m
    join main_gold.ind_territorio i on i.cod = m.cod7 and i.nivel = 'municipio'
    where m.uf_cod = '{UF_COD}' and m.lat is not null
    group by 1,2,3,4,5,6,7,8 order by 1"""
    df = con.execute(sql).fetchdf()
    for c in df.columns:
        if c.startswith(("o_", "nv_")) or c == "nv":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(float)
    return df, anos, anos_sinan


def agregar_regioes(df: pd.DataFrame, W: np.ndarray, chave: str, nome: str) -> tuple[pd.DataFrame, np.ndarray]:
    g = df.groupby([chave, nome], dropna=True)
    agg = g[[c for c in df.columns if c.startswith(("o_", "nv_")) or c == "nv"]].sum().reset_index()
    agg["lat"] = g.lat.mean().values; agg["lon"] = g.lon.mean().values
    cods = agg[chave].tolist(); idx = {c: i for i, c in enumerate(cods)}
    n = len(cods); Wr = np.zeros((n, n))
    mi = df[chave].map(idx).to_numpy()
    for i, j in zip(*np.where(np.triu(W) > 0)):
        a, b = mi[i], mi[j]
        if not (np.isnan(a) or np.isnan(b)) and a != b:
            Wr[int(a), int(b)] = Wr[int(b), int(a)] = 1
    return agg.rename(columns={chave: "cod", nome: "nome"}), Wr


# ------------------------------------------------------------------ principal
def analisar_nivel(nivel: str, base: pd.DataFrame, W: np.ndarray, cfg: dict, usar_bym: bool) -> tuple[list[dict], dict]:
    linhas, meta = [], {}
    lq, lf, lfr = float(cfg.get("limiar_quente", 0.8)), float(cfg.get("limiar_forte", 0.95)), float(cfg.get("limiar_fria", 0.2))
    for k, v in cfg["desfechos"].items():
        O = base[f"o_{k}"].to_numpy(float); NV = base[f"nv_{k}"].to_numpy(float)
        if O.sum() <= 0 or NV.sum() <= 0:
            log(f"  {nivel}/{k}: sem casos ou sem denominador; pulado"); continue
        taxa = O.sum() / NV.sum()
        E = np.maximum(NV * taxa, 1e-6)
        smr = O / E
        ic = [ic_byar(o, e) for o, e in zip(O, E)]
        rr_eb, p_eb, info_eb = eb_poisson_gama(O, E)
        res = bym2(O, E, W, cfg) if usar_bym else None
        if res is None:
            rr, inf, sup, prob = rr_eb, np.full(len(O), np.nan), np.full(len(O), np.nan), p_eb
            modelo = "eb"; diag = info_eb
        else:
            rr, inf, sup, prob = res["rr"], res["inf"], res["sup"], res["prob"]
            modelo = "bym2"; diag = res["diag"]
        classe = np.where(prob >= lf, "quente forte", np.where(prob >= lq, "quente", np.where(prob <= lfr, "fria", "indefinida")))
        # autocorrelação medida no risco com suavização NÃO espacial (Bayes empírico): a suavização espacial do
        # BYM2 induziria autocorrelação por construção. Com menos de 30 unidades o Moran não é informativo.
        x_ac = np.log(np.maximum(rr_eb, 1e-6))
        if len(O) >= 30:
            mg = moran_global(x_ac, W, int(cfg.get("permutacoes", 999)), int(cfg.get("semente", 42)))
            Ii, pl, cat = lisa(x_ac, W, int(cfg.get("permutacoes", 999)), float(cfg.get("alfa_lisa", 0.05)), int(cfg.get("semente", 42)))
        else:
            mg = {"I": None, "p": None, "esperado": None}; Ii = np.zeros(len(O)); pl = np.ones(len(O)); cat = ["ns"] * len(O)
        for i in range(len(O)):
            linhas.append({"nivel": nivel, "desfecho": k, "cod": str(base.cod.iloc[i]), "nome": base.nome.iloc[i],
                           "nv": NV[i], "obs": O[i], "esp": round(E[i], 3), "smr": round(smr[i], 3), "smr_inf": round(ic[i][0], 3), "smr_sup": round(ic[i][1], 3),
                           "rr_eb": round(float(rr_eb[i]), 3), "prob_eb": round(float(p_eb[i]), 3),
                           "rr": round(float(rr[i]), 3), "rr_inf": None if np.isnan(inf[i]) else round(float(inf[i]), 3),
                           "rr_sup": None if np.isnan(sup[i]) else round(float(sup[i]), 3),
                           "prob": round(float(prob[i]), 3), "classe": str(classe[i]), "lisa": cat[i], "lisa_i": round(float(Ii[i]), 3), "lisa_p": round(float(pl[i]), 3),
                           "taxa_por_1000": round(1000 * O[i] / NV[i], 2) if NV[i] > 0 else None})
        n_q = int((classe == "quente").sum() + (classe == "quente forte").sum())
        meta[k] = {"rotulo": v.get("rotulo", k), "modelo": modelo, "n": int(len(O)), "obs": int(O.sum()), "nv": int(NV.sum()),
                   "taxa_por_1000": round(1000 * taxa, 3), "quentes": n_q, "quentes_fortes": int((classe == "quente forte").sum()),
                   "frias": int((classe == "fria").sum()), "alto_alto": int(sum(1 for c in cat if c == "alto-alto")),
                   "moran": mg, "diagnostico": {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in diag.items()}}
        log(f"  {nivel}/{k}: {modelo}; {int(O.sum())} casos; quentes {n_q} (fortes {meta[k]['quentes_fortes']}); Moran I {mg['I']} (p {mg['p']}); alto-alto {meta[k]['alto_alto']}")
    return linhas, meta


def main() -> int:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    con = duckdb.connect(DB)
    df, anos, anos_sinan = carregar(con, cfg)
    log(f"{len(df)} municípios; anos {anos}; anos com SINAN {anos_sinan}")
    pts = projetar(df.lat.to_numpy(), df.lon.to_numpy())
    metodo = str(cfg.get("vizinhanca", "poligonos"))
    W = None
    if metodo == "poligonos":
        gj = malha_geojson()
        if gj is not None:
            W = vizinhanca_poligonos(df.cod7.tolist(), gj)
            if W is not None:
                metodo_usado = "poligonos (contiguidade rainha, malha IBGE)"
    if W is None and metodo == "knn":
        W = vizinhanca_knn(pts, int(cfg.get("k_vizinhos", 6))); metodo_usado = f"knn k={cfg.get('k_vizinhos', 6)}"
    if W is None:
        W = vizinhanca_delaunay(pts, float(cfg.get("poda_x_mediana", 3))); metodo_usado = "delaunay podada (sedes)"
    W = tornar_conexo(W, pts)
    log(f"vizinhança: {metodo_usado}; {int(W.sum() / 2)} pares; média {W.sum(1).mean():.1f} vizinhos")

    usar_bym = str(cfg.get("modelo", "bym2")).lower() == "bym2"
    df_m = df.rename(columns={"cod7": "cod"})
    todas, meta = [], {"gerado_em": datetime.now(timezone.utc).isoformat(), "uf": UF, "anos": anos, "anos_sinan": anos_sinan,
                       "vizinhanca": metodo_usado, "modelo": "bym2" if usar_bym else "eb", "limiares": {"quente": cfg.get("limiar_quente", 0.8), "forte": cfg.get("limiar_forte", 0.95), "fria": cfg.get("limiar_fria", 0.2)},
                       "niveis": {}}
    niveis = cfg.get("niveis", ["municipio", "regiao_saude"])
    if "municipio" in niveis:
        log("nível município")
        l, m = analisar_nivel("municipio", df_m, W, cfg, usar_bym); todas += l; meta["niveis"]["municipio"] = m
    if "regiao_saude" in niveis and df.regiao_saude_cod.notna().any():
        log("nível região de saúde")
        reg, Wr = agregar_regioes(df, W, "regiao_saude_cod", "regiao_saude_nome")
        Wr = tornar_conexo(Wr, projetar(reg.lat.to_numpy(), reg.lon.to_numpy()))
        l, m = analisar_nivel("regiao_saude", reg, Wr, cfg, usar_bym and len(reg) >= 20); todas += l; meta["niveis"]["regiao_saude"] = m
    if df.macro_cod.notna().any():
        log("nível macrorregião (sem suavização espacial)")
        mac, Wm = agregar_regioes(df, W, "macro_cod", "macro_nome")
        Wm = tornar_conexo(Wm, projetar(mac.lat.to_numpy(), mac.lon.to_numpy()))
        l, m = analisar_nivel("macrorregiao", mac, Wm, cfg, False); todas += l; meta["niveis"]["macrorregiao"] = m

    out = pd.DataFrame(todas)
    con.execute("create schema if not exists main_analise")
    con.register("esp_df", out)
    con.execute("create or replace table main_analise.espacial as select * from esp_df")
    con.execute("create or replace table main_analise.espacial_meta as select ?::json as meta", [json.dumps(meta, ensure_ascii=False)])
    con.close()
    EXPORT.mkdir(parents=True, exist_ok=True)
    out.to_csv(EXPORT / "espacial.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    (EXPORT / "espacial_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.set_option("display.width", 220)
    for nivel in out.nivel.unique():
        for k in out.desfecho.unique():
            sub = out[(out.nivel == nivel) & (out.desfecho == k) & (out.classe.isin(["quente", "quente forte"]))].sort_values("prob", ascending=False)
            if len(sub):
                log(f"{nivel}/{k}: áreas quentes ({len(sub)}): " + "; ".join(f"{r.nome} RR {r.rr:.2f} P {r.prob:.2f}" for r in sub.head(8).itertuples()))
    log(f"gravado: main_analise.espacial / espacial_meta e {EXPORT}/espacial.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
