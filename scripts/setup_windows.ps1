# Run from an already extracted backend. Does not install into live mpv config.
[CmdletBinding()]
param([switch]$Accel, [switch]$Cpu, [string]$Python = '3.12')
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $backendRoot 'pyproject.toml'))) {
    throw 'Run this script from the extracted source distribution.'
}
# Check before uv even queries an editable interpreter (Python 3.12 site crash).
if ($backendRoot -match '[^\x00-\x7F]') {
    throw 'Unsupported Windows backend path. Re-extract under an ASCII-only absolute path and recreate .venv. Unicode media/config paths remain supported. See WINDOWS_VERIFICATION.md.'
}
if ($env:UV_PROJECT_ENVIRONMENT -and $env:UV_PROJECT_ENVIRONMENT -ne (Join-Path $backendRoot '.venv')) {
    throw 'Clear UV_PROJECT_ENVIRONMENT: this supported workflow prepares the backend-local .venv only.'
}
Push-Location -LiteralPath $backendRoot
try {
    $syncArgs = @('sync', '--locked', '--python', $Python)
    if ($Accel) { $syncArgs += @('--group', 'accel') }
    & uv @syncArgs
    if ($LASTEXITCODE -ne 0) { throw 'Locked environment preparation failed.' }
    $backendPython = Join-Path $backendRoot '.venv\Scripts\python.exe'
    & $backendPython -X utf8 scripts/windows_preflight.py --paths-only
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported interpreter location. See WINDOWS_VERIFICATION.md.' }
    & $backendPython -X utf8 -m reframe doctor
    if ($LASTEXITCODE -ne 0) { throw 'Model preparation/doctor failed.' }
    $probeArgs = @('scripts/windows_preflight.py')
    if ($Cpu) { $probeArgs += '--cpu' }
    & $backendPython @probeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Inference preflight failed. Do not install/enable yet.' }
    Write-Output 'Prepared and inference verified. Install explicitly with scripts/plugin.py install --config-dir <your chosen config directory>.'
} finally { Pop-Location }
