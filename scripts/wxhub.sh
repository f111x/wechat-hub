#!/usr/bin/env bash
# 情报引擎入口：替代上游面向 Codex 的 hub.sh
# 差异点：路径自动发现，不依赖 CODEX_HOME / 特定盘符 / 特定用户名
#
# 首次使用先跑：  python scripts/wxconfig.py preflight
#
# 用法：
#   bash wxhub.sh home
#   bash wxhub.sh scan <聊天导出文件...> --out <输出目录>
#   bash wxhub.sh brief --hours 24
#   bash wxhub.sh today
#   bash wxhub.sh person "联系人昵称"
#   bash wxhub.sh topic "AI培训" --days 7
#   bash wxhub.sh opportunities
set -euo pipefail
umask 077

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

# WECHAT_HUB_HOME 可以指仓库根，也可以直接指 projects 下的引擎目录
if [ -f "$REPO/wechat_intelligence_hub.py" ]; then
  PROJECT_DIR="$REPO"
else
  PROJECT_DIR="$REPO/projects/wechat-intelligence-hub"
fi

# Hub 是纯标准库，不强制要 sqlcipher3 那个解释器
PYTHON_BIN="${WECHAT_HUB_PYTHON:-${WECHAT_PYTHON:-}}"
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(cfg 'wxconfig.find_python(with_sqlcipher=False) or ""')"
fi
if [ -z "$PYTHON_BIN" ] || [ "$PYTHON_BIN" = "None" ]; then
  PYTHON_BIN="$PY"
fi

if [ ! -f "$PROJECT_DIR/wechat_intelligence_hub.py" ]; then
  echo "找不到情报引擎：$PROJECT_DIR" >&2
  echo "设置 WECHAT_HUB_HOME 指向仓库根或含 wechat_intelligence_hub.py 的目录" >&2
  exit 2
fi

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" wechat_intelligence_hub.py "$@"