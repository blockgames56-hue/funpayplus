param(
    [Parameter(Mandatory=$true)][string]$Server,
    [string]$User = 'root',
    [ValidateRange(1,65535)][int]$Port = 22,
    [string]$Domain = '',
    [string]$StoragePath = '',
    [string]$Repository = '',
    [string]$Branch = 'main'
)
$ErrorActionPreference = 'Stop'
if ($Server -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$' -or $User -notmatch '^[A-Za-z_][A-Za-z0-9_-]*$') { throw 'Некорректное имя сервера или пользователя' }
if ($Domain -and $Domain -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$') { throw 'Некорректный домен' }
if ($Repository -and $Repository -notmatch '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$') { throw 'Repository должен быть HTTPS-адресом GitHub без пароля' }
if ($Branch -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or $Branch.Contains('..')) { throw 'Некорректная ветка' }
$root = Split-Path -Parent $PSScriptRoot
if (-not $StoragePath) { $StoragePath = Join-Path $root 'storage' }
$StoragePath = (Resolve-Path -LiteralPath $StoragePath).Path
if (-not (Test-Path -LiteralPath (Join-Path $StoragePath 'pulse.db'))) { throw 'В StoragePath нет pulse.db' }
$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw 'Перед переносом закройте FunPay Plus, включая значок в трее и локальный сервер на порту 8765. Это исключит одновременную обработку заказов на ПК и сервере.' }
$python = Join-Path $root '.venv-core/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $root '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
foreach ($command in @('ssh', 'scp', 'tar')) { Get-Command $command -ErrorAction Stop | Out-Null }
$transferId = [Guid]::NewGuid().ToString('N')
$stage = Join-Path ([IO.Path]::GetTempPath()) ('funpay-vps-' + $transferId)
$archive = "$stage.tar.gz"
New-Item -ItemType Directory -Path $stage | Out-Null
function Invoke-Checked([string]$program, [string[]]$arguments) {
    & $program @arguments
    if ($LASTEXITCODE -ne 0) { throw "$program завершился с кодом $LASTEXITCODE" }
}
try {
    Invoke-Checked $python @((Join-Path $root 'deploy/export_transfer.py'), '--storage', $StoragePath, '--output', (Join-Path $stage '.transfer'))
    $tarArgs = @('-czf', $archive, '--exclude=node_modules', '--exclude=target', '--exclude=dist', '--exclude=build', '--exclude=binaries', '--exclude=__pycache__', '--exclude=*.pyc', '--exclude=*.log', '--exclude=*.spec', '--exclude=backend/storage', '--exclude=.env', '--exclude=.venv*', '--exclude=deploy/snapshot.db')
    $tarArgs += @('-C', $root, 'install.sh', 'docker-compose.yml', '.dockerignore', '.gitignore', '.gitattributes', '.env.example', 'package.json', 'package-lock.json', 'pyproject.toml', 'README.md', 'backend', 'apps', 'deploy', 'docs', '-C', $stage, '.transfer')
    Invoke-Checked 'tar' $tarArgs
    $target = "$User@$Server"
    $remoteFolder = ".funpay-plus-upload-$transferId"
    Invoke-Checked 'ssh' @('-p', "$Port", $target, "umask 077; test ! -e ~/funpay-plus && mkdir ~/$remoteFolder")
    Invoke-Checked 'scp' @('-P', "$Port", $archive, "${target}:$remoteFolder/project.tar.gz")
    $installCode = if ($Repository) { "command -v git >/dev/null 2>&1 || { as_root apt-get update && as_root apt-get install -y git ca-certificates; }; git clone --depth 1 --branch '$Branch' '$Repository' ~/funpay-plus" } else { 'mkdir -m 700 ~/funpay-plus' }
    # Password prompts, sudo and a private GitHub clone use the user's SSH terminal.
    $privilegeHelper = 'as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }; '
    $remoteCommand = 'set -eu; umask 077; ' + $privilegeHelper + "$installCode; tar xzf ~/$remoteFolder/project.tar.gz -C ~/funpay-plus; rm ~/$remoteFolder/project.tar.gz; rmdir ~/$remoteFolder; cd ~/funpay-plus; as_root bash deploy/finish-upload.sh"
    if ($Domain) { $remoteCommand += " --domain '$Domain'" }
    Invoke-Checked 'ssh' @('-tt', '-p', "$Port", $target, $remoteCommand)
    Write-Host "Перенос завершён. $(if ($Domain) { "Панель: https://$Domain" } else { "Доступ: ssh -p $Port -L 8080:127.0.0.1:8080 $target ; затем http://localhost:8080" })"
    Write-Host 'Для просмотра ключа входа на сервере: cd ~/funpay-plus && sudo bash deploy/server.sh token'
} finally {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    if (-not $resolvedStage.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $resolvedStage) -notmatch '^funpay-vps-[0-9a-f]{32}$') { throw 'Небезопасный путь временной папки' }
    if (Test-Path -LiteralPath $resolvedStage) { Remove-Item -LiteralPath $resolvedStage -Recurse -Force }
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
}
