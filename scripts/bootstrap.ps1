# Bootstrap for the realtime screen translator.
#
# Finds a usable Python 3.10+ interpreter and hands over to scripts/setup_env.py.
# When no interpreter exists at all, a per-user Python is downloaded from
# python.org and installed silently. That install mode does NOT require
# administrator rights.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Models
#
# Windows PowerShell 5.1 compatible.

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Models,
    [switch]$ForceInstallPython,
    [switch]$InstallPythonOnly
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
# The Python helper scripts emit UTF-8; without this they would use the GBK locale
# encoding, and PowerShell would then decode those bytes as UTF-8 -> mojibake.
$env:PYTHONIOENCODING = 'utf-8'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Version of the interpreter to fetch when nothing usable is on the machine.
$PyVersion = '3.12.10'

function Get-PythonVersion {
    param([string]$Exe, [string[]]$Prefix = @())

    # The Microsoft Store "app execution alias" is a 0-byte stub that opens the
    # Store instead of running Python - never treat it as an interpreter.
    if ($Exe -like '*WindowsApps*') { return $null }

    # The probe must contain NO double quotes: Windows PowerShell 5.1 strips
    # embedded double quotes when passing arguments to a native executable, which
    # would turn a format string into a SyntaxError.
    $probe = 'import sys;print(sys.version_info[:2])'
    # NOTE: never name this $args - that is a PowerShell automatic variable and
    # splatting it silently passes nothing to the interpreter.
    $argList = @()
    $argList += $Prefix
    $argList += @('-c', $probe)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        $out = & $Exe @argList 2>$null
        $code = $LASTEXITCODE
    } catch {
        $out = $null
        $code = 1
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { return $null }

    $text = ("$out" | Select-Object -First 1).Trim()
    if ($text -notmatch '\((\d+),\s*(\d+)\)') { return $null }
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
        return [pscustomobject]@{ Exe = $Exe; Prefix = $Prefix; Version = "$major.$minor" }
    }
    return $null
}

function Find-Python {
    Write-Host '正在查找可用的 Python ...'

    # 1. The official launcher, which can pick a specific version.
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($selector in @('-3.12', '-3.11', '-3', '-3.10')) {
            $found = Get-PythonVersion -Exe $pyLauncher.Source -Prefix @($selector)
            if ($found) {
                Write-Host ("  找到 py {0} -> Python {1}" -f $selector, $found.Version)
                return $found
            }
        }
    }

    # 2. Whatever "python" resolves to on PATH.
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) {
        $found = Get-PythonVersion -Exe $onPath.Source
        if ($found) {
            Write-Host ("  找到 PATH 上的 python -> {0} ({1})" -f $found.Exe, $found.Version)
            return $found
        }
        if ($onPath.Source -like '*WindowsApps*') {
            Write-Host '  PATH 上的 python 是 Microsoft Store 别名，已跳过'
        }
    }

    # 3. Standard per-user and machine-wide install locations.
    $globs = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
        'C:\Python3*\python.exe',
        (Join-Path $env:ProgramFiles 'Python3*\python.exe')
    )
    foreach ($glob in $globs) {
        foreach ($candidate in (Get-ChildItem -Path $glob -ErrorAction SilentlyContinue |
                                Sort-Object FullName -Descending)) {
            $found = Get-PythonVersion -Exe $candidate.FullName
            if ($found) {
                Write-Host ("  找到已安装的 Python -> {0} ({1})" -f $found.Exe, $found.Version)
                return $found
            }
        }
    }

    # 4. The interpreter bundled with DeepSeek Harness, if present. This machine has
    #    no standalone Python, so this is normally the one that gets used.
    $dshGlob = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\*\dependencies\python\python.exe'
    foreach ($candidate in (Get-ChildItem -Path $dshGlob -ErrorAction SilentlyContinue |
                            Sort-Object FullName -Descending)) {
        $found = Get-PythonVersion -Exe $candidate.FullName
        if ($found) {
            Write-Host ("  找到 DeepSeek Harness 自带的 Python -> {0} ({1})" -f $found.Exe, $found.Version)
            return $found
        }
    }

    return $null
}

