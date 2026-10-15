"""微信接入验证器：分层验证「密钥 -> 解密 -> 读取 -> 情报」全链路。

每层都是可复现的客观检查，不依赖上一次的结论。
    python verify_wechat.py            # 跑全部 5 层
    python verify_wechat.py --layer 4  # 只跑第4 层
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import wxconfig  # noqa: E402  (配置层，必须在路径常量之前导入)

DB_ROOT = wxconfig.autodiscover_db_root()
KEYS_JSON = wxconfig.wcdb_key_dir() / "wechat-passphrase.keys.json"
MATERIAL = wxconfig.private_dir() / "material.json"
VP = wxconfig.find_python(with_sqlcipher=True) or sys.executable
PRIVATE_DIR = wxconfig.private_dir()

_results: list[tuple[str, bool, str]] = []


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """跑外部命令并正确解码。

    Windows 上 bash 子进程输出可能是 GBK（CP936），不是 UTF-8；
    强解会抛 UnicodeDecodeError。errors='replace' 保证永不崩。
    """
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


SCRIPTS = Path(__file__).parent
_REPO = wxconfig.autodiscover_repo()
READER = (_REPO / "projects" / "rion-wechat-reader" / "rion_wechat_reader.py"
          if _REPO else None)
COMPAT = SCRIPTS / "rion_compat.py"
HUB = (_REPO / "projects" / "wechat-intelligence-hub" / "wechat_intelligence_hub.py"
       if _REPO else None)


def rreader(*args: str) -> dict:
    """直接调 Reader，绕过 bash。

    不走 rreader.sh 是因为 Python 里 subprocess(["bash", ...]) 在 Windows 上
    命不中 Git Bash（PATH 差异）。这里复刻 rreader.sh 的核心：兼容补丁 + 只读。
    """
    src = READER.read_text(encoding="utf-8")
    ns = {"__name__": "_verify", "__file__": str(READER)}
    exec(compile(src, str(READER), "exec"), ns)

    # 复用 compat 的补丁（export 需放宽输出目录 ACL）
    compat_src = COMPAT.read_text(encoding="utf-8")
    cns = {"__name__": "_compat", "__file__": str(COMPAT)}
    exec(compile(compat_src, str(COMPAT), "exec"), cns)
    cns["patch"]()
    argv = [a for a in args if not a.startswith("-")]
    if argv and argv[0].startswith("export"):
        cns["_READER_NS"].clear()
        cns["_READER_NS"].update(ns)
        cns["_patch_export_acl_scope"]()
        ns.clear()
        ns.update(cns["_READER_NS"])

    old_argv = sys.argv
    sys.argv = [str(READER), "--strict-read-only", *args]
    import io
    import contextlib

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ns["main"](sys.argv[1:])
    finally:
        sys.argv = old_argv

    lines = [ln for ln in buf.getvalue().splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"Reader 无 JSON 输出: {buf.getvalue()[-300:]}")
    return json.loads(lines[-1])


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def layer1_keys() -> bool:
    """第1 层：密钥材料齐全且条数一致。"""
    print("\n[1] 密钥材料")
    if not KEYS_JSON.is_file():
        check("wcdb-key 原始输出存在", False, str(KEYS_JSON))
        return False
    if not MATERIAL.is_file():
        check("Reader 材料存在", False, str(MATERIAL))
        return False
    raw = json.loads(KEYS_JSON.read_text(encoding="utf-8"))
    mat = json.loads(MATERIAL.read_text(encoding="utf-8"))
    check("wcdb-key 输出条数", len(raw) == 20, f"{len(raw)} 条")
    check("Reader 材料条数", len(mat.get("keys", {})) == len(raw), f"{len(mat.get('keys', {}))} 条")
    bad = [k for k, v in raw.items() if len(str(v)) != 64]
    check("所有 key 为 64 位 hex", not bad, f"异常 {bad[:3]}" if bad else "全部合规")
    return all(ok for _, ok, _ in _results)


def layer2_acl() -> bool:
    """第 2 层：密钥文件 ACL 收紧。"""
    print("\n[2] 密钥文件权限")
    import subprocess

    script = str(Path(__file__).parent / "secure_acl.py")
    r = _run([VP, script, "--check", str(PRIVATE_DIR)])
    safe = "RESULT SAFE" in r.stdout
    check("ACL 仅当前用户 + SYSTEM", safe, r.stdout.strip().replace("\n", " / "))
    return safe


def layer3_decrypt() -> bool:
    """第 3 层：每个库用真实 key 真解密，能读出 sqlite_master。"""
    print("\n[3] 逐库解密（最硬的证据）")
    import sqlcipher3.dbapi2 as db

    keys = json.loads(KEYS_JSON.read_text(encoding="utf-8"))
    located = {}
    for path in DB_ROOT.rglob("*.db"):
        located.setdefault(path.name, path)

    ok_count, fail = 0, []
    for name, key in keys.items():
        target = located.get(name)
        if target is None:
            fail.append((name, "文件缺失"))
            continue
        try:
            conn = db.connect(str(target))
            conn.execute(f"PRAGMA key = \"x'{key}'\"")
            conn.execute("PRAGMA cipher_page_size = 4096")
            objs = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            conn.close()
            if objs > 0:
                ok_count += 1
            else:
                fail.append((name, "空 schema"))
        except Exception as exc:
            fail.append((name, str(exc)[:50]))

    check(f"数据库解密成功", ok_count == len(keys), f"{ok_count}/{len(keys)}")
    if fail:
        print(f"       失败: {fail[:5]}")
    return not fail


def layer4_read() -> bool:
    """第 4 层：读到真实会话与消息内容（不只是能解密）。"""
    print("\n[4] 真实数据读取")

    stats = rreader("stats").get("data", {})
    check("会话数 > 0", stats.get("session_count", 0) > 0,
          f"{stats.get('session_count')} 个会话")
    check("联系人 > 0", stats.get("contact_count", 0) > 0,
          f"{stats.get('contact_count')} 个联系人")
    check("消息分表 > 0", stats.get("message_table_count", 0) > 0,
          f"{stats.get('message_table_count')} 张表")

    sess = rreader("sessions", "--limit", "20").get("data", {}).get("sessions", [])
    # 只认真实群组（username 以 @chatroom 结尾），服务号/折叠会话没有可导出正文
    groups = [s for s in sess if str(s.get("username", "")).endswith("@chatroom")]
    named = [s for s in groups if s.get("display_name")]
    check("会话可解析出群名/昵称", bool(named),
          f"{len(groups)} 个群组 / {named[0]['display_name']}" if named else "无群组")

    if named:
        chat = named[0]["username"]
        out = wxconfig.default_data_dir() / "_verify.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        res = rreader("export-messages", chat, "--format", "jsonl",
                      "--view", "agent", "--limit", "20", "--path", str(out))
        if res.get("ok"):
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
            has_text = [r for r in rows if r.get("text")]
            check("导出真实消息", len(rows) > 0, f"{len(rows)} 条")
            check("消息含正文", len(has_text) == len(rows), f"{len(has_text)}/{len(rows)} 条有正文")
            if has_text:
                sample = has_text[0]
                check("含发送者与时间戳",
                      bool(sample.get("sender")) and bool(sample.get("time")),
                      f"{sample.get('sender')} @ {sample.get('time')}")
            out.unlink()
        else:
            check("导出真实消息", False, str(res.get("error")))
    return all(ok for name, ok, _ in _results if name.startswith(("会话", "联系人", "消息", "导出")))


def load_hub():
    """正规 import Hub 模块（不能 exec）。

    Hub 里有 @dataclass，dataclasses 需要 sys.modules[cls.__module__]
    存在才能解析注解；裸 exec 出来的 namespace 未注册模块 →
    `AttributeError: 'NoneType' object has no attribute '__dict__'`。
    """
    import importlib

    sys.path.insert(0, str(HUB.parent))
    try:
        return importlib.import_module(HUB.stem)
    finally:
        if str(HUB.parent) in sys.path:
            sys.path.remove(str(HUB.parent))


def layer5_hub() -> bool:
    """第 5 层：情报引擎能解析导出数据并抽出信号。"""
    print("\n[5] 情报引擎")
    import subprocess
    import tempfile

    # 取当天导出目录里最大的 jsonl 作为样本；没有则现导一个群
    data_dir = wxconfig.default_data_dir()
    samples = sorted(data_dir.glob("*/*.jsonl"), key=lambda p: p.stat().st_size, reverse=True) \
        if data_dir.is_dir() else []
    if not samples:
        check("找到导出样本", False, f"{data_dir} 下无 *.jsonl，先跑 export_day.py")
        return False
    src = samples[0]
    with tempfile.TemporaryDirectory() as td:
        import io
        import contextlib

        buf = io.StringIO()
        old_argv = sys.argv
        old_cwd = os.getcwd()
        # Hub 的相对路径基于它自己的目录
        os.chdir(HUB.parent)
        hub_mod = load_hub()
        sys.argv = ["wechat_intelligence_hub.py", "scan", str(src), "--out", td]
        try:
            with contextlib.redirect_stdout(buf):
                hub_mod.main()
        except SystemExit:
            pass
        finally:
            out = buf.getvalue()
            sys.argv = old_argv
            os.chdir(old_cwd)
        msgs = signals = 0
        for line in out.splitlines():
            if "读取消息：" in line:
                msgs = int(line.split("读取消息：")[1].split()[0])
            if "识别信号：" in line:
                signals = int(line.split("识别信号：")[1].split()[0])
        check("Hub 读取消息", msgs > 0, f"{msgs} 条")
        check("Hub 识别信号", signals > 0, f"{signals} 条")
        produced = list(Path(td).glob("*.json")) + list(Path(td).glob("*.csv"))
        check("产出情报文件", len(produced) >= 3, f"{len(produced)} 个")
    return True


LAYERS = [
    ("1", layer1_keys),
    ("2", layer2_acl),
    ("3", layer3_decrypt),
    ("4", layer4_read),
    ("5", layer5_hub),
]


def _guard() -> bool:
    """前置条件不足时给出可操作提示，而不是抛一堆 KeyError。"""
    missing = []
    if DB_ROOT is None or not DB_ROOT.is_dir():
        missing.append("微信数据库目录：设置 WECHAT_DB_ROOT 或 WECHAT_HUB_DATA_DIR，"
                       "确认微信 PC 版登录过")
    if READER is None or not READER.is_file():
        missing.append("上游仓库：git clone https://github.com/Rion-Wu-tech/wechat-intelligence-hub，"
                       "然后 export WECHAT_HUB_HOME=<仓库路径>")
    if HUB is None or not HUB.is_file():
        missing.append("情报引擎：同上，需在 WECHAT_HUB_HOME 下能找到 projects/wechat-intelligence-hub")
    if missing:
        print("前置条件未满足：\n", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print("\n完整自检：python wxconfig.py preflight", file=sys.stderr)
        return False
    return True


def main() -> int:
    only = None
    if "--layer" in sys.argv:
        only = sys.argv[sys.argv.index("--layer") + 1]

    print("=" * 60)
    print("微信接入链路验证")
    print("=" * 60)

    if not _guard():
        return 2

    for tag, fn in LAYERS:
        if only and tag != only:
            continue
        before = len(_results)
        try:
            fn()
        except Exception as exc:
            check(f"第 {tag} 层异常", False, f"{type(exc).__name__}: {exc}")

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print("\n" + "=" * 60)
    print(f"结果：{passed}/{total} 项通过")
    if passed == total:
        print("结论：接入已打通，全链路可用。")
    else:
        print("结论：存在失败项，见上方 FAIL。")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())