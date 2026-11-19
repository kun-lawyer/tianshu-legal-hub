# 天枢中台安装：检查并补齐 Python、Node.js、Git，建虚拟环境装依赖，再交给 工具\安装.py 完成其余配置。
# 用法：双击 安装.cmd，或在 PowerShell 里运行 .\install.ps1 [安装.py 的参数，如 --answers 应答.json]
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Update-PathFromRegistry {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Confirm-Step($question) {
    if ($env:TIANSHU_YES -eq '1') { return $true }
    $a = Read-Host "$question [Y/n]"
    return ($a -eq '' -or $a -match '^[Yy]')
}

function Test-Tool($name, $versionArgs, $minMajor) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    if ($cmd.Source -match 'WindowsApps\\python') { return $false }  # 应用商店占位程序，不是真的 Python
    if ($minMajor -eq 0) { return $true }
    $v = (& $name @versionArgs 2>&1 | Out-String)
    if ($v -match '(\d+)\.(\d+)') { return ([int]$Matches[1] * 100 + [int]$Matches[2]) -ge $minMajor }
    return $false
}

function Install-Tool($label, $wingetId) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "缺少 $label，本机也没有 winget。请手动安装 $label 后重新运行本脚本。"
    }
    if (-not (Confirm-Step "缺少 $label，用 winget 安装（$wingetId）？")) { throw "请先安装 $label 再运行本脚本。" }
    winget install -e --id $wingetId --accept-package-agreements --accept-source-agreements
    Update-PathFromRegistry
}

Write-Host '== 检查运行环境'
# 最低版本：Python 3.10（写成 310），Node.js 24（写成 2400）
if (-not (Test-Tool 'python' @('--version') 310)) { Install-Tool 'Python 3.10 或更新' 'Python.Python.3.13' }
if (-not (Test-Tool 'node' @('--version') 2400)) { Install-Tool 'Node.js 24 或更新' 'OpenJS.NodeJS' }
if (-not (Test-Tool 'git' @('--version') 0)) { Install-Tool 'Git' 'Git.Git' }
foreach ($t in 'python', 'node', 'git') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "装好了 $t 但当前窗口还找不到它：关掉窗口重新运行本脚本。" }
}
Write-Host ('   Python ' + (python --version 2>&1) + '，Node.js ' + (node --version) + '，' + (git --version))

Write-Host '== Python 依赖'
$venv = Join-Path $repo '.venv'
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) { python -m venv $venv }
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $repo 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖没装上，请看上面的报错。' }

& $py (Join-Path $repo '工具\安装.py') @args
exit $LASTEXITCODE
