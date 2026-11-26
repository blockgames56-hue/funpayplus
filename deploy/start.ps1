param([switch]$Dev, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
function Invoke-Checked {
    param([string]$FilePath, [string[]]$ArgumentList)
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $FilePath (exit $LASTEXITCODE)" }
}
$pythonPath = Join-Path $projectRoot '.venv-core/Scripts/python.exe'
$env:STORAGE_DIR = Join-Path $projectRoot 'storage'
$launcherPath = Join-Path $projectRoot 'backend/launcher.py'
$appUrl = 'http://127.0.0.1:8765'

function Get-RunningServer {
    if (-not (Test-Path -LiteralPath $pythonPath)) { return 2 }
    & $pythonPath $launcherPath --probe
    $probeExit = $LASTEXITCODE
    if ($probeExit -notin @(0, 2, 3)) { throw 'Could not check the local backend.' }
    return $probeExit
}

function Show-RunningServer {
    Write-Host "FunPay Plus is already running: $appUrl" -ForegroundColor Green
    Write-Host "Key file: $(Join-Path $projectRoot 'storage/access.key')"
    if ($Dev) { Write-Host 'Run npm run dev in a second terminal and open http://127.0.0.1:5173' }
    if (-not $NoBrowser) { Start-Process -FilePath $appUrl }
}

# Serialize launchers before package installation, builds or database migrations.
# A second start may connect to the server but must not start another worker set.
$startupMutex = New-Object System.Threading.Mutex($false, 'Local\FunPayPlus-Backend-8765')
$ownsStartup = $false
try {
    try { $ownsStartup = $startupMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $ownsStartup = $true }
    $serverState = Get-RunningServer
    if ($serverState -eq 0) {
        Show-RunningServer
        return
    }
    if (-not $ownsStartup) {
        Write-Host 'FunPay Plus is already starting in another window. Please wait.' -ForegroundColor Yellow
        return
    }
    if ($serverState -eq 3) {
        throw "Port 8765 is occupied by another service, another store, or a backend that is not ready. No second server was started. Check $appUrl and close the other instance if necessary."
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        Invoke-Checked -FilePath 'python' -ArgumentList @('-m', 'venv', '.venv-core')
    }
    Invoke-Checked -FilePath $pythonPath -ArgumentList @('-m', 'pip', 'install', '-r', 'backend/requirements.txt')
    Invoke-Checked -FilePath 'npm.cmd' -ArgumentList @('install', '--no-audit', '--no-fund')
    if (-not $Dev) { Invoke-Checked -FilePath 'npm.cmd' -ArgumentList @('run', 'build') }
    Set-Location -LiteralPath (Join-Path $projectRoot 'backend')
    # Reserve the port before migration and ASGI startup so an occupied address
    # cannot briefly run duplicate financial workers or rental schedulers.
    Write-Host "FunPay Plus: $appUrl" -ForegroundColor Green
    Write-Host "Key file: $(Join-Path $projectRoot 'storage/access.key')"
    if ($Dev) { Write-Host 'Run npm run dev in a second terminal and open http://127.0.0.1:5173' }
    Invoke-Checked -FilePath $pythonPath -ArgumentList @($launcherPath, '--host', '127.0.0.1', '--port', '8765')
}
finally {
    if ($ownsStartup) { $startupMutex.ReleaseMutex() }
    $startupMutex.Dispose()
}
