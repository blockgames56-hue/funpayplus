$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv-core\Scripts\python.exe'
$localCargo = Join-Path $projectRoot '.toolchains\cargo'
$savedPath = $env:PATH
$savedCargo = $env:CARGO_HOME
$savedRustup = $env:RUSTUP_HOME
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv-core and install backend/requirements-core.lock first.' }
    if (Test-Path -LiteralPath (Join-Path $localCargo 'bin\cargo.exe')) {
        $env:CARGO_HOME = $localCargo
        $env:RUSTUP_HOME = Join-Path $projectRoot '.toolchains\rustup'
        $env:PATH = (Join-Path $localCargo 'bin') + ';' + $savedPath
    }
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Rust/MSVC toolchain is required.' }
    $hostTarget = (& rustc -vV | Select-String '^host: ').ToString().Substring(6)
    $sidecar = Join-Path $projectRoot "apps\desktop\src-tauri\binaries\pulse-backend-$hostTarget.exe"
    # Tauri replaces this file while preparing the bundle. A running isolated
    # desktop smoke leaves the executable locked on Windows and otherwise
    # turns the later build error into an unhelpful PermissionDenied panic.
    $lockedSidecars = Get-CimInstance Win32_Process -Filter "Name = 'pulse-backend.exe'" |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq $sidecar }
    if ($lockedSidecars) {
        $processes = ($lockedSidecars | ForEach-Object { $_.ProcessId }) -join ', '
        throw "Stop the isolated FunPay Plus desktop process before building; it locks $sidecar (PID: $processes)."
    }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    & $python deploy/build-sidecar.py
    if ($LASTEXITCODE -ne 0) { throw 'Sidecar build failed.' }
    & $python deploy/verify-sidecar.py $sidecar
    if ($LASTEXITCODE -ne 0) { throw 'Embedded adapter does not match current sources.' }
    & $python deploy/smoke-core.py $sidecar
    if ($LASTEXITCODE -ne 0) { throw 'Packaged backend smoke failed.' }
    & npm.cmd run desktop:build
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
    $destination = Join-Path $projectRoot 'artifacts\desktop'
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $projectRoot 'apps\desktop\src-tauri\target\release\bundle') -Recurse -File |
        Where-Object { $_.Extension -in '.exe', '.msi' } |
        Copy-Item -Destination $destination
    Write-Output "Desktop packages: $destination"
} finally {
    $env:PATH = $savedPath
    $env:CARGO_HOME = $savedCargo
    $env:RUSTUP_HOME = $savedRustup
    Pop-Location
}
