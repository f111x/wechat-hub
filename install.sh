#!/usr/bin/env bash
#
# wechat-hub 一键安装器（macOS / Linux / Windows-Git-Bash）
#
# 自动完成：
#   1. 取得本 Skill 自身（clone 或识别当前目录）
#   2. 克隆上游 wechat-intelligence-hub
#   3. 检测 / 安装 Rust
#   4. 克隆并编译 wcdb-key（密钥抓取工具）
#   5. 安装 Python 依赖（sqlcipher3 / zstandard）
#   6. 运行 preflight 自检
#
# 首次接入（抓密钥）仍需手动，见文末提示。工具不代取密钥。
#
set -euo pipefail

# ===== 可配置项（发布前把 <YOUR_GITHUB> 换成你的仓库）=====
DEFAULT_REPO="https://github.com/<YOUR_GITHUB>/wechat-hub.git"
UPSTREAM_HUB="https://github.com/Rion-Wu-tech/wechat-intelligence-hub.git"
UPSTREAM_KEY="https://github.com/webees/wcdb-key.git"
TARGET="${HOME}/.workbuddy/skills/wechat-hub"

# ===== 环境探测 =====
OS="$(uname -s)"
case "$OS" in
  MINGW*|MSYS*|CYGWIN*) PLAT="win" ;;
  Darwin)                PLAT="mac" ;;
  *)                     PLAT="linux" ;;
esac

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "✗ 未找到 Python，请先安装 Python 3.11+ 再运行本脚本" >&2
  exit 1
fi

echo "== wechat-hub 安装器 =="
echo "平台: $PLAT | Python: $($PY --version 2>&1)"

# ===== 1. 取得 Skill 自身 =====
if [ -f "$PWD/SKILL.md" ]; then
  SKILL_DIR="$PWD"
  echo "✓ 已在 Skill 目录: $SKILL_DIR"
elif [ -d "$TARGET/SKILL.md" ]; then
  SKILL_DIR="$TARGET"
  echo "✓ 已安装到: $SKILL_DIR"
else
  REPO="${1:-$DEFAULT_REPO}"
  if [ "$REPO" = "$DEFAULT_REPO" ]; then
    echo "✗ 未提供 Skill 仓库地址。请先 clone 本仓库，或传入仓库 URL 作为第一个参数。" >&2
    exit 1
  fi
  echo "→ 克隆 Skill: $REPO"
  git clone "$REPO" "$TARGET"
  SKILL_DIR="$TARGET"
fi
SCRIPTS="$SKILL_DIR/scripts"

# ===== 2. 上游 wechat-intelligence-hub =====
HUB_HOME="${WECHAT_HUB_HOME:-$HOME/wechat-intelligence-hub}"
if [ -d "$HUB_HOME/.git" ]; then
  echo "✓ 上游已存在: $HUB_HOME（跳过）"
else
  echo "→ 克隆上游 wechat-intelligence-hub: $HUB_HOME"
  git clone "$UPSTREAM_HUB" "$HUB_HOME"
fi

# ===== 3. Rust =====
if command -v cargo >/dev/null 2>&1; then
  echo "✓ Rust 已装: $(rustc --version 2>&1)"
else
  echo "→ 安装 Rust ..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  # shellcheck disable=SC1091
  [ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
fi

# ===== 4. wcdb-key 克隆 + 编译 =====
WCKB="${WCKB_DIR:-$HOME/wcdb-key}"
WCKB_BIN="$WCKB/target/release/wcdb-key"
[ "$PLAT" = "win" ] && WCKB_BIN="$WCKB_BIN.exe"

if [ -x "$WCKB_BIN" ]; then
  echo "✓ wcdb-key 已编译: $WCKB_BIN（跳过）"
else
  if [ ! -d "$WCKB/.git" ]; then
    echo "→ 克隆 wcdb-key: $WCKB"
    git clone "$UPSTREAM_KEY" "$WCKB"
  fi
  echo "→ 编译 wcdb-key（约 15s）..."
  (cd "$WCKB" && cargo build --release)
fi

# ===== 5. Python 依赖 =====
echo "→ 安装 Python 依赖（sqlcipher3 需要本地 C 编译工具链）"
$PY -m pip install --user sqlcipher3 zstandard 2>/dev/null || \
$PY -m pip install sqlcipher3 zstandard || \
pip install sqlcipher3 zstandard

# ===== 6. 自检 =====
echo ""
echo "== 运行 preflight 自检 =="
$PY "$SCRIPTS/wxconfig.py" preflight || true

echo ""
echo "=================================================="
echo " 安装完成。下一步：首次接入（需微信 PC 版正在运行）"
echo "=================================================="
echo ""
echo "1) 抓密钥（在 wcdb-key 目录）："
echo "     cd $WCKB && cargo build --release && ./target/release/wcdb-key capture --json"
if [ "$PLAT" = "mac" ]; then
  echo "     （macOS 可能需加 --wechat-path 指向 Weixin.app）"
fi
echo ""
echo "2) 转成 Reader 格式："
echo "     $PY $SCRIPTS/wxkey_to_reader.py \\"
echo "       --input ~/.wcdb-key/wechat-passphrase.keys.json \\"
echo "       --database-root <db_storage> \\"
echo "       --output ~/.wechat-private/material.json"
echo ""
echo "3) 收紧权限 + 导入验证："
echo "     $PY $SCRIPTS/secure_acl.py ~/.wechat-private"
echo "     bash $SCRIPTS/rreader.sh import-access \\"
echo "       --source ~/.wechat-private/material.json \\"
echo "       --database-root <db_storage> \\"
echo "       --keys-file ~/.wechat-private/keys.json --verify"
echo ""
echo "4) 之后每天："
echo "     $PY $SCRIPTS/verify_wechat.py"
echo "     $PY $SCRIPTS/export_day.py <YYYY-MM-DD>"
echo "     $PY $SCRIPTS/analyze_day.py <导出目录>"
echo ""
echo "db_storage 路径："
echo "  Windows: %USERPROFILE%\\Documents\\xwechat_files\\<账号>\\db_storage"
echo "  macOS:   ~/Library/Containers/com.tencent.xinWeChat/.../db_storage"
