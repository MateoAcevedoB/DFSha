param([switch]$Ejecutar,[switch]$Prueba,[switch]$SinTranscripcion)
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
# Ejecuta docker mezclando stderr (el progreso de compose y los errores de Python salen por ahi),
# muestra todo en pantalla/transcripcion y devuelve las lineas de salida. Falla con el texto real.
function Run-Docker {
    $previous=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        $lines = @(& docker @args 2>&1 | ForEach-Object { $_.ToString() })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference=$previous }
    $lines | Out-Host
    if ($code -ne 0) { throw ("Docker fallo (codigo $code): docker $args`n" + ($lines -join "`n")) }
    return $lines
}
New-Item -ItemType Directory -Force evidencias | Out-Null
if (-not $SinTranscripcion) { Start-Transcript -Path ('evidencias/gc-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.txt') | Out-Null }
try {
    Run-Docker compose stop gateway cn1 cn2 cn3 repair | Out-Null
    $active = Run-Docker compose ps --status running --services
    if ($active | Where-Object { $_ -match '^(gateway|cn[123]|repair)$' }) { throw 'Quedan escritores activos' }
    if ($Prueba) {
        if (-not $Ejecutar) { throw 'La prueba de borrado requiere -Ejecutar' }
        # dn1 pudo haberse reiniciado hace segundos (prueba degradada): esperar a que este sano.
        Run-Docker compose up -d --wait --wait-timeout 120 dn1 | Out-Null
        # Solo crea un objeto sintetico propio; no envejece datos del usuario.
        $out = Run-Docker compose exec -T dn1 python scripts/gc_probe.py create
        $probeId = ($out | Where-Object { $_ -match '^[0-9a-f]{64}$' } | Select-Object -Last 1)
        if (-not $probeId) { throw ('No se pudo crear el objeto de prueba GC. Salida: ' + ($out -join ' | ')) }
        Write-Output "Objeto de prueba GC creado: $probeId"
    }
    # Dejar terminar capacidades de bloque ya emitidas y transferencias en vuelo.
    for ($i=0;$i -lt 13;$i++) { Write-Output 'Esperando que terminen las capacidades en vuelo...'; Start-Sleep -Seconds 5 }
    if ($Ejecutar) {
        Run-Docker compose --profile maintenance run --rm --no-deps maintenance python -m dfsha.gc --writers-stopped --execute | Out-Null
    } else {
        Run-Docker compose --profile maintenance run --rm --no-deps maintenance python -m dfsha.gc --writers-stopped | Out-Null
    }
    if ($Prueba) {
        Run-Docker compose exec -T dn1 python scripts/gc_probe.py check $probeId | Out-Null
    }
} finally {
    Run-Docker compose up -d --wait --wait-timeout 120 | Out-Null
    if (-not $SinTranscripcion) { Stop-Transcript | Out-Null }
}
