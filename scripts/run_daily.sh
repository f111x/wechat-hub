#!/usr/bin/env bash
# 微信情报日报 —— 一键端到端（导出 → 生成日报 → 推送）
#
# 供定时任务调用。设计原则：
#   1. 每一步失败都要看得见，但不要因为推送失败判定整个任务失败
#   2. 幂等：同一天重跑会覆盖旧产物，不会重复堆积
#   3. 只读：全程不发送任何微信消息
#
# 用法：
#   bash run_daily.sh              # 处理昨天
#   bash run_daily.sh 2026-10-16   # 指定日期
#   bash run_daily.sh --no-export  # 跳过导出（已有导出数据时）
set -uo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) command -v cygpath >/dev/null 2>&1 && HERE="$(cygpath -w "$HERE")" ;;
esac

# 引导解释器：只用来查一次配置，不解密数据
PYTHON_BOOTSTRAP="${WECHAT_PYTHON_BOOTSTRAP:-}"
if [ -z "$PYTHON_BOOTSTRAP" ] || [ ! -x "$PYTHON_BOOTSTRAP" ]; then
  PYTHON_BOOTSTRAP="python"
  command -v python >/dev/null 2>&1 || PYTHON_BOOTSTRAP="python3"
fi

# ★ 优先用带 sqlcipher3 的那个解释器（导出要解密数据库）。
#   借 wxconfig.find_python 自动找，找不到才退回系统 python。
PY="${WECHAT_PYTHON:-}"
if [ -z "$PY" ]; then
  PY="$("$PYTHON_BOOTSTRAP" -c "
import sys
sys.path.insert(0, r'''$HERE''')
try:
    import wxconfig
    print(wxconfig.find_python(with_sqlcipher=True) or '')
except Exception:
    print('')
" 2>/dev/null)"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  PY="python"
  command -v python >/dev/null 2>&1 || PY="python3"
fi

DATE_ARG="yesterday"
DO_EXPORT=1
for a in "$@"; do
  case "$a" in
    --no-export) DO_EXPORT=0 ;;
    -*) echo "未知参数：$a" >&2; exit 2 ;;
    *) DATE_ARG="$a" ;;
  esac
done

# 解析实际日期
DAY="$("$PY" - "$DATE_ARG" <<'PYEOF'
import sys, datetime as dt
a = sys.argv[1]
print((dt.date.today() - dt.timedelta(days=1)).isoformat() if a in ("yesterday", "昨天") else a)
PYEOF
)"
[ -z "$DAY" ] && { echo "日期解析失败" >&2; exit 2; }

echo "═══════════════════════════════════════"
echo " wechat-hub 每日情报  ·  $DAY"
echo "═══════════════════════════════════════"

if [ "$DO_EXPORT" = "1" ]; then
  echo ""
  echo "[1/3] 导出聊天记录…"
  "$PY" "$HERE/export_day.py" "$DAY"
  RC=$?
  if [ $RC -ne 0 ]; then
    echo "[WARN] 导出返回码 $RC —— 可能微信未登录或库被占用，继续尝试用已有数据生成日报"
  fi
else
  echo "[1/3] 跳过导出（--no-export）"
fi

echo ""
echo "[2/3] 生成日报…"
"$PY" "$HERE/daily_digest.py" "$DAY"
RC2=$?
if [ $RC2 -ne 0 ]; then
  echo "[ERROR] 日报生成失败（返回码 $RC2）" >&2
  exit $RC2
fi

echo ""
echo "[3/3] 推送…"
"$PY" "$HERE/push_daily.py" "$DAY"
RC3=$?
if [ $RC3 -ne 0 ]; then
  echo "[WARN] 推送返回码 $RC3，但日报文件已落盘，不判任务失败" >&2
fi

echo ""
echo "完成。日报目录：<data_dir>/digest/"
exit 0
