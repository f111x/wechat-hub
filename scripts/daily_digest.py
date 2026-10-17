"""微信情报日报生成器 —— 把一天的聊天记录变成一份可行动的结构化日报。

设计要点（三条，都是踩过坑换来的）：

1. **不信 Hub 的原始信号。** Hub 会把 `<img aeskey=...>` / `<sysmsg>` / `<emoji>`
   误判为「待创作」（实测 450 条信号里 205 条是误判，占 45.6%）。
   本脚本自己从 jsonl 重读原文，不消费 signals.json。

2. **日报必须机器可判，不能靠人读。** 定时推送没人盯着，所以行动项靠
   确定性规则抽取 + 置信度分档，不依赖模型主观判断。

3. **只读，不发送。** 全程只读本地文件与数据库，不碰微信客户端。

用法：
    python daily_digest.py 2026-10-17 --out <日报输出目录>
    python daily_digest.py yesterday
    python daily_digest.py 2026-10-17 --json      # 输出机器可读结构，供推送用
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── 噪声过滤：与 Hub 的判断无关，自己判 ──────────────────────────────────
NOISE = re.compile(
    r"(aeskey=|cdnurl|<img|<emoji|cdn\.|wx_fmt|<\?xml|<msg>|appmsg|thumburl|"
    r"^\[图片\]|^\[表情\]|^\[动画表情\]|^\[语音\]|^\[视频\]|^\[文件\]|"
    r"撤回了一条消息|^\[链接\]|^https?://)",
    re.I,
)

# ── 行动信号：比Hub 严格，避免「同业讨论」被算成商机 ────────────────────
SIGNALS: dict[str, re.Pattern] = {
    "待跟进": re.compile(
        r"(帮我|能否|可以|麻烦|麻烦你).{0,14}(看下|看一下|看下|发我|给我|提供|确认|报价|评估)"),
    "报价预算": re.compile(
        r"(报价|预算|多少钱|价格|费用|报价单|万元|元/|USD|\$\s?\d)"),
    "时间承诺": re.compile(
        r"(截止|deadline|最晚|之前给|需要.{0,8}前|周[一二三四五六日]前|今天内|明天前|尽快)"),
    "合作意向": re.compile(
        r"(合作|一起做|联名|联合|找个人|有人要|求带|带我|收徒|接单|有偿|外包)"),
    "交付结算": re.compile(
        r"(已发布|已交付|结款|结算|发票|打款|尾款|首款|已付款|到账|已结)"),
    "邀约联系": re.compile(
        r"(约个|约一|见面|电话|加个微信|私聊|回头联系|保持联系|节日快乐)"),
}

# ── 置信度分档（STRONG 定义见下方 PROMO 段之后）────────────────────────
#  强信号：明确动作 + 具体金额/时间/交付状态

# 商业类会话（用于商单机会单独归类）
BIZ_HINT = re.compile(r"(客户|商单|接单|需求|报价|合作|项目|甲方|乙方|外包|咨询)", re.I)

# ── ★ 推广/广告文案识别（实测踩坑：一天 580 条文本里 4 成是这类）────────
# 特征：① 自定义/报名/扫码类祈使 ② 社群定价/名额话术 ③ 免责声明
#      ④ 分销/代理话术（「跟着 XX 入的都很不错」「分红到账」「躺赚」）
#      ⑤ 账号售卖/会员充值类价目表
PROMO = re.compile(
    r"(加我?微信|扫码|报名|名额|席位|早鸟|限时|立减|优惠券|推广位|"
    r"社群.{0,8}(价|费|报名|加入)|课程.{0,6}(价|报名)|线下课.{0,6}(费|报名)|"
    r"【昵称】|自定义，坐标|加v[:：]|微信号|vx[:：])", re.I)
PROMO_TAIL = re.compile(
    r"(不构成投资建议|风险自担|免责声明|最终解释权|仅供参考，不作为)", re.I)
# 分销/收割话术：这类最像「商单」，实际全是拉人头（实测踩过）
PROMO_BIZ = re.compile(
    r"(股东营|报喜|入股东|跟着.{0,6}入的|躺赚|被动收益|gu票|割.{0,2}菜|"
    r"分红到账|会员月卡|充值服务|官方渠道.{0,10}质保|账号直充|"
    "倍增计划|变现营|拉新|代理加盟|招代理|分销|合伙人招募|"
    r"高防\s?IP|云防护|超低折扣|渠道合作接|特招|企业级接入|"
    r"价格优惠|代充|直充|批发价)", re.I)

# ── 系统通知 / 机器人消息（实测踩坑）─────────────────────────────────────
#   打卡机器人日报命中了「截止」类关键词，被判成「已确认商机」——
#   系统通知不是商机，且每天固定发，日报里出现就是纯噪音。
SYSBOT = re.compile(
    r"(#行动日报|已完成打卡|连续签到|满\d+天|打卡打卡|打卡提醒|"
    r"签到成功|系统提示|机器人|积分已|排行榜单)", re.I)

# 强信号：必须「有明确动作对象 + 具体金额/时间/交付状态」
#注意：不能只用数字或钱字，推广文案里全是价格，会全被误判（实测踩过）。
STRONG = re.compile(
    r"(报价单|预算\d|截止|deadline|最晚.{0,6}前|付款|打款|尾款|首款|已付款|到账|"
    r"已交付|已结|发票寄|合同签|需求文档发你|麻烦你确认)")



def load_rows(day_dir: Path) -> list[dict]:
    """读全部 jsonl，返回剔除噪声后的真实文本消息。"""
    rows: list[dict] = []
    for f in sorted(day_dir.glob("*.jsonl")):
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
            stem = f.stem
            rows.append({
                "chat": stem,
                "type": stem.split("__")[0] if "__" in stem else "unknown",
                "name": stem.split("__", 1)[1] if "__" in stem else stem,
                "sender": str(d.get("sender") or "?"),
                "time": str(d.get("time") or ""),
                "text": t,
            })
    return rows


def self_names() -> set[str]:
    """我自己的微信昵称集合。

    ★ 默认值已含老大实际昵称「哪儿都火🔥」——
    漏配的后果是日报里出现「你说过『床垫留你的电话』」这种把自己当待办的废话。
    换昵称时改这里或设环境变量 WECHAT_SELF_NAMES（逗号分隔）。
    """
    return {n.strip() for n in
            os.environ.get("WECHAT_SELF_NAMES", "我,自己,self,哪儿都火").split(",")
            if n.strip()}


def content_key(text: str) -> str:
    """内容指纹：用于跨会话去重。

    ★ 实测踩坑：同一篇公众号推文会被作者转发到十几个群，
    若按「会话 + 标签」去重，同一条内容会在日报里出现十几次。
    改成按正文前 60 字指纹去重，保留首次出现的会话，其余只记转发数。
    """
    s = re.sub(r"\s+", "", text)
    return s[:60]


def is_promo(text: str) -> bool:
    """推广/广告文案识别。

    ★ 实测踩坑：一天 580 条真实文本里大半是社群推广，
    但因为文案里全是价格，旧版把「这次我们把价格定得很低」判成了「已确认商机」——
    强信号正则一旦只匹配钱字，整个日报就废了。
    """
    if PROMO.search(text):
        return True
    if PROMO_TAIL.search(text):
        return True
    if PROMO_BIZ.search(text):
        return True
    return False


def build_digest(day: str, day_dir: Path, rows: list[dict]) -> dict:
    """把原始消息压成结构化日报。返回 dict，可渲染成 Markdown。"""
    mine = self_names()
    total_raw = sum(
        1 for f in day_dir.glob("*.jsonl")
        for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()
    )

    # 三层过滤：推广文案 → 系统通知 → 跨会话转发去重
    # （都要在统计之前做，否则日报里的数字全是虚的）
    promo_rows = [r for r in rows if is_promo(r["text"])]
    rows = [r for r in rows if not is_promo(r["text"])]
    bot_rows = [r for r in rows if SYSBOT.search(r["text"])]
    rows = [r for r in rows if not SYSBOT.search(r["text"])]

    first_seen: dict[str, dict] = {}
    forwarded = 0
    for r in rows:
        k = content_key(r["text"])
        if k in first_seen:
            forwarded += 1
            first_seen[k]["forwarded"] = first_seen[k].get("forwarded", 0) + 1
            first_seen[k]["chats"] = first_seen[k].get("chats", [r["name"]])
            continue
        first_seen[k] = dict(r, forwarded=0, chats=[r["name"]])
    rows = list(first_seen.values())

    by_chat = collections.Counter(r["name"] for r in rows)

    # 行动项：按信号类型聚合，同一会话同类只留一条最相关的
    actions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in sorted(rows, key=lambda x: x["time"]):
        # ★ 自己发的话不是「待办」——否则日报里会出现「你说过床垫留你电话」这种废话。
        #   只采信别人说的话。
        #   注意前缀匹配：实际 sender 常带 emoji 后缀（「哪儿都火🔥」），精确等值会漏。
        s = str(r["sender"]).strip()
        if any(s == n or s.startswith(n) for n in mine):
            continue
        for label, pat in SIGNALS.items():
            if pat.search(r["text"]):
                key = (label, r["name"])
                if key in seen:
                    # 同类多条：保留信息量更大的（文本更长的那通常含金额/日期）
                    for a in actions:
                        if (a["label"], a["name"]) == key and len(r["text"]) > len(a["text"]):
                            a.update(text=r["text"], time=r["time"], sender=r["sender"])
                    break
                seen.add(key)
                actions.append({
                    "label": label,
                    "name": r["name"],
                    "type": r["type"],
                    "sender": r["sender"],
                    "time": r["time"],
                    "text": r["text"],
                    "forwarded": r.get("forwarded", 0),
                    "confidence": "已确认" if STRONG.search(r["text"]) else "高概率",
                })
                break

    # 分档：已确认优先，同档内时间倒序（时间串可直接反向字典序比较）
    rank = {"已确认": 0, "高概率": 1}
    actions.sort(key=lambda a: a["time"], reverse=True)
    actions.sort(key=lambda a: rank[a["confidence"]])

    biz = [a for a in actions if BIZ_HINT.search(a["text"]) or BIZ_HINT.search(a["name"])]
    general = [a for a in actions if a not in biz]
    follow = [a for a in actions if a["label"] in ("待跟进", "邀约联系", "时间承诺")]

    return {
        "date": day,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "coverage": {
            "sessions": len(by_chat),
            "raw_messages": total_raw,
            "media_noise": total_raw - (len(rows) + len(promo_rows) + len(bot_rows) + forwarded),
            "text_messages": len(rows) + len(promo_rows) + len(bot_rows) + forwarded,
            "promo_filtered": len(promo_rows),
            "bot_filtered": len(bot_rows),
            "forward_deduped": forwarded,
            "actionable": len(rows),
            "noise_rate": round(
                (total_raw - (len(rows) + len(promo_rows) + len(bot_rows) + forwarded))
                / total_raw * 100, 1
            ) if total_raw else 0.0,
            "promo_rate": round(
                len(promo_rows) / (len(rows) + len(promo_rows) + len(bot_rows) + forwarded) * 100, 1
            ) if (len(rows) + len(promo_rows) + len(bot_rows) + forwarded) else 0.0,
            "top_sessions": by_chat.most_common(8),
            "my_messages": sum(
                1 for r in rows
                if any(str(r["sender"]).strip() == n
                       or str(r["sender"]).strip().startswith(n) for n in mine)),
        },
        "immediate": actions[:10],
        "opportunities": biz[:8],
        "followups": follow[:8],
        "general": general[:8],
        "self_names": sorted(mine),
    }


def render_markdown(d: dict) -> str:
    c = d["coverage"]
    L: list[str] = []
    L.append(f"# 微信情报日报 · {d['date']}")
    L.append(f"> 生成时间 {d['generated_at']}｜ 数据来源：本机微信（全程只读）")
    L.append("")
    L.append("## 一、覆盖范围")
    L.append(f"- 活跃会话 **{c['sessions']}** 个｜原始消息 **{c['raw_messages']}** 条")
    L.append(f"- ① 剔除图片/表情/链接 **{c['media_noise']}** 条 → 文本 **{c['text_messages']}** 条")
    L.append(f"- ② 剔除推广/广告文案 **{c['promo_filtered']}** 条"
             f"（占文本 {c['promo_rate']}%）")
    L.append(f"- ③ 剔除系统通知/机器人 **{c['bot_filtered']}** 条")
    L.append(f"- ④ 跨会话转发去重 **{c['forward_deduped']}** 条 → "
             f"**可行动文本 {c['actionable']}** 条")
    L.append(f"- 我的发言 **{c['my_messages']}** 条（识别名：{'、'.join(d['self_names'])}）")
    if c["top_sessions"]:
        L.append("- 发言 TOP：" + "、".join(f"{n}({v})" for n, v in c["top_sessions"]))
    L.append("")
    L.append("## 二、立即处理（最多 10 项）")
    if d["immediate"]:
        L.append("| 置信度 | 会话 | 发言人 | 时间 | 内容 |")
        L.append("|---|---|---|---|---|")
        for a in d["immediate"]:
            t = a["text"].replace("|", "／").replace("\n", " ")[:70]
            fwd = f"（另转发 {a['forwarded']} 处）" if a.get("forwarded") else ""
            L.append(f"| {a['confidence']} | {a['name'][:16]} | {a['sender'][:10]} | "
                     f"{a['time'][5:16]} | {t}{fwd} |")
    else:
        L.append("_当日无可行动的真实对话（推广文案与转发已剔除）。_")
    L.append("")
    L.append("## 三、商单机会")
    if d["opportunities"]:
        for a in d["opportunities"]:
            L.append(f"- **[{a['confidence']}] {a['name'][:24]}** — {a['text'][:80]}")
    else:
        L.append("_当日未识别到明确商机线索（同业讨论不计）。_")
    L.append("")
    L.append("## 四、等待与复联")
    if d["followups"]:
        for a in d["followups"]:
            L.append(f"- {a['name'][:20]} / {a['sender'][:10]}：{a['text'][:70]}")
    else:
        L.append("_当日无待复联事项。_")
    L.append("")
    L.append("---")
    L.append("*说明：置信度分「已确认 / 高概率」两档，仅靠确定性规则抽取，未经模型推断。*")
    L.append("*金额、日期、承诺一律取自原文，不做推断。原始聊天不出本机。*")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="YYYY-MM-DD 或 yesterday")
    ap.add_argument("--out", default=None, help="日报输出目录，默认 <data_dir>/digest/<日期>")
    ap.add_argument("--json", action="store_true", help="额外输出 digest.json")
    ap.add_argument("--print", dest="do_print", action="store_true", help="打印 Markdown")
    args = ap.parse_args()

    import wxconfig

    day = (dt.date.today() - dt.timedelta(days=1)).isoformat() \
        if args.date in ("yesterday", "昨天") else args.date
    day_dir = Path(args.out or (wxconfig.default_data_dir() / day)) if args.out \
        else Path(wxconfig.default_data_dir()) / day

    if not day_dir.exists():
        print(f"找不到导出目录：{day_dir}")
        print("先跑 export_day.py 导出该日数据。")
        return 2

    rows = load_rows(day_dir)
    digest = build_digest(day, day_dir, rows)
    md = render_markdown(digest)

    out_dir = Path(wxconfig.default_data_dir()) / "digest"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{day}.md"
    js_path = out_dir / f"{day}.json"
    md_path.write_text(md, encoding="utf-8")
    js_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.do_print:
        print(md)
    print(f"\n日报已生成：{md_path}")
    print(f"结构化输出：{js_path}")
    c = digest["coverage"]
    print(f"覆盖 {c['sessions']} 会话 / 原始 {c['raw_messages']} 条 → "
          f"可行动 {c['actionable']} 条（媒体噪声 {c['media_noise']}、"
          f"推广 {c['promo_filtered']}、机器人 {c['bot_filtered']}、"
          f"转发去重 {c['forward_deduped']}）/ "
          f"行动项 {len(digest['immediate'])} 条（已确认 "
          f"{sum(1 for a in digest['immediate'] if a['confidence'] == '已确认')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
