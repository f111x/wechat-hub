"""微信情报链路 —— 统一配置解析。

设计目标：**别人 clone 下来就能跑**，不依赖任何特定机器的路径。

配置优先级（后者覆盖前者）：
    1. 内置默认值（基于 $HOME / %USERPROFILE% 推导，跨平台）
    2. 配置文件 ~/.wechat-hub/config.json
    3. 环境变量 WECHAT_HUB_*
    4. 命令行参数

环境变量清单见 README「依赖条件」一节。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "wechat-hub"

# ---------------------------------------------------------------------------
# 基础目录推导（不依赖任何硬编码盘符）
# ---------------------------------------------------------------------------

def home() -> Path:
    """用户主目录，跨平台。"""
    return Path(os.path.expanduser("~"))


def config_dir() -> Path:
    """本工具的配置目录：Windows 走 %LOCALAPPDATA%，其他走 ~/.config。"""
    override = os.environ.get("WECHAT_HUB_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(home() / ".config")
    return Path(base) / APP_NAME


def config_file() -> Path:
    return config_dir() / "config.json"


def _load_config() -> dict:
    f = config_file()
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            warn(f"配置文件解析失败，已忽略：{f}（{exc}）")
            return {}
    return {}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

_WARNED: set[str] = set()


def warn(msg: str) -> None:
    """打印一次性的警告（同一 key 只打一次，避免刷屏）。"""
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"[{APP_NAME}] {msg}", file=sys.stderr)


def autodiscover_repo() -> Path | None:
    """自动找到 wechat-intelligence-hub 仓库。

    查找顺序：
      1. 环境变量 WECHAT_HUB_HOME（指向含 projects/ 的目录，或仓库根）
      2. 配置文件 hub_home
      3. 常见位置：~/wechat-intelligence-hub、~/github/、../、./ 附近
    """
    cfg = _load_config()
    candidates: list[Path] = []

    env_home = os.environ.get("WECHAT_HUB_HOME")
    if env_home:
        candidates.append(Path(env_home))
    if cfg.get("hub_home"):
        candidates.append(Path(cfg["hub_home"]))

    # 从 Skill 所在位置向上找（Skill 常被放在 ~/xxx/skills/wechat-hub）
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "wechat-intelligence-hub":
            candidates.append(parent)
    for parent in list(here.parents)[:4]:
        candidates.append(parent / "wechat-intelligence-hub")

    candidates += [
        home() / "wechat-intelligence-hub",
        home() / "github" / "wechat-intelligence-hub",
        home() / "src" / "wechat-intelligence-hub",
        Path.cwd() / "wechat-intelligence-hub",
    ]

    for c in candidates:
        try:
            if c.is_dir() and (c / "projects").is_dir():
                return c.resolve()
        except OSError:
            continue
    return None


def find_python(with_sqlcipher: bool = True) -> str | None:
    """找一个能 import sqlcipher3 的 Python 解释器。

    with_sqlcipher=True 时只认带 sqlcipher3 的（Reader 需要），
    否则任何 python3 都行（Hub 是纯标准库）。
    """
    override = os.environ.get("WECHAT_PYTHON")
    if override:
        return override

    cfg = _load_config()
    if cfg.get("python"):
        return cfg["python"]

    names = ["python3", "python"] if sys.platform != "win32" else ["python", "python3"]
    for name in names:
        found = shutil.which(name)
        if not found:
            continue
        if not with_sqlcipher:
            return found
        # 检查 sqlcipher3
        probe = subprocess_ok(found)
        if probe:
            return found
    return None


def subprocess_ok(python_exe: str) -> bool:
    """检测解释器能否 import sqlcipher3 与 zstandard。"""
    import subprocess

    try:
        r = subprocess.run(
            [python_exe, "-c", "import sqlcipher3, zstandard"],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def autodiscover_db_root() -> Path | None:
    """自动定位微信数据库目录（含 db_storage 子目录）。

    微信 4.x 默认在 ~/Documents/xwechat_files/<账号>/db_storage。
    """
    env = os.environ.get("WECHAT_DB_ROOT")
    if env:
        return Path(env)
    cfg = _load_config()
    if cfg.get("db_root"):
        return Path(cfg["db_root"])

    base = home() / "Documents" / "xwechat_files"
    if not base.is_dir():
        # macOS / 部分配置可能在别处
        for alt in (home() / "Library" / "Containers" / "com.tencent.xinWeChat",
                    home() / ".wine" / "drive_c" / "users"):
            if alt.is_dir():
                base = alt
                break
    if not base.is_dir():
        return None

    # 多个账号时挑最近改动的
    cands = [p / "db_storage" for p in base.iterdir()
             if p.is_dir() and (p / "db_storage").is_dir()]
    if not cands:
        return None
    try:
        return max(cands, key=lambda p: p.stat().st_mtime).resolve()
    except OSError:
        return cands[0].resolve()


def private_dir() -> Path:
    """密钥材料目录（必须在项目目录之外，且 ACL 收紧）。"""
    env = os.environ.get("WECHAT_PRIVATE_DIR")
    if env:
        return Path(env)
    cfg = _load_config()
    if cfg.get("private_dir"):
        return Path(cfg["private_dir"])
    return home() / ".wechat-private"


def wcdb_key_dir() -> Path:
    env = os.environ.get("WECHAT_KEY_DIR")
    if env:
        return Path(env)
    return home() / ".wcdb-key"


def reader_config_dir() -> Path:
    env = os.environ.get("WECHAT_READER_CONFIG_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(home() / "AppData" / "Roaming")
        return Path(base) / "rion-wechat-reader"
    base = os.environ.get("XDG_CONFIG_HOME") or str(home() / ".config")
    return Path(base) / "rion-wechat-reader"


def default_data_dir() -> Path:
    """导出数据默认落点。用配置或当前工作目录，不假定 Obsidian 结构。"""
    env = os.environ.get("WECHAT_HUB_DATA_DIR")
    if env:
        return Path(env)
    cfg = _load_config()
    if cfg.get("data_dir"):
        return Path(cfg["data_dir"])
    return Path.cwd() / "wechat-export"


def is_windows() -> bool:
    return sys.platform == "win32"


def powershell_exe() -> str | None:
    if not is_windows():
        return shutil.which("pwsh") or shutil.which("pwsh.exe")
    return shutil.which("pwsh.exe") or shutil.which("powershell.exe")


# ---------------------------------------------------------------------------
# 前置条件自检
# ---------------------------------------------------------------------------

def preflight(verbose: bool = True) -> dict:
    """检查所有依赖是否就位，返回结构化结果。

    这是给别人用之前必须跑的一步。
    """
    checks: dict[str, dict] = {}

    def add(name: str, ok: bool, detail: str = "", fix: str = ""):
        checks[name] = {"ok": ok, "detail": detail, "fix": fix}

    # 1. 平台
    add("平台", True, f"{sys.platform} / Python {sys.version.split()[0]}",
        "")

    # 2. 上游仓库
    repo = autodiscover_repo()
    add("上游仓库 wechat-intelligence-hub", repo is not None,
        str(repo) if repo else "未找到",
        "git clone https://github.com/Rion-Wu-tech/wechat-intelligence-hub "
        "然后 export WECHAT_HUB_HOME=<路径>")

    # 3. 带sqlcipher 的 Python
    py = find_python(with_sqlcipher=True)
    add("Python + sqlcipher3 + zstandard", py is not None,
        py or "未找到可用解释器",
        "pip install sqlcipher3 zstandard；或 export WECHAT_PYTHON=<解释器路径>")

    # 4. 微信数据库
    db = autodiscover_db_root()
    n_db = 0
    if db and db.is_dir():
        n_db = len(list(db.rglob("*.db")))
    add("微信数据库目录", db is not None and n_db > 0,
        f"{db}（{n_db} 个 .db）" if db else "未找到 db_storage",
        "确认微信 PC 版已登录过；或 export WECHAT_DB_ROOT=<db_storage 路径>")

    # 5. 密钥
    keys = wcdb_key_dir() / "wechat-passphrase.keys.json"
    mat = private_dir() / "material.json"
    has_keys = keys.is_file()
    has_mat = mat.is_file()
    add("密钥材料", has_mat,
        f"material.json {'存在' if has_mat else '缺失'}；"
        f"原始 keys {'存在' if has_keys else '缺失'}",
        "运行 wcdb-key capture（需微信 PC 版在运行），再用 wxkey_to_reader.py 转换")

    # 6. PowerShell（仅 Windows ACL 检查需要）
    if is_windows():
        ps = powershell_exe()
        add("PowerShell（ACL 检查）", ps is not None, ps or "未找到",
            "安装 PowerShell 7 或使用 Windows 自带的 powershell.exe")

    if verbose:
        print(f"\n{'=' * 62}")
        print(f"{APP_NAME} 前置条件检查")
        print("=" * 62)
        for name, info in checks.items():
            mark = "PASS" if info["ok"] else "FAIL"
            print(f"  [{mark}] {name}")
            if info["detail"]:
                print(f"         {info['detail']}")
            if not info["ok"] and info["fix"]:
                print(f"         → {info['fix']}")
        passed = sum(1 for i in checks.values() if i["ok"])
        print("-" * 62)
        print(f"  {passed}/{len(checks)} 项通过")
        if passed < len(checks):
            print("  存在 FAIL 项，按上面的 → 提示处理")
        print("=" * 62)

    return checks


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "preflight":
        result = preflight()
        return 0 if all(i["ok"] for i in result.values()) else 1

    # 无参数时打印当前生效的配置
    print(json.dumps({
        "config_file": str(config_file()),
        "config_file_exists": config_file().is_file(),
        "hub_home": str(autodiscover_repo() or "(未找到)"),
        "python": find_python(with_sqlcipher=True) or "(未找到)",
        "db_root": str(autodiscover_db_root() or "(未找到)"),
        "private_dir": str(private_dir()),
        "keys_file": str(wcdb_key_dir() / "wechat-passphrase.keys.json"),
        "reader_config": str(reader_config_dir()),
        "data_dir": str(default_data_dir()),
        "platform": sys.platform,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())