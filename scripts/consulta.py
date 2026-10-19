#!/usr/bin/env python3
"""
Consulta o warehouse DuckDB e, opcionalmente, exporta o resultado em CSV.

Exemplos (dentro do container "consulta"):
  python /scripts/consulta.py "select * from gold.conferencia_tabnet order by ano, item"
  python /scripts/consulta.py "select * from gold.ind_territorio where nivel='regiao_saude'" --csv ind_regiao
  python /scripts/consulta.py --tabelas
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

DB = os.environ.get("DUCKDB_PATH", "/data/warehouse/nascer.duckdb")
EXPORT = Path(os.environ.get("DATA_DIR", "/data")) / "export"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sql", nargs="?", help="SQL a executar")
    ap.add_argument("--arquivo", help="arquivo .sql a executar")
    ap.add_argument("--csv", help="nome do CSV de saída (sem extensão), gravado em data/export/")
    ap.add_argument("--tabelas", action="store_true", help="lista as tabelas do warehouse")
    ap.add_argument("--limite", type=int, default=50)
    a = ap.parse_args()

    con = duckdb.connect(DB, read_only=True)
    if a.tabelas:
        print(con.sql("select table_schema, table_name, estimated_size as linhas from duckdb_tables() order by 1, 2"))
        return
    sql = Path(a.arquivo).read_text(encoding="utf-8") if a.arquivo else a.sql
    if not sql:
        ap.error("informe o SQL ou --arquivo ou --tabelas")
    rel = con.sql(sql)
    if a.csv:
        EXPORT.mkdir(parents=True, exist_ok=True)
        destino = EXPORT / f"{a.csv}.csv"
        rel.write_csv(str(destino), header=True, sep=";")
        print(f"CSV gravado em {destino}")
    print(rel.limit(a.limite))


if __name__ == "__main__":
    main()
