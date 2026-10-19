# Atalhos para Windows (PowerShell). Rode a partir da pasta nascer-pipeline:
#   .\scripts\run.ps1 ingest      -> baixa e converte os arquivos do DATASUS
#   .\scripts\run.ps1 territorio  -> municípios IBGE + base territorial (regiões de saúde, macrorregiões, coordenadas)
#   .\scripts\run.ps1 malha       -> converte os mapas (.MAP do TabWin, shapefile ou GeoJSON) em app/public/data/malha_*.geojson
#   .\scripts\run.ps1 build       -> roda o dbt (silver, gold, testes)
#   .\scripts\run.ps1 plano       -> gera o modelo a partir de plano/plano_analise.json e executa
#   .\scripts\run.ps1 conferir    -> imprime os totais para comparar com o TabNet
#   .\scripts\run.ps1 sql "select ..."  -> consulta livre
#   .\scripts\run.ps1 analise     -> tipologia dos municípios (clusters) + sífilis congênita × taxas (Python)
#   .\scripts\run.ps1 exportar    -> gera os JSON do app (app/public/data) a partir do warehouse
#   .\scripts\run.ps1 app         -> sobe o servidor do app e abre http://localhost:8080 no navegador
#   .\scripts\run.ps1 parar       -> para o servidor do app
#   .\scripts\run.ps1 empacotar   -> gera nascer-app.zip (conteúdo de app\public) para publicar em Netlify/Cloudflare Pages/VM
#   .\scripts\run.ps1 tudo        -> ingest + territorio + malha + build + plano + conferir + analise + exportar + app
param(
  [Parameter(Position = 0)] [string] $acao = "ajuda",
  [Parameter(Position = 1)] [string] $arg = ""
)
$ErrorActionPreference = "Stop"

# Este arquivo deve ser EXECUTADO (.\scripts\run.ps1 acao), não colado no console.
if ([string]::IsNullOrEmpty($PSScriptRoot)) {
  if (Test-Path ".\docker-compose.yml") { $raiz = (Get-Location).Path }
  else {
    Write-Host "Abra o PowerShell na pasta nascer-pipeline (onde está o docker-compose.yml) e rode:  .\scripts\run.ps1 tudo" -ForegroundColor Red
    exit 1
  }
} else {
  $raiz = Split-Path -Parent $PSScriptRoot
}
Set-Location $raiz

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Host "Docker não encontrado. Instale o Docker Desktop, abra-o e espere o motor iniciar; depois feche e reabra o PowerShell." -ForegroundColor Red
  exit 1
}

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Criei o arquivo .env a partir de .env.example. Edite UF e ANOS se quiser e rode de novo." -ForegroundColor Yellow
  exit 0
}

function Subir-App {
  docker compose up -d app
  if ($LASTEXITCODE -ne 0) { throw "Não consegui subir o servidor do app." }
  $url = "http://localhost:8080"
  Write-Host ""
  Write-Host "App no ar em $url  (para parar: .\scripts\run.ps1 parar)" -ForegroundColor Green
  Start-Process $url
}

switch ($acao) {
  "ingest"     { docker compose run --rm ingest }
  "territorio" { docker compose run --rm territorio }
  "malha"      { docker compose run --rm malha }
  "build"      { docker compose run --rm dbt dbt build }
  "plano"      { docker compose run --rm plano }
  "conferir"   { docker compose run --rm consulta "select * from main_gold.conferencia_tabnet order by ano, item" --limite 200 }
  "sql"        { docker compose run --rm consulta $arg --limite 100 }
  "tabelas"    { docker compose run --rm consulta --tabelas }
  "analise"    { docker compose run --rm analise }
  "exportar"   { docker compose run --rm exportar }
  "app"        { Subir-App }
  "parar"      { docker compose stop app; Write-Host "Servidor do app parado." }
  "empacotar"  {
    if (-not (Test-Path ".\app\public\data\meta.json")) { Write-Host "Rode 'exportar' antes de empacotar." -ForegroundColor Red; exit 1 }
    $zip = Join-Path $raiz "nascer-app.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Compress-Archive -Path ".\app\public\*" -DestinationPath $zip
    Write-Host "Pacote pronto: $zip  (só o app: página, biblioteca e JSON agregados; sem microdados, sem .env)" -ForegroundColor Green
  }
  "tudo"       {
    docker compose run --rm ingest
    docker compose run --rm territorio
    docker compose run --rm malha
    docker compose run --rm dbt dbt build
    docker compose run --rm plano
    docker compose run --rm consulta "select * from main_gold.conferencia_tabnet order by ano, item" --limite 200
    docker compose run --rm analise
    docker compose run --rm exportar
    Subir-App
  }
  default {
    Write-Host "Uso: .\scripts\run.ps1 [ingest|territorio|malha|build|plano|conferir|tabelas|analise|exportar|app|parar|empacotar|tudo|sql `"select ...`"]"
  }
}
