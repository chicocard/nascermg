#!/usr/bin/env python3
"""
Tabelas territoriais:
  1) Municípios do IBGE (API de localidades, HTTPS) -> /data/bronze/territorio/municipios_ibge.parquet
     colunas: cod7, cod6, nome, uf_sigla, uf_nome, microrregiao, mesorregiao, regiao_imediata, regiao_intermediaria
  2) Base territorial do DATASUS (FTP, base_territorial.zip) -> um Parquet por .dbf do zip
     (inclui a relação município -> região de saúde). Os nomes das tabelas e colunas
     são gravados em /data/bronze/territorio/_colunas.txt para conferência.

Idempotente: arquivos existentes são pulados. Para refazer, apague o arquivo correspondente.
"""
from __future__ import annotations

import ftplib
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests
from dbfread import DBF

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OUT = DATA_DIR / "bronze" / "territorio"
RAW = DATA_DIR / "raw" / "TERRITORIO"
IBGE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
FTP_HOST = "ftp.datasus.gov.br"
FTP_DIR = "/territorio/tabelas"
FTP_ZIP = "base_territorial.zip"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def municipios_ibge() -> None:
    destino = OUT / "municipios_ibge.parquet"
    if destino.exists():
        log("municipios_ibge.parquet já existe")
        return
    log("IBGE: baixando lista de municípios (API de localidades)")
    r = requests.get(IBGE_URL, timeout=120)
    r.raise_for_status()
    dados = r.json()
    linhas = []
    for d in dados:
        cod7 = str(d.get("municipio-id"))
        linhas.append({
            "cod7": cod7,
            "cod6": cod7[:6],
            "nome": d.get("municipio-nome"),
            "uf_sigla": d.get("UF-sigla"),
            "uf_nome": d.get("UF-nome"),
            "uf_cod": str(d.get("UF-id")),
            "microrregiao": d.get("microrregiao-nome"),
            "mesorregiao": d.get("mesorregiao-nome"),
            "regiao_imediata": d.get("regiao-imediata-nome"),
            "regiao_intermediaria": d.get("regiao-intermediaria-nome"),
            "regiao_nome": d.get("regiao-nome"),
        })
    OUT.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(linhas), destino, compression="zstd")
    (OUT / "municipios_ibge.meta.json").write_text(json.dumps({
        "origem": IBGE_URL, "linhas": len(linhas), "baixado_em": datetime.now(timezone.utc).isoformat()
    }, indent=2), encoding="utf-8")
    log(f"  {len(linhas)} municípios")


CANDIDATOS = [
    "/territorio/tabelas/base_territorial.zip",
    "/territorio/tabelas/Base_Territorial.zip",
    "/territorio/tabelas/base_territorial/base_territorial.zip",
    "/territorio/mapas/base_territorial.zip",
]
PADRAO = re.compile(r"(base.?territorial|regsaud|regiao.?saude|cir)", re.I)


def _listar(c: ftplib.FTP, d: str) -> list[tuple[str, str]]:
    """Retorna [(nome, tipo)] usando MLSD se disponível, senão NLST."""
    itens: list[tuple[str, str]] = []
    try:
        for nome, fatos in c.mlsd(d):
            if nome in (".", ".."):
                continue
            itens.append((nome, fatos.get("type", "file")))
        return itens
    except Exception:  # noqa: BLE001
        pass
    try:
        for nome in c.nlst(d):
            base = nome.rsplit("/", 1)[-1]
            tipo = "dir" if "." not in base else "file"
            itens.append((base, tipo))
    except Exception:  # noqa: BLE001
        pass
    return itens


MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _chave_versao(caminho: str) -> tuple:
    """(ano, mês, dia) lidos do caminho/nome: /territorio/tabelas/2026/06-base_territorial_jun26.zip -> (2026, 6, 0);
    /territorio/2015-01-19/base_territorial.zip -> (2015, 1, 19). Sem data -> (0, 0, 0), fica por último."""
    s = caminho.lower()
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(20\d\d)", s)
    if m:
        ano = int(m.group(1))
    else:
        m = re.search(r"(" + "|".join(MESES) + r")(\d\d)\b", s)
        ano = 2000 + int(m.group(2)) if m else 0
    m = re.search(r"(?:^|/)(\d{2})-", s)
    if m:
        mes = int(m.group(1))
    else:
        m = re.search(r"(" + "|".join(MESES) + r")", s)
        mes = MESES.index(m.group(1)) + 1 if m else 0
    return (ano, mes, 0)


