"""从一天的聊天记录里提取真实文本信号，过滤 Hub 对图片/表情的误判。

Hub 的 stage 分类会把 <img aeskey=...> 之类的 XML 也算成「待创作」，
日报必须基于真实可读文本重做一遍。
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
from pathlib import Path

NOISE = re.compile(
    r"(aeskey=|cdnurl|<img|<emoji|cdn\.|wx_fmt|<\?xml|<msg>|appmsg|thumburl|"
    r"^\[图片\]|^\[表情\]|^\[动画表情\]|^\[语音\]|^\[视频\]|^\[文件\]|"
    r"撤回了一条消息|^\[链接\]|^https?://)",
    re.I,
)

# 真实待办 / 机会的触发词（比Hub 严格，避免同业讨论被算成商机）
ACTION_PATTERNS = {
    "待跟进": (r"(帮我|能否|可以|麻烦).{0,12}(看下|看下|发我|给|提供|确认|报价)",),
    "报价/预算": (r"(报价|预算|多少钱|价格|费用|报价单|万元|元/|USD|\$\d)",),
    "时间承诺": (r"(截止|deadline|最晚|之前给|需要.{0,6}前|周[一二三四五六日]前|今天内|明天)",),
    "合作意向": (r"(合作|一起做|联名|联合|找个人|有人要|求带|带我|收徒|接单|有偿)",),
    "交付/结算": (r"(已发布|已交付|结款|结算|发票|打款|尾款|首款|已付款|到账)",),
    "邀约": (r"(约个|约一|见面|电话|加个微信|私聊|回头联系|保持联系|节日快乐)",),
}


def load_texts(day_dir: Path):
    """读取全部 jsonl，返回(文本, 会话名, 类型)列表。"""
    rows = []
    for f in sorted(day_dir.glob("*.jsonl")):
        chat = f.stem
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = str(d.get("text") or "").strip()
            if not t or NOISE.search(t):
                continue
            rows.append({
                "chat": chat,
                "type": chat.split("__")[0],
                "sender": d.get("sender") or "?",
                "time": d.get("time") or "",
                "text": t,
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day_dir")
    args = ap.parse_args()

    day_dir = Path(args.day_dir)
    rows = load_texts(day_dir)
    print(f"真实文本消息：{len(rows)} 条（已剔除图片/表情/链接）\n")

    # 按会话统计
    by_chat = collections.Counter(r["chat"] for r in rows)
    print("=== 真实发言量 TOP15 会话 ===")
    for k, v in by_chat.most_common(15):
        print(f"  {v:4d}  {k[:44]}")

    # 我自己的发言（昵称可配：WECHAT_SELF_NAMES，默认匹配常见写法）
    self_names = {n.strip() for n in
                  os.environ.get("WECHAT_SELF_NAMES", "我,自己,self").split(",")
                  if n.strip()}
    mine = [r for r in rows if str(r["sender"]).strip() in self_names]
    print(f"\n=== 我自己的发言：{len(mine)} 条"
          + (f"（识别名：{sorted(self_names)}）" if mine else ""))
    print("  提示：可用 WECHAT_SELF_NAMES='你的微信昵称' 精确识别")
    for r in mine[:20]:
        print(f"  {r['time'][:16]} | {r['chat'][:22]:22} | {r['text'][:70]}")

    # 命中行动类的信号
    print("\n=== 命中行动类关键词的消息 ===")
    hits = collections.defaultdict(list)
    for r in rows:
        for label, pats in ACTION_PATTERNS.items():
            if any(re.search(p, r["text"], re.I) for p in pats):
                hits[label].append(r)
                break
    for label, items in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        print(f"\n【{label}】{len(items)} 条")
        for r in items[:12]:
            print(f"  {r['time'][:16]} | {r['chat'][:24]:24} | {r['sender'][:10]:10} | {r['text'][:60]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())