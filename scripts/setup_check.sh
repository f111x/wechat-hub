#!/usr/bin/env bash
# 一键自检 + 配置引导
#
# 用法：
#   bash setup_check.sh            # 只检查，缺什么给什么修复建议
#   bash setup_check.sh --init     # 检查并交互式写入配置文件
#
# 设计原则：**不猜、不代劳、只提示**。
# 密钥捕获必须用户本人手动跑，本脚本不碰。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${WECHAT_PYTHON:-python3}"
[ -x "$PY" ] || PY="python"

INIT=0
[ "${1:-}" = "--init" ] && INIT=1

echo "=============================================================="
echo " wechat-hub 安装自检"
echo "=============================================================="

# 1) 前置条件
"$PY" "$HERE/wxconfig.py" preflight
RC=$?

echo
echo "--------------------------------------------------------------"
echo " 环境变量速查"
echo "--------------------------------------------------------------"
cat <<'EOF'
 WECHAT_HUB_HOME      上游仓库 wechat-intelligence-hub 的路径
 WECHAT_PYTHON        带 sqlcipher3 的 Python 解释器
 WECHAT_DB_ROOT       微信 db_storage 目录（多账号时指定具体那个）
 WECHAT_PRIVATE_DIR   密钥材料目录（必须在项目目录之外）
 WECHAT_HUB_DATA_DIR  导出数据根目录
 WECHAT_SELF_NAMES    你的微信昵称，逗号分隔，用于识别本人发言
EOF

# 2) 平台附加提示
echo
echo "--------------------------------------------------------------"
echo " 平台检查"
echo "--------------------------------------------------------------"
case "$(uname -s 2>/dev/null || echo Windows)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    echo "  [INFO] Windows：ACL 检查依赖 PowerShell。"
    echo "         若出现 unsafe_key_permissions，多半是密钥目录里有多余 ACL 项："
    echo "         icacls <目录> /remove:g <多余账户> /T"
    ;;
  Darwin)
    echo "  [INFO] macOS：wcdb-key 需 Xcode CLT（lldb）。"
    echo "         Reader 解密路径本 Skill 未验证，请先用 --layer 3 确认。"
    ;;
  *)
    echo "  [WARN] 该平台 Reader 解密未验证，仅 Hub 引擎可用。"
    ;;
esac

# 3) 交互式写配置
if [ "$INIT" = "1" ]; then
  echo
  echo "--------------------------------------------------------------"
  echo " 交互式配置"
  echo "--------------------------------------------------------------"
  read -r -p "上游仓库路径 [留空=自动发现]: " V_HUB
  read -r -p "带 sqlcipher3 的 Python [留空=自动探测]: " V_PY
  read -r -p "微信 db_storage 目录 [留空=自动发现]: " V_DB
  read -r -p "导出数据目录 [留空=./wechat-export]: " V_DATA
  read -r -p "你的微信昵称（逗号分隔）: " V_SELF

  "$PY" - "$V_HUB" "$V_PY" "$V_DB" "$V_DATA" "$V_SELF" <<'PYEOF'
import json, os, sys
sys.path.insert(0, os.environ.get("WECHAT_HUB_SCRIPTS", "."))
import wxconfig

hub, py, db, data, self_names = sys.argv[1:6]
cfg = {}
if hub:  cfg["hub_home"]   = os.path.expanduser(hub)
if py:   cfg["python"]     = os.path.expanduser(py)
if db:   cfg["db_root"]    = os.path.expanduser(db)
if data: cfg["data_dir"]   = os.path.expanduser(data)

f = wxconfig.config_file()
f.parent.mkdir(parents=True, exist_ok=True)
if f.is_file():
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
        old.update(cfg)
        cfg = old
    except json.JSONDecodeError:
        pass
f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n配置已写入：{f}")

if self_names.strip():
    print(f"\n提示：本人昵称请用环境变量指定，或在 shell 配置里加：")
    print(f'  export WECHAT_SELF_NAMES="{self_names.strip()}"')
print()
print("接下来：")
print("  1. 保持微信 PC 版运行，手动执行密钥捕获（脚本不代劳，见 README）")
print("  2. python wxconfig.py preflight   # 复检")
print("  3. python verify_wechat.py        # 全链路验证")
PYEOF
fi

echo "=============================================================="
if [ "$RC" = "0" ]; then
  echo " 全部依赖就绪，可执行密钥捕获（README「首次接入」一节）"
else
  echo " 存在未满足项，见上方 FAIL 与修复建议"
fi
echo "=============================================================="
exit "$RC"