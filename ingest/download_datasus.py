#!/usr/bin/env python3
"""
Baixa arquivos públicos do DATASUS (FTP), converte DBC -> DBF -> Parquet.

Camadas:
  /data/raw/<BASE>/<arquivo>.dbc        cópia fiel do original (nunca é alterada)
  /data/bronze/<base>/<arquivo>.parquet  mesma informação, todas as colunas como texto
  /data/bronze/<base>/<arquivo>.meta.json  origem, tamanho, nº de linhas, data da conversão

Tudo é idempotente: arquivos já baixados/convertidos são pulados.
Configuração por variáveis de ambiente (ver .env.example): UF, ANOS, CNES_MES, BASES, DATA_DIR.
"""
from __future__ import annotations

import ftplib
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from dbfread import DBF, FieldParser
from pyreaddbc import dbc2dbf

FTP_HOST = "ftp.datasus.gov.br"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
RAW = DATA_DIR / "raw"
BRONZE = DATA_DIR / "bronze"

UF = os.environ.get("UF", "MG").upper()
ANOS = [int(a) for a in os.environ.get("ANOS", "2022,2023").replace(" ", "").split(",") if a]
CNES_MES = os.environ.get("CNES_MES", "12").zfill(2)
BASES = [b.strip().upper() for b in os.environ.get("BASES", "SINASC,SIM,DOFET,SIH_RD,CNES_LT,CNES_ST").split(",") if b.strip()]

# Catálogo de localização dos arquivos no FTP.
# 'dirs' é uma lista: o primeiro diretório em que o arquivo existir é usado
# (os dados definitivos vêm antes dos preliminares).
CATALOGO = {
    "SINASC": {
        "dirs": ["/dissemin/publicos/SINASC/1996_/Dados/DNRES", "/dissemin/publicos/SINASC/PRELIM/DNRES"],
        "freq": "anual", "nome": "DN{UF}{ANO4}.dbc", "pasta": "sinasc",
    },
    "SIM": {
        "dirs": ["/dissemin/publicos/SIM/CID10/DORES", "/dissemin/publicos/SIM/PRELIM/DORES"],
        "freq": "anual", "nome": "DO{UF}{ANO4}.dbc", "pasta": "sim_do",
    },
    "DOFET": {  # óbitos fetais: arquivo nacional por ano
        "dirs": ["/dissemin/publicos/SIM/CID10/DOFET", "/dissemin/publicos/SIM/PRELIM/DOFET"],
        "freq": "anual", "nome": "DOFET{ANO2}.dbc", "pasta": "sim_dofet",
    },
    "SIH_RD": {
        "dirs": ["/dissemin/publicos/SIHSUS/200801_/Dados"],
        "freq": "mensal", "nome": "RD{UF}{ANO2}{MES}.dbc", "pasta": "sih_rd",
    },
    "CNES_LT": {
        "dirs": ["/dissemin/publicos/CNES/200508_/Dados/LT"],
        "freq": "competencia", "nome": "LT{UF}{ANO2}{MES}.dbc", "pasta": "cnes_lt",
    },
    "CNES_ST": {
        "dirs": ["/dissemin/publicos/CNES/200508_/Dados/ST"],
        "freq": "competencia", "nome": "ST{UF}{ANO2}{MES}.dbc", "pasta": "cnes_st",
    },
    "SINAN_SIFC": {  # sífilis congênita (SINAN): arquivo nacional por ano de notificação; a UF é filtrada no dbt
        "dirs": ["/dissemin/publicos/SINAN/DADOS/FINAIS", "/dissemin/publicos/SINAN/DADOS/PRELIM"],
        "freq": "anual", "nome": "SIFCBR{ANO2}.dbc", "pasta": "sinan_sifc",
    },
}


class ParserTolerante(FieldParser):
    """O SINAN grava datas vazias como '********' em campos de tipo data; o dbfread levantaria erro.
    Devolve o texto bruto nesses casos (todas as colunas viram texto de qualquer forma)."""

    def parseD(self, field, data):
        try:
            return super().parseD(field, data)
        except ValueError:
            s = data.decode("latin-1", "replace").strip("* \x00")
            return s or None

    def parseN(self, field, data):
        try:
            return super().parseN(field, data)
        except ValueError:
            return data.decode("latin-1", "replace").strip() or None


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- FTP
class Ftp:
    """Conexão FTP com reconexão simples."""

    def __init__(self) -> None:
        self.conn: ftplib.FTP | None = None

    def connect(self) -> ftplib.FTP:
        if self.conn is None:
            for tentativa in range(1, 6):
                try:
                    c = ftplib.FTP()
                    c.connect(FTP_HOST, 21, timeout=60)
                    c.login()
                    c.set_pasv(True)
                    self.conn = c
                    break
                except Exception as e:  # noqa: BLE001
                    log(f"FTP: falha ao conectar ({tentativa}/5): {e}")
                    time.sleep(5 * tentativa)
            else:
                raise RuntimeError("Não foi possível conectar ao FTP do DATASUS.")
        return self.conn

    def reset(self) -> None:
        try:
            if self.conn:
                self.conn.quit()
        except Exception:  # noqa: BLE001
            pass
        self.conn = None

    def listar(self, diretorio: str) -> set[str]:
        c = self.connect()
        c.cwd(diretorio)
        return {n.upper() for n in c.nlst()}

    def tamanho(self, diretorio: str, nome: str) -> int | None:
        c = self.connect()
        try:
            c.cwd(diretorio)
            c.voidcmd("TYPE I")
            return c.size(nome)
        except Exception:  # noqa: BLE001
            return None

    def baixar(self, diretorio: str, nome: str, destino: Path) -> None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        for tentativa in range(1, 4):
            try:
                c = self.connect()
                c.cwd(diretorio)
                c.voidcmd("TYPE I")
                with tempfile.NamedTemporaryFile(dir=destino.parent, delete=False, suffix=".part") as tmp:
                    c.retrbinary(f"RETR {nome}", tmp.write, blocksize=1024 * 256)
                    tmp_path = Path(tmp.name)
                tmp_path.replace(destino)  # troca atômica: nunca fica um .dbc pela metade
                return
            except Exception as e:  # noqa: BLE001
                log(f"  download falhou ({tentativa}/3): {e}")
                self.reset()
                time.sleep(5 * tentativa)
        raise RuntimeError(f"Falha definitiva ao baixar {nome}")


# ---------------------------------------------------------------- conversão
def dbc_para_parquet(dbc: Path, parquet: Path, origem: dict) -> int:
    """Converte DBC -> DBF (temporário) -> Parquet. Todas as colunas como texto."""
    parquet.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        dbf_path = Path(td) / (dbc.stem + ".dbf")
        dbc2dbf(str(dbc), str(dbf_path))
        tabela = DBF(str(dbf_path), encoding="latin-1", char_decode_errors="replace",
                     raw=False, load=False, ignore_missing_memofile=True, parserclass=ParserTolerante)
        campos = [f.name for f in tabela.fields]
        schema = pa.schema([(c, pa.string()) for c in campos])
        linhas = 0
        writer = pq.ParquetWriter(str(parquet) + ".part", schema, compression="zstd")
        lote: dict[str, list] = {c: [] for c in campos}
        try:
            for rec in tabela:
                for c in campos:
                    v = rec.get(c)
                    if v is None:
                        lote[c].append(None)
                    elif isinstance(v, (bytes, bytearray)):
                        lote[c].append(v.decode("latin-1", "replace").strip())
                    else:
                        s = str(v).strip()
                        lote[c].append(s if s != "" else None)
                linhas += 1
                if linhas % 50000 == 0:
                    writer.write_table(pa.table(lote, schema=schema))
                    lote = {c: [] for c in campos}
            if lote[campos[0]]:
                writer.write_table(pa.table(lote, schema=schema))
        finally:
            writer.close()
    Path(str(parquet) + ".part").replace(parquet)
    meta = {
        "arquivo_origem": dbc.name, "ftp": origem, "md5_dbc": md5(dbc), "linhas": linhas,
        "colunas": campos, "convertido_em": datetime.now(timezone.utc).isoformat(),
    }
    parquet.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return linhas


