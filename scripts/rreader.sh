#!/usr/bin/env bash
# rion-wechat-reader 统一调用入口（跨平台 + 自动配置）
#
# 三件事：
#   1. 用带 sqlcipher3/zstandard 的 Python（自动发现或读配置）
#   2. 打 Windows 兼容补丁（见 rion_compat.py）
#   3. 强制 --strict-read-only
#
# 首次使用先跑：  python scripts/wxconfig.py preflight
#
# 用法：
#   bash rreader.sh <命令> [参数...]
# 例：
#   bash rreader.sh self-test
#   bash rreader.sh doctor
#   bash rreader.sh sessions --limit 20
#   bash rreader.sh export-messages "<chatroom>" --format jsonl --view agent \
#        --limit 500 --path /tmp/chat.jsonl
#   bash rreader.sh import-access --source <材料.json> \
#        --database-root <db_storage> --keys-file <输出keys.json> --verify
#   bash rreader.sh setup --keys-file <keys.json> --database-root <目录>
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Git Bash 的 pwd 给出 POSIX 路径（/c/Users/...），Windows Python 不识别，
# 会导致 sys.path.insert 失效、wxconfig 找不到。转成 Windows 风格路径。
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) command -v cygpath >/dev/null 2>&1 && HERE="$(cygpath -w "$HERE")" ;;
esac
PY="${WECHAT_PYTHON:-python3}"
[ -x "$PY" ] || PY="python"

cfg() {
  "$PY" -c "
import sys
sys.path.insert(0, r'''$HERE''')
import wxconfig
print($1)
"
}

# 1) 带 sqlcipher3 的解释器
VP="${WECHAT_PYTHON:-}"
if [ -z "$VP" ]; then
  VP="$(cfg 'wxconfig.find_python(with_sqlcipher=True) or ""')"
fi
if [ -z "$VP" ] || [ "$VP" = "None" ]; then
  cat >&2 <<'EOF'
找不到带 sqlcipher3 的 Python。
  安装：pip install sqlcipher3 zstandard
  指定：export WECHAT_PYTHON=<解释器路径>
  自检：python scripts/wxconfig.py preflight
EOF
  exit 2
fi

# 2) 上游仓库
REPO="${WECHAT_HUB_HOME:-}"
if [ -z "$REPO" ]; then
  REPO="$(cfg 'str(wxconfig.autodiscover_repo() or "")')"
fi
if [ -z "$REPO" ] || [ "$REPO" = "None" ]; then
  cat >&2 <<'EOF'
找不到 wechat-intelligence-hub 仓库。
  克隆：git clone https://github.com/Rion-Wu-tech/wechat-intelligence-hub
  指定：export WECHAT_HUB_HOME=<仓库路径>
  自检：python scripts/wxconfig.py preflight
EOF
  exit 2
fi

READER="$REPO/projects/rion-wechat-reader/rion_wechat_reader.py"
ACCESS="$REPO/projects/rion-wechat-reader/rion_wechat_access.py"
COMPAT="$HERE/rion_compat.py"
ACL="$HERE/secure_acl.py"
PRIVATE_DIR="${WECHAT_PRIVATE_DIR:-$(cfg 'str(wxconfig.private_dir())')}"

if [ ! -f "$READER" ]; then
  echo "找不到 Reader: $READER" >&2
  exit 2
fi

mkdir -p "$PRIVATE_DIR"

# 幂等收紧 ACL：已 SAFE 会跳过
"$VP" "$ACL" "$PRIVATE_DIR" >/dev/null 2>&1 || true

cmd="${1:-}"
[ $# -gt 0 ] && shift || true

case "$cmd" in
  onboard|status|diagnose|plan|connect|finish-recovery|run)
    exec "$VP" "$COMPAT" "$ACCESS" "$cmd" "$@" ;;
  *)
    exec "$VP" "$COMPAT" "$READER" --strict-read-only "$cmd" "$@" ;;
esac