"""微信情报日报 —— 推送通道。

设计原则（三条）：
1. **webhook 只从环境变量读**，绝不写进文件（密钥安全红线）。
2. **推送失败绝不影响日报产出** —— 日报文件已经落盘了，推送挂了不能白干。
3. **可 dry-run** —— 定时任务正式跑之前必须能先看内容对不对。

支持通道：
- dingtalk   钉钉群机器人（推荐，配置最简单）
- wecom      企业微信群机器人
- console    只打印，不发网络请求（默认，没配 webhook 时的安全兜底）

用法：
    python push_daily.py 2026-10-16 --dry-run
    python push_daily.py yesterday --channel dingtalk
    python push_daily.py 2026-10-16 --channel dingtalk --test     # 只测连通性
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import wxconfig  # noqa: E402

MAX_DINGTALK = 4500# 钉钉 text 单条上限
MAX_WECOM = 4096      # 企微 text 上限

LOG_DIR_NAME = "digest"


def log_path() -> Path:
    d = Path(wxconfig.default_data_dir()) / LOG_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d / "push_log.jsonl"


def write_log(rec: dict) -> None:
    with log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n…（内容超长已截断）"


def brief_text(digest: dict) -> str:
    """把结构化日报压成推送消息体。

    ★ 设计取舍：推送里不放全文。
      钉钉/企微都是 IM，500 行 Markdown 没人看。
      推送只给「数字 + 前几条」，全文留在本地 md 文件路径。
    """
    c = digest["coverage"]
    L: list[str] = []
    L.append(f"【微信情报日报】{digest['date']}")
    L.append("")
    L.append(f"覆盖 {c['sessions']} 个会话 / 原始 {c['raw_messages']} 条")
    L.append(f"过滤后剩余可行动 {c['actionable']} 条")
    L.append(f"（媒体 {c['media_noise']} · 推广 {c['promo_filtered']} · 机器人 {c['bot_filtered']} · "
             f"转发去重 {c['forward_deduped']}）")
    L.append("")

    conf = [a for a in digest["immediate"] if a["confidence"] == "已确认"]
    L.append(f"已确认行动项 {len(conf)} 条｜高概率 {len(digest['immediate']) - len(conf)} 条")
    if digest["immediate"]:
        L.append("")
        for i, a in enumerate(digest["immediate"][:5], 1):
            fwd = f"（转发 {a['forwarded']} 处）" if a.get("forwarded") else ""
            body = a["text"].replace("\n", " ")[:52]
            L.append(f"{i}. [{a['confidence']}] {a['name'][:14]}｜{body}{fwd}")
    else:
        L.append("当日无可行动对话。")

    op = digest.get("opportunities") or []
    if op:
        L.append("")
        L.append(f"—— 商单线索 {len(op)} 条 ——")
        for a in op[:3]:
            L.append(f"· {a['name'][:16]}：{a['text'].replace(chr(10), ' ')[:46]}")

    L.append("")
    L.append("（全程只读本机微信，原始记录不出本机）")
    return "\n".join(L)


def post_json(url: str, payload: dict, timeout: int = 12) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


def push_dingtalk(webhook: str, text: str) -> tuple[bool, str]:
    try:
        st, body = post_json(webhook, {"msgtype": "text",
                                       "text": {"content": truncate(text, MAX_DINGTALK)}})
        ok = st == 200 and '"errcode":0' in body.replace(" ", "")
        return ok, f"HTTP {st} {body[:120]}"
    except urllib.error.URLError as e:
        return False, f"网络错误 {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def push_wecom(webhook: str, text: str) -> tuple[bool, str]:
    try:
        st, body = post_json(webhook, {"msgtype": "text",
                                       "text": {"content": truncate(text, MAX_WECOM)}})
        ok = st == 200 and '"errcode":0' in body.replace(" ", "")
        return ok, f"HTTP {st} {body[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default="yesterday", help="YYYY-MM-DD 或 yesterday")
    ap.add_argument("--channel", default=None,
                    choices=["dingtalk", "wecom", "console"],
                    help="缺省读环境变量 WECHAT_PUSH_CHANNEL，兜底 console")
    ap.add_argument("--webhook", default=None,
                    help="临时覆盖；缺省读环境变量，绝不写入文件")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--test", action="store_true", help="只发一条连通性测试消息")
    args = ap.parse_args()

    ch = args.channel or os.environ.get("WECHAT_PUSH_CHANNEL", "console")
    day = (dt.date.today() - dt.timedelta(days=1)).isoformat() \
        if args.date in ("yesterday", "昨天") else args.date

    jf = Path(wxconfig.default_data_dir()) / LOG_DIR_NAME / f"{day}.json"
    if not jf.exists():
        print(f"找不到 {jf}")
        print("先跑 daily_digest.py 生成日报。")
        return 2
    digest = json.loads(jf.read_text(encoding="utf-8"))

    if args.test:
        text = f"【连通性测试】wechat-hub 推送通道正常 · {dt.datetime.now():%Y-%m-%d %H:%M}"
    else:
        text = brief_text(digest)

    print("=" * 60)
    print(text)
    print("=" * 60)

    if args.dry_run:
        print("[DRY-RUN] 未发送")
        return 0

    if ch == "console":
        print("[SKIP] 通道=console，未配置推送，只打印")
        write_log({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                   "date": day, "channel": "console", "ok": None,
                   "note": "未配置 webhook"})
        return 0

    # ★ 只从环境变量或命令行取，绝不落盘
    wh = args.webhook or os.environ.get(
        "DINGTALK_WEBHOOK" if ch == "dingtalk" else "WECOM_WEBHOOK", "")
    if not wh:
        env = "DINGTALK_WEBHOOK" if ch == "dingtalk" else "WECOM_WEBHOOK"
        print(f"[SKIP] 未配置 {env}，只打印不推送")
        write_log({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                   "date": day, "channel": ch, "ok": False,
                   "note": f"未配置 {env}"})
        return 0

    fn = push_dingtalk if ch == "dingtalk" else push_wecom
    ok, detail = fn(wh, text)
    print(f"[{ch.upper()}] {'成功' if ok else '失败'}：{detail}")
    write_log({"ts": dt.datetime.now().isoformat(timespec="seconds"),
               "date": day, "channel": ch, "ok": ok, "detail": detail,
               "mode": "test" if args.test else "digest"})
    # ★ 推送失败返回 0：日报已经落盘，不该因此判定整个任务失败
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