function Install-Python {
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        $arch = 'arm64'
    } else {
        $arch = 'amd64'
    }

    $target = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'
    $installerName = "python-$PyVersion-$arch.exe"
    $url = "https://www.python.org/ftp/python/$PyVersion/$installerName"
    $installer = Join-Path $env:TEMP $installerName

    Write-Host ''
    Write-Host '这台机器上没有找到 Python 3.10 及以上版本。'
    Write-Host ("将自动下载并为你（当前用户）安装 Python {0}：" -f $PyVersion)
    Write-Host "  $url"
    Write-Host "  安装位置: $target"
    Write-Host '  无需管理员权限，不影响系统其它程序。'
    Write-Host ''

    if ($DryRun) {
        Write-Host '[DryRun] 跳过实际下载与安装。'
        return $null
    }

    $answer = 'y'
    if (-not $ForceInstallPython) {
        $answer = Read-Host '继续？(Y/n)'
        if ($answer -eq '') { $answer = 'y' }
    }
    if ($answer -notmatch '^[Yy]') {
        Write-Host '已取消。'
        return $null
    }

    Write-Host '下载中 ...'
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'Continue'
    try {
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    } finally {
        $ProgressPreference = $oldProgress
    }
    if (-not (Test-Path $installer)) {
        throw '下载失败，请手动安装 Python 后重新运行 install.bat'
    }
    Write-Host ("下载完成: {0:N1} MB" -f ((Get-Item $installer).Length / 1MB))

    Write-Host '安装中（静默，仅当前用户，约 1 分钟）...'
    $installArgs = @(
        '/quiet',
        'InstallAllUsers=0',
        'PrependPath=0',
        'Include_launcher=0',
        'Include_test=0',
        'Include_tcltk=1',
        'Include_pip=1',
        'AssociateFiles=0',
        'Shortcuts=0',
        "TargetDir=$target"
    )
    $proc = Start-Process -FilePath $installer -ArgumentList $installArgs -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw ("Python 安装程序返回错误码 {0}" -f $proc.ExitCode)
    }

    $installed = Join-Path $target 'python.exe'
    if (-not (Test-Path $installed)) {
        throw "安装后没有找到 $installed"
    }
    $found = Get-PythonVersion -Exe $installed
    if (-not $found) {
        throw '安装完成但解释器不可用'
    }
    Write-Host ("Python {0} 安装完成" -f $found.Version)
    Remove-Item $installer -ErrorAction SilentlyContinue
    return $found
}

# ---------------------------------------------------------------------------

Write-Host '=============================================================='
Write-Host '  守译 - 环境安装'
Write-Host '=============================================================='

$python = $null
if (-not $InstallPythonOnly) {
    $python = Find-Python
}
if (-not $python) {
    $python = Install-Python
}
if (-not $python) {
    Write-Host ''
    Write-Host '未能准备好 Python。可手动下载安装后重试：'
    Write-Host '  https://www.python.org/downloads/windows/'
    Write-Host '  安装时请勾选 "Add python.exe to PATH" 和 "tcl/tk and IDLE"。'
    exit 1
}

if ($InstallPythonOnly) {
    Write-Host ''
    Write-Host ("Python {0} 就绪: {1}" -f $python.Version, $python.Exe)
    Write-Host '（只安装了 Python，没有改动虚拟环境和依赖）'
    exit 0
}

$setupArgs = @()
$setupArgs += $python.Prefix
$setupArgs += @((Join-Path $Root 'scripts\setup_env.py'))
if ($Models) { $setupArgs += '--models' }

Write-Host ''
Write-Host '--------------------------------------------------------------'
$cmdLine = ('"{0}" {1}' -f $python.Exe, (($setupArgs | ForEach-Object { '"{0}"' -f $_ }) -join ' '))
Write-Host "调用: $cmdLine"
Write-Host '--------------------------------------------------------------'

if ($DryRun) {
    Write-Host '[DryRun] 到此为止，没有做任何改动。'
    exit 0
}

& $python.Exe @setupArgs
$code = $LASTEXITCODE
exit $code