def md5(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


# ---------------------------------------------------------------- plano de arquivos
def arquivos_desejados(base: str) -> list[tuple[str, str]]:
    """Retorna [(nome_arquivo, rotulo)] conforme a frequência da base."""
    cfg = CATALOGO[base]
    out = []
    for ano in ANOS:
        a4, a2 = str(ano), str(ano)[-2:]
        if cfg["freq"] == "anual":
            out.append((cfg["nome"].format(UF=UF, ANO4=a4, ANO2=a2), a4))
        elif cfg["freq"] == "mensal":
            for m in range(1, 13):
                out.append((cfg["nome"].format(UF=UF, ANO2=a2, MES=f"{m:02d}"), f"{a4}-{m:02d}"))
        elif cfg["freq"] == "competencia":
            out.append((cfg["nome"].format(UF=UF, ANO2=a2, MES=CNES_MES), f"{a4}-{CNES_MES}"))
    return out


def main() -> int:
    log(f"UF={UF} ANOS={ANOS} CNES_MES={CNES_MES} BASES={BASES}")
    ftp = Ftp()
    resumo = []
    erros = 0
    for base in BASES:
        if base not in CATALOGO:
            log(f"Base desconhecida: {base} (ignorada)")
            continue
        cfg = CATALOGO[base]
        listagens: dict[str, set[str]] = {}
        for nome, rotulo in arquivos_desejados(base):
            dbc = RAW / base / nome
            parquet = BRONZE / cfg["pasta"] / (Path(nome).stem.lower() + ".parquet")
            if parquet.exists():
                log(f"{base} {rotulo}: já convertido ({parquet.name})")
                continue
            if not dbc.exists():
                origem_dir = None
                for d in cfg["dirs"]:
                    if d not in listagens:
                        try:
                            listagens[d] = ftp.listar(d)
                        except Exception as e:  # noqa: BLE001
                            log(f"  não consegui listar {d}: {e}")
                            listagens[d] = set()
                            ftp.reset()
                    if nome.upper() in listagens[d]:
                        origem_dir = d
                        break
                if origem_dir is None:
                    log(f"{base} {rotulo}: {nome} não existe no FTP (ano ainda não publicado?)")
                    resumo.append((base, rotulo, "ausente no FTP", 0))
                    continue
                log(f"{base} {rotulo}: baixando {nome} de {origem_dir}")
                try:
                    ftp.baixar(origem_dir, nome, dbc)
                except Exception as e:  # noqa: BLE001
                    log(f"  ERRO: {e}")
                    erros += 1
                    continue
                origem = {"host": FTP_HOST, "dir": origem_dir, "nome": nome,
                          "baixado_em": datetime.now(timezone.utc).isoformat()}
            else:
                origem = {"host": FTP_HOST, "dir": "(já existia em raw)", "nome": nome}
            log(f"{base} {rotulo}: convertendo {nome} -> {parquet.name}")
            try:
                n = dbc_para_parquet(dbc, parquet, origem)
                log(f"  {n:,} linhas".replace(",", "."))
                resumo.append((base, rotulo, "ok", n))
            except Exception as e:  # noqa: BLE001
                log(f"  ERRO na conversão: {e}")
                erros += 1
    ftp.reset()
    log("Resumo:")
    for base, rotulo, status, n in resumo:
        log(f"  {base:8s} {rotulo:8s} {status:14s} {n:>12,}".replace(",", "."))
    if erros:
        log(f"{erros} erro(s). Rode de novo: o que já foi feito será pulado.")
        return 1
    log("Concluído sem erros.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
