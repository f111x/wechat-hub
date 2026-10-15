#Requires -Version 5.1
<#
.SYNOPSIS
  wechat-hub 一键安装器（Windows / PowerShell 原生，无需 Git Bash）
.DESCRIPTION
  1. 取得本 Skill 自身（clone 或识别当前目录）
  2. 克隆上游 wechat-intelligence-hub
  3. 检测 / 安装 Rust
  4. 克隆并编译 wcdb-key
  5. 安装 Python 依赖（sqlcipher3 / zstandard）
  6. 运行 preflight 自检
#>
$ErrorActionPreference = 'Stop'

$DEFAULT_REPO = "https://github.com/<YOUR_GITHUB>/wechat-hub.git"
$UPSTREAM_HUB = "https://github.com/Rion-Wu-tech/wechat-intelligence-hub.git"
$UPSTREAM_KEY = "https://github.com/webees/wcdb-key.git"
$TARGET = Join-Path $env:USERPROFILE ".workbuddy\skills\wechat-hub"

function Find-Python {
    foreach ($c in @('python', 'python3')) {
        try {
            $cmd = Get-Command $c -ErrorAction Stop
            return $cmd.Source
        } catch { }
    }
    return $null
}

Write-Host "== wechat-hub 安装器 (PowerShell) ==" -ForegroundColor Cyan

# 1. Python
$PY = Find-Python
if (-not $PY) {
    Write-Error "未找到 Python，请先安装 Python 3.11+ 再运行本脚本"
    exit 1
}
Write-Host "Python: $PY" -ForegroundColor Green

# 2. Skill 自身
if (Test-Path (Join-Path $PWD "SKILL.md")) {
    $SKILL_DIR = $PWD.Path
    Write-Host "已在 Skill 目录: $SKILL_DIR" -ForegroundColor Green
}
elseif (Test-Path (Join-Path $TARGET "SKILL.md")) {
    $SKILL_DIR = $TARGET
    Write-Host "已安装到: $SKILL_DIR" -ForegroundColor Green
}
else {
    if ($args.Count -ge 1) { $REPO = $args[0] } else { $REPO = $DEFAULT_REPO }
    if ($REPO -eq $DEFAULT_REPO) {
        Write-Error "未提供 Skill 仓库地址。请传入仓库 URL 作为第一个参数，或先 clone 本仓库。"
        exit 1
    }
    Write-Host "克隆 Skill: $REPO" -ForegroundColor Yellow
    & git clone $REPO $TARGET
    $SKILL_DIR = $TARGET
}
$SCRIPTS = Join-Path $SKILL_DIR "scripts"

# 3. 上游 wechat-intelligence-hub
$HUB_HOME = if ($env:WECHAT_HUB_HOME) { $env:WECHAT_HUB_HOME } else { Join-Path $env:USERPROFILE "wechat-intelligence-hub" }
if (Test-Path (Join-Path $HUB_HOME ".git")) {
    Write-Host "上游已存在: $HUB_HOME (跳过)" -ForegroundColor Green
}
else {
    Write-Host "克隆上游: $HUB_HOME" -ForegroundColor Yellow
    & git clone $UPSTREAM_HUB $HUB_HOME
}

# 4. Rust
$cargoExe = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
if (Test-Path $cargoExe) {
    Write-Host "Rust 已装: $(& $cargoExe --version)" -ForegroundColor Green
}
else {
    Write-Host "安装 Rust ..." -ForegroundColor Yellow
    $rustup = Join-Path $env:TEMP "rustup-init.exe"
    Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $rustup
    & $rustup -y --default-toolchain stable --profile minimal
    Remove-Item $rustup -Force
    if (-not (Test-Path $cargoExe)) {
        Write-Error "Rust 安装后仍未找到 cargo.exe，请手动安装 Rust 后重跑：https://rustup.rs"
        exit 1
    }
}

# 5. wcdb-key 克隆 + 编译
$WCKB = if ($env:WCKB_DIR) { $env:WCKB_DIR } else { Join-Path $env:USERPROFILE "wcdb-key" }
$WCKB_BIN = Join-Path $WCKB "target\release\wcdb-key.exe"
if (Test-Path $WCKB_BIN) {
    Write-Host "wcdb-key 已编译 (跳过)" -ForegroundColor Green
}
else {
    if (-not (Test-Path (Join-Path $WCKB ".git"))) {
        Write-Host "克隆 wcdb-key: $WCKB" -ForegroundColor Yellow
        & git clone $UPSTREAM_KEY $WCKB
    }
    Write-Host "编译 wcdb-key (约 15s) ..." -ForegroundColor Yellow
    Push-Location $WCKB
    & $cargoExe build --release
    Pop-Location
}

# 6. Python 依赖
Write-Host "安装 Python 依赖 (sqlcipher3 需要本地 C 编译工具链)" -ForegroundColor Yellow
& $PY -m pip install --user sqlcipher3 zstandard

# 7. 自检
Write-Host "`n== preflight 自检 ==" -ForegroundColor Cyan
& $PY (Join-Path $SCRIPTS "wxconfig.py") preflight

# 收尾提示
$line = "=================================================="
Write-Host "`n$line" -ForegroundColor Green
Write-Host " 安装完成。下一步：首次接入（需微信 PC 版正在运行）" -ForegroundColor Green
Write-Host $line -ForegroundColor Green

Write-Host "`n1) 抓密钥（在 $WCKB ）："
Write-Host "   cd $WCKB; & $cargoExe build --release; .\target\release\wcdb-key.exe capture --json"

Write-Host "`n2) 转成 Reader 格式："
Write-Host "   & $PY $(Join-Path $SCRIPTS 'wxkey_to_reader.py') --input $env:USERPROFILE\.wcdb-key\wechat-passphrase.keys.json --database-root <db_storage> --output $env:USERPROFILE\.wechat-private\material.json"

Write-Host "`n3) 收紧权限 + 导入验证："
Write-Host "   & $PY $(Join-Path $SCRIPTS 'secure_acl.py') $env:USERPROFILE\.wechat-private"
Write-Host "   bash $(Join-Path $SCRIPTS 'rreader.sh') import-access --source $env:USERPROFILE\.wechat-private\material.json --database-root <db_storage> --keys-file $env:USERPROFILE\.wechat-private\keys.json --verify"

Write-Host "`n4) 之后每天："
Write-Host "   & $PY $(Join-Path $SCRIPTS 'verify_wechat.py')"
Write-Host "   & $PY $(Join-Path $SCRIPTS 'export_day.py') <YYYY-MM-DD>"
Write-Host "   & $PY $(Join-Path $SCRIPTS 'analyze_day.py') <导出目录>"

Write-Host "`ndb_storage 路径：%USERPROFILE%\Documents\xwechat_files\<账号>\db_storage"
