param([int]$ArchivoMiB=1024)
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Run-Docker {
    $stderr=Join-Path (Get-Location) 'evidencias/docker-error.txt'
    # Windows PowerShell convierte stderr nativo en errores, incluso para progreso.
    $previousPreference=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        & docker @args 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $stderr | Out-Host
        $dockerExit=$LASTEXITCODE
    } finally {
        $ErrorActionPreference=$previousPreference
    }
    if ($dockerExit -ne 0) { throw "Docker fallo (codigo $dockerExit): $args" }
}
New-Item -ItemType Directory -Force evidencias | Out-Null
$report='evidencias/validacion-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.txt'
Start-Transcript -Path $report | Out-Null
try {
    Run-Docker info --format '{{.ServerVersion}}'
    Run-Docker info --format 'CPU={{.NCPU}} RAM_bytes={{.MemTotal}} Driver={{.Driver}} OS={{.OperatingSystem}}'
    Run-Docker compose --profile maintenance build
    $project=(Get-Location).Path
    Run-Docker run --rm --user 0 --mount "type=bind,source=$project,target=/project" --workdir /project dfsha5-cn1 python scripts/setup_secure.py
    Run-Docker compose up -d --wait --wait-timeout 240
    Run-Docker compose restart gateway
    Run-Docker compose run -T --rm --no-deps cn1 env -u TLS_CA -u TLS_CERT -u TLS_KEY -u JWT_SECRET_FILE -u FILE_MASTER_KEY_FILE -u CAPABILITY_SECRET_FILE -u RECEIPT_KEYS_DIR python -u -m pytest -ra -p no:cacheprovider
    # La CA local no publica CRL. Schannel mantiene la validacion de CA y nombre,
    # tolerando solo la ausencia de informacion de revocacion (no usar --insecure).
    $curlError=Join-Path $project 'evidencias/https-windows-error.txt'
    & curl.exe --fail --silent --show-error --ssl-revoke-best-effort --connect-timeout 5 --max-time 30 --stderr $curlError --cacert "$project\runtime\trust\ca.crt" https://127.0.0.1:18443/health/ready | Out-Host
    $curlExit=$LASTEXITCODE
    if (Test-Path $curlError) { Get-Content $curlError | Out-Host }
    if ($curlExit -ne 0) { throw "No paso la comprobacion HTTPS desde Windows (curl=$curlExit; ver $curlError)" }
    Run-Docker compose exec -T -e DFSHA_URL=https://gateway -e PYTHONPATH=/app cn2 python scripts/verify_full.py --large-mib $ArchivoMiB
    Run-Docker compose cp cn2:/tmp/dfsha-evidence/full.json evidencias/full.json
    Run-Docker compose exec -T -e DFSHA_URL=https://gateway -e PYTHONPATH=/app cn2 python scripts/benchmark.py --sizes-mib 4 16 --clients 1 2 --repeats 3 --condition normal --output /tmp/dfsha-evidence/normal.csv
    Run-Docker compose cp cn2:/tmp/dfsha-evidence/normal.csv evidencias/benchmark-normal.csv
    try {
        Run-Docker compose stop dn1
        Run-Docker compose exec -T -e DFSHA_URL=https://gateway -e PYTHONPATH=/app cn2 python scripts/benchmark.py --sizes-mib 4 16 --clients 1 2 --repeats 3 --condition dn1_detenido --output /tmp/dfsha-evidence/degraded.csv
        Run-Docker compose cp cn2:/tmp/dfsha-evidence/degraded.csv evidencias/benchmark-degradado.csv
    } finally {
        Run-Docker compose up -d --wait --wait-timeout 120 dn1
    }
    Run-Docker compose exec -T cn2 python scripts/summarize_benchmark.py /tmp/dfsha-evidence/normal.csv /tmp/dfsha-evidence/degraded.csv
    & .\scripts\limpiar_bloques.ps1 -Ejecutar -Prueba -SinTranscripcion
    if (-not $?) { throw 'Fallo la verificacion GC' }
    Write-Output 'PRUEBAS RF3, TLS/mTLS, archivo grande, benchmark y GC terminadas. Revisar los informes y CSV antes de declarar cumplimiento.'
    Write-Output 'Acceso: https://127.0.0.1:18443/docs. Usar la CA runtime/trust/ca.crt; no desactivar la verificacion TLS.'
} catch {
    Write-Output ('VERIFICACION INCOMPLETA: '+$_.Exception.Message)
    & docker compose ps | Out-Host
    & docker compose logs --tail 25 | Out-Host
    throw
} finally {Stop-Transcript | Out-Null;Write-Output "Informe: $report"}