def base_territorial_datasus() -> None:
    """Procura a base territorial do DATASUS (município -> região de saúde, macrorregião, coordenadas) no FTP.
    1) lista os candidatos: caminhos conhecidos + varredura de /territorio (3 níveis), gravada em
       bronze/territorio/_ftp_territorio.txt;
    2) do mais recente para o mais antigo, baixa o primeiro cujo zip contenha rl_municip_regsaud e converte
       suas tabelas para Parquet (sobrescrevendo versões anteriores). Nomes de colunas são normalizados."""
    RAW.mkdir(parents=True, exist_ok=True)
    c = None
    try:
        c = ftplib.FTP()
        c.connect(FTP_HOST, 21, timeout=60)
        c.login()
        c.set_pasv(True)
    except Exception as e:  # noqa: BLE001
        log(f"  FTP indisponível ({str(e).strip()[:60]}); usando o que já existe em raw/TERRITORIO")
        c = None

    def baixar(caminho: str) -> Path | None:
        nome = caminho.rsplit("/", 1)[-1]
        destino = RAW / caminho.strip("/").replace("/", "__")   # nome local guarda o caminho (versão)
        if destino.exists():
            log(f"  já existe: {destino.name}")
            return destino
        simples = RAW / nome                                     # execuções antigas gravavam só o nome
        if simples.exists() and nome.lower() != "base_territorial.zip":
            log(f"  já existe: {simples.name}")
            return simples
        if c is None:
            return None
        try:
            c.voidcmd("TYPE I")
            with destino.open("wb") as f:
                c.retrbinary(f"RETR {caminho}", f.write)
            log(f"  baixado: {caminho} ({destino.stat().st_size:,} bytes)")
            return destino
        except Exception as e:  # noqa: BLE001
            if destino.exists():
                destino.unlink()
            log(f"  não encontrado: {caminho} ({str(e).strip()[:60]})")
            return None

    # 1) candidatos: caminhos conhecidos + varredura do FTP + arquivos já baixados
    candidatos: list[str] = list(CANDIDATOS)
    if c is not None:
        log("DATASUS: listando /territorio (até 3 níveis) para achar a base territorial mais recente")
        linhas: list[str] = []
        fila = [("/territorio", 0)]
        while fila:
            d, prof = fila.pop(0)
            for nome, tipo in _listar(c, d):
                caminho = f"{d}/{nome}"
                linhas.append(f"{tipo:4s} {caminho}")
                if tipo == "dir" and prof < 2:
                    fila.append((caminho, prof + 1))
                elif tipo == "file" and PADRAO.search(nome) and nome.lower().endswith((".zip", ".dbf")):
                    candidatos.append(caminho)
        (OUT / "_ftp_territorio.txt").write_text("\n".join(linhas), encoding="utf-8")
        log(f"  {len(linhas)} entradas listadas (gravado em bronze/territorio/_ftp_territorio.txt)")
    for p in RAW.iterdir():
        if p.suffix.lower() in (".zip", ".dbf"):
            candidatos.append("/" + p.name.replace("__", "/"))
    candidatos = sorted(set(candidatos), key=_chave_versao, reverse=True)
    log(f"  {len(candidatos)} candidatos; mais recente: {candidatos[0] if candidatos else '-'}")

    RENOMEAR = {"co_muninip": "co_municip", "regsaud": "co_regsaud", "co_microrr": "co_regsaud",
                "co_macrorr": "co_macsaud", "ordem": "nu_ordem", "ordem_": "nu_ordem", "nu_longitu": "nu_longit"}
    relatorio: list[str] = []
    usado = None
    # 2) do mais recente para o mais antigo
    for remoto in candidatos:
        arq = baixar(remoto)
        if not arq:
            continue
        dbfs: list[Path] = []
        with tempfile.TemporaryDirectory() as td:
            if arq.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(arq) as z:
                        for n in z.namelist():
                            if n.lower().endswith(".dbf"):
                                z.extract(n, td)
                                dbfs.append(Path(td) / n)
                except zipfile.BadZipFile:
                    log(f"  {arq.name}: zip inválido; pulando")
                    continue
            else:
                dbfs.append(arq)
            nomes = {d.stem.lower() for d in dbfs}
            if "rl_municip_regsaud" not in nomes:
                log(f"  {arq.name}: sem rl_municip_regsaud; pulando")
                continue
            log(f"  usando {remoto}")
            for dbf in dbfs:
                nome_tab = dbf.stem.lower()
                destino = OUT / f"{nome_tab}.parquet"
                # Versões antigas vêm em latin-1; as de 2024+ em cp860 (DOS português). Em latin-1 os bytes
                # 0x80-0x9F nunca são texto; se aparecem, o arquivo está em cp860.
                enc = "cp860" if re.search(rb"[\x80-\x9f]", dbf.read_bytes()[32:]) else "latin-1"
                tab = DBF(str(dbf), encoding=enc, char_decode_errors="replace", load=False,
                          ignore_missing_memofile=True)
                campos = [RENOMEAR.get(f.name.lower(), f.name.lower()) for f in tab.fields]
                relatorio.append(f"{nome_tab} [{enc}]: {', '.join(campos)}")
                cols: dict[str, list] = {cc: [] for cc in campos}
                for rec in tab:
                    for f, cc in zip(tab.fields, campos):
                        v = rec.get(f.name)
                        cols[cc].append(None if v is None else str(v).strip())
                pq.write_table(pa.table(cols), destino, compression="zstd")
                log(f"  {nome_tab}.parquet ({len(cols[campos[0]])} linhas)")
            (OUT / "_versao.txt").write_text(remoto, encoding="utf-8")
            usado = remoto
            break
    if c is not None:
        c.quit()
    if not usado:
        log("  nenhuma base territorial utilizável no FTP; o pipeline segue sem região de saúde (nível municipal).")
        return
    (OUT / "_colunas.txt").write_text("\n".join(relatorio), encoding="utf-8")
    log("  tabelas e colunas listadas em bronze/territorio/_colunas.txt")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    erros = 0
    try:
        municipios_ibge()
    except Exception as e:  # noqa: BLE001
        log(f"ERRO IBGE: {e}")
        erros += 1
    try:
        base_territorial_datasus()
    except Exception as e:  # noqa: BLE001
        log(f"ERRO base territorial: {e}")
        erros += 1
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
