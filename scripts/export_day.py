"""批量导出指定日期的微信聊天记录为 jsonl。

用法：
    python export_day.py 2026-10-14 --out <输出目录>

策略：
    - private（私聊）：全量导出，优先级最高
    - group（群聊）：关键词命中才全量导出；未命中的只导出最近 N 条做话题识别
    - official_account（公众号）：只导最近 N 条（多为推送，无对话价值）
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import wxconfig  # noqa: E402
from verify_wechat import READER, SCRIPTS, rreader  # noqa: E402

# 命中即全量导出的群关键词（老大实际在用的价值群）
FOCUS_KEYWORDS = (
    "FDE", "一人公司", "AI破局", "AI编程", "极客时间", "待报价", "接单",
    "客户", "合作", "商单", "出海", "工具", "智能体", "Claude", "Codex",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--out", default=None,
                    help="输出目录，默认 <WECHAT_HUB_DATA_DIR>/<日期>")
    ap.add_argument("--group-sample", type=int, default=60,
                    help="未命中关键词的群导出条数")
    ap.add_argument("--oa-sample", type=int, default=10,
                    help="公众号导出条数")
    args = ap.parse_args()

    day = dt.date.fromisoformat(args.date)
    lo = dt.datetime.combine(day, dt.time(0, 0)).timestamp()
    hi = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0)).timestamp()

    out_dir = Path(args.out or (wxconfig.default_data_dir() / args.date))
    out_dir.mkdir(parents=True, exist_ok=True)

    sessions = rreader("sessions", "--limit", "300").get("data", {}).get("sessions", [])
    active = [s for s in sessions
              if lo <= int(s.get("sort_timestamp") or 0) < hi]
    print(f"{args.date} 活动会话：{len(active)}")

    buckets = {"private": [], "group": [], "official_account": []}
    for s in active:
        buckets.setdefault(str(s.get("chat_type")), []).append(s)

    plan = []
    for s in buckets.get("private", []):
        plan.append((s, None, "full"))
    for s in buckets.get("group", []):
        name = str(s.get("display_name") or "")
        hit = any(k in name for k in FOCUS_KEYWORDS)
        plan.append((s, None, "full" if hit else args.group_sample))
    for s in buckets.get("official_account", []):
        plan.append((s, None, args.oa_sample))

    print(f"待导出：私聊 {len(buckets.get('private', []))} / "
          f"群 {len(buckets.get('group', []))} / "
          f"公众号 {len(buckets.get('official_account', []))}")

    total_msgs = 0
    ok, fail = 0, []
    manifest = []

    for idx, (s, _, mode) in enumerate(plan, 1):
        chat = s.get("username")
        name = str(s.get("display_name") or chat)
        safe = "".join(c if c.isalnum() or c in "-_一-鿿" else "_" for c in name)[:40]
        kind = str(s.get("chat_type"))
        path = out_dir / f"{kind}__{safe}.jsonl"

        limit = 100000 if mode == "full" else int(mode)
        try:
            if path.exists():
                path.unlink()
            res = rreader("export-messages", chat, "--format", "jsonl",
                          "--view", "agent", "--limit", str(limit),
                          "--after", str(int(lo)), "--before", str(int(hi)),
                          "--path", str(path))
            if not res.get("ok"):
                fail.append((name, str(res.get("error"))[:60]))
                continue
            rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            n = len(rows)
            if n == 0:
                path.unlink(missing_ok=True)
                continue
            total_msgs += n
            ok += 1
            manifest.append({
                "chat": chat, "name": name, "chat_type": kind,
                "messages": n, "file": path.name, "mode": mode,
                "unread_at_export": s.get("unread_count", 0),
            })
            print(f"  [{idx}/{len(plan)}] {n:5d} 条  {name[:36]}")
        except Exception as exc:
            fail.append((name, f"{type(exc).__name__}: {exc}"))
        time.sleep(0.15)

    (out_dir / "manifest.json").write_text(
        json.dumps({"date": args.date, "sessions": len(active),
                    "exported": ok, "total_messages": total_msgs,
                    "failed": fail, "items": manifest},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成：{ok} 个会话，{total_msgs} 条消息")
    if fail:
        print(f"失败 {len(fail)} 个：")
        for n, e in fail[:10]:
            print(f"  - {n}: {e}")
    print(f"输出目录：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())