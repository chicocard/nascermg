#!/usr/bin/env python3
"""
Converte mapas para o GeoJSON que o app e a análise espacial usam (app/public/data/):
  malha_<uf>.geojson          municípios   (propriedade cod7 = código IBGE de 7 dígitos)
  malha_regsaud_<uf>.geojson  regiões de saúde (cod = código DATASUS de 5 dígitos)
  malha_macsaud_<uf>.geojson  macrorregiões de saúde (cod = 4 dígitos)

Fontes aceitas na pasta MALHA_DIR (padrão /malhas):
  * mapas do TabWin/DATASUS (.MAP): <uf>_municip.MAP, <uf>_regsaud.MAP, <uf>_macsaud.MAP — formato binário lido aqui
    sem dependências (cabeçalho: versão uint16, caixa 4×float32; registros: tamanho do código uint16 BE, código
    10 bytes, nome Pascal de 25 chars, ponto do rótulo 2×float32, nº de vértices uint16, vértices 2×float32);
  * shapefile do IBGE (.shp + .dbf, campo CD_MUN ou CD_GEOCMU) para municípios — precisa de pyshp;
  * GeoJSON já pronto (.geojson/.json) com propriedade codarea, CD_MUN ou cod7.
Se nada for encontrado, mantém o que existir em app/public/data (ou a malha baixada do IBGE pelo "exportar").

Uso: python malha.py   (variáveis: MALHA_DIR, APP_DATA, UF, DATA_DIR)
"""
from __future__ import annotations

import json
import os
import struct
import sys
from datetime import datetime
from pathlib import Path

MALHA_DIR = Path(os.environ.get("MALHA_DIR", "/malhas"))
APP_DATA = Path(os.environ.get("APP_DATA", "/app/public/data"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UF = os.environ.get("UF", "MG").upper()
UF_CODIGOS = {"RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17", "MA": "21", "PI": "22", "CE": "23",
              "RN": "24", "PB": "25", "PE": "26", "AL": "27", "SE": "28", "BA": "29", "MG": "31", "ES": "32", "RJ": "33", "SP": "35",
              "PR": "41", "SC": "42", "RS": "43", "MS": "50", "MT": "51", "GO": "52", "DF": "53"}
UF_COD = os.environ.get("UF_COD") or UF_CODIGOS.get(UF, "31")
DEC = 5   # casas decimais nas coordenadas (~1 m)


def log(m: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


def cod6_para_cod7() -> dict[str, str]:
    """Mapa código IBGE de 6 dígitos -> 7 dígitos, a partir de bronze/territorio/municipios_ibge.parquet."""
    p = DATA_DIR / "bronze" / "territorio" / "municipios_ibge.parquet"
    if not p.exists():
        log(f"  {p} não existe (rode 'territorio'); os municípios ficarão sem o 7º dígito")
        return {}
    import pyarrow.parquet as pq
    t = pq.read_table(p, columns=["cod6", "cod7"]).to_pylist()
    return {str(r["cod6"]): str(r["cod7"]) for r in t}


def ler_map(path: Path) -> list[dict]:
    """Lê um .MAP do TabWin. Devolve [{codigo, nome, rotulo:(x,y), anel:[(x,y),...]}]."""
    b = path.read_bytes()
    n = len(b)
    off = 18    # versão (2) + caixa (16)
    recs = []
    while off + 2 <= n:
        off += 2                                            # tamanho significativo do código (uint16 BE)
        codigo = b[off:off + 10].split(b"\x00")[0].decode("latin-1").strip(); off += 10
        ln = b[off]; nome = b[off + 1:off + 1 + ln].decode("latin-1", "replace").strip(); off += 26
        lx, ly = struct.unpack("<2f", b[off:off + 8]); off += 8
        npts, = struct.unpack("<H", b[off:off + 2]); off += 2
        pts = struct.unpack(f"<{2 * npts}f", b[off:off + 8 * npts]); off += 8 * npts
        anel = [(round(x, DEC), round(y, DEC)) for x, y in zip(pts[0::2], pts[1::2])]
        if anel and anel[0] != anel[-1]:
            anel.append(anel[0])
        recs.append({"codigo": codigo, "nome": nome, "rotulo": (lx, ly), "anel": anel})
    if off != n:
        log(f"  aviso: {path.name} lido até {off} de {n} bytes")
    return recs


def feature(props: dict, aneis: list[list[tuple[float, float]]]) -> dict:
    geom = {"type": "Polygon", "coordinates": [[list(p) for p in a] for a in aneis]} if len(aneis) == 1 else \
           {"type": "MultiPolygon", "coordinates": [[[list(p) for p in a]] for a in aneis]}
    return {"type": "Feature", "properties": props, "geometry": geom}


def gravar(nome: str, feats: list[dict]) -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    p = APP_DATA / nome
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")), encoding="utf-8")
    log(f"  {p.name}: {len(feats)} polígonos, {p.stat().st_size / 1e6:.1f} MB")


def de_tabwin(arq: Path, tipo: str, mapa7: dict[str, str]) -> list[dict]:
    recs = ler_map(arq)
    # várias partes com o mesmo código -> MultiPolygon
    por_cod: dict[str, dict] = {}
    for r in recs:
        c = r["codigo"]
        if tipo == "municip":
            c6 = c[:6]
            if not c6.startswith(UF_COD):
                continue
            chave = mapa7.get(c6, c6)
            props = {"cod7": chave, "cod6": c6, "nome": r["nome"]}
        elif tipo == "regsaud":
            chave = c[:5]; props = {"cod": chave, "nome": r["nome"]}
        else:
            chave = c[:4]; props = {"cod": chave, "nome": r["nome"]}
        if not chave.startswith(UF_COD):
            continue
        e = por_cod.setdefault(chave, {"props": props, "aneis": []})
        e["aneis"].append(r["anel"])
    return [feature(e["props"], e["aneis"]) for e in por_cod.values()]


def de_shapefile(arq: Path, mapa7: dict[str, str]) -> list[dict]:
    try:
        import shapefile  # pyshp
    except ImportError:
        log("  pyshp não instalado; shapefile ignorado (use o .MAP do TabWin ou um GeoJSON)")
        return []
    sf = shapefile.Reader(str(arq))
    campos = [f[0] for f in sf.fields[1:]]
    chave = next((c for c in ("CD_MUN", "CD_GEOCMU", "GEOCODIGO", "cod7", "codarea") if c in campos), None)
    if not chave:
        log(f"  shapefile sem campo de código conhecido ({campos[:8]}...)"); return []
    feats = []
    for sr in sf.iterShapeRecords():
        cod = str(sr.record[campos.index(chave)]).strip()
        if not cod.startswith(UF_COD):
            continue
        g = sr.shape.__geo_interface__
        feats.append({"type": "Feature", "properties": {"cod7": cod if len(cod) == 7 else mapa7.get(cod[:6], cod)}, "geometry": g})
    return feats


def de_geojson(arq: Path, mapa7: dict[str, str]) -> list[dict]:
    gj = json.loads(arq.read_text(encoding="utf-8"))
    feats = []
    for f in gj.get("features", []):
        p = f.get("properties", {})
        cod = str(p.get("cod7") or p.get("codarea") or p.get("CD_MUN") or p.get("CD_GEOCMU") or "").strip()
        if cod.startswith(UF_COD):
            feats.append({"type": "Feature", "properties": {"cod7": cod if len(cod) == 7 else mapa7.get(cod[:6], cod)}, "geometry": f["geometry"]})
    return feats


def main() -> int:
    if not MALHA_DIR.exists():
        log(f"pasta de malhas {MALHA_DIR} não existe; nada a fazer (o 'exportar' tenta baixar a malha do IBGE)")
        return 0
    arquivos = {p.name.lower(): p for p in MALHA_DIR.iterdir() if p.is_file()}
    log(f"pasta {MALHA_DIR}: {len(arquivos)} arquivos")
    mapa7 = cod6_para_cod7()
    uf = UF.lower()
    feito = False
    for tipo, saida in (("municip", f"malha_{uf}.geojson"), ("regsaud", f"malha_regsaud_{uf}.geojson"), ("macsaud", f"malha_macsaud_{uf}.geojson")):
        cand = [p for n, p in arquivos.items() if n.endswith(".map") and tipo in n]
        if cand:
            log(f"TabWin {cand[0].name} -> {saida}")
            feats = de_tabwin(cand[0], tipo, mapa7)
            if feats:
                gravar(saida, feats); feito = True
    if not any(n.endswith(".map") and "municip" in n for n in arquivos):
        shp = [p for n, p in arquivos.items() if n.endswith(".shp")]
        gjs = [p for n, p in arquivos.items() if n.endswith((".geojson", ".json"))]
        feats = de_shapefile(shp[0], mapa7) if shp else (de_geojson(gjs[0], mapa7) if gjs else [])
        if feats:
            log(f"{(shp or gjs)[0].name} -> malha_{uf}.geojson")
            gravar(f"malha_{uf}.geojson", feats); feito = True
    if not feito:
        log("nenhum mapa reconhecido (.MAP do TabWin, .shp ou .geojson)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
