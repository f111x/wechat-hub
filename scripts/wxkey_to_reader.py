#!/usr/bin/env python3
"""
把 wcdb-key 输出的每库密钥映射文件，转换成 rion-wechat-reader 的 schema-2 授权材料。

背景：
  wcdb-key (webees/wcdb-key) 在 Windows 微信 4.1.12+ 下输出 `*.keys.json`，
  结构是 { 数据库路径: {"salt": <32位hex>, "key": <64或96位hex>, ...} }，
  其中 key 已经是「该库的 raw key」（已派生，无需再做 PBKDF2）。

  rion-wechat-reader 的 import-access 接受 schema-2 格式：
    { "schema_version": 2, "keys": { "<32位salt>": {"key": "<64位hex>"} } }
  Reader 会拿 salt 去 db_storage 里 rglob("*.db") 匹配文件头。

用法：
  python wxkey_to_reader.py --input <wcdb-key输出的keys.json> --output <授权材料路径>
  python wxkey_to_reader.py --probe <单个db文件>   # 只探测，不写材料
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Reader 只接受 64 或 96 位十六进制 raw key
VALID_KEY_LEN = {64, 96}
SALT_LEN = 32


def probe_salt(db_path: Path) -> str:
    """读取加密库文件头的 16 字节 salt（不读取任何聊天内容）。"""
    try:
        with db_path.open("rb") as fh:
            header = fh.read(16)
    except OSError:
        return ""
    if len(header) != 16 or header == b"SQLite format 3\x00":
        return ""
    return header.hex()


def normalize_entries(payload) -> dict:
    """从各种可能的 JSON 结构里提取 {路径或salt: 密钥信息}。"""
    if not isinstance(payload, dict):
        return {}
    for field in ("keys", "salt_keys", "databases", "key_map"):
        value = payload.get(field)
        if isinstance(value, dict):
            return value
    return payload


def pick_key(entry) -> str:
    """从一个条目里取出 raw key，兼容多种字段名。"""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for field in ("key", "raw_key", "enc_key", "hex", "master_key"):
            value = entry.get(field)
            if value:
                return str(value).strip()
    return ""


def locate_db(database_root: Path, name: str) -> Path | None:
    """按文件名在数据库根目录下递归定位真实加密库。"""
    base = Path(name).name
    direct = database_root / base
    if direct.is_file():
        return direct
    if not database_root.is_dir():
        return None
    for candidate in database_root.rglob(base):
        if candidate.is_file():
            return candidate
    return None


def build_material(entries: dict, database_root: Path | None, dry_run: bool) -> dict:
    """构建 schema-2 授权材料，并尽量用真实文件头校验 salt。"""
    material_keys: dict[str, dict] = {}
    stats = {"total": 0, "accepted": 0, "salt_mismatch": 0, "no_salt": 0, "key_len_bad": 0, "verified_against_file": 0}

    for name, entry in entries.items():
        stats["total"] += 1
        raw_key = pick_key(entry)
        declared_salt = ""
        if isinstance(entry, dict):
            declared_salt = str(entry.get("salt") or "").strip().lower()

        # 96 位 key 的后 32 位就是 salt
        key = raw_key.strip().strip("'\"").lower()
        if key.startswith("x'") and key.endswith("'"):
            key = key[2:-1]
        if not key:
            stats["no_salt"] += 1
            continue
        if len(key) not in VALID_KEY_LEN:
            stats["key_len_bad"] += 1
            print(f"  跳过 {name}: key 长度 {len(key)}，Reader 只接受 64/96 位", file=sys.stderr)
            continue

        if len(key) == 96:
            key_body, key_salt = key[:64], key[64:]
        else:
            key_body, key_salt = key, declared_salt

        if declared_salt and key_salt and declared_salt != key_salt:
            stats["salt_mismatch"] += 1
            print(f"  跳过 {name}: 文件头声明 salt 与 key 尾部不一致", file=sys.stderr)
            continue

        salt = key_salt or declared_salt

        # wcdb-key 的每库独立 key 模式只输出 64 位 key，不带 salt。
        # 此时按文件名到 database_root 里定位真实库，读文件头前 16 字节作为 salt。
        if (not salt or len(salt) != SALT_LEN) and database_root is not None:
            located = locate_db(database_root, name)
            if located is not None:
                header_salt = probe_salt(located)
                if header_salt and len(header_salt) == SALT_LEN:
                    salt = header_salt
                    stats["verified_against_file"] += 1

        if not salt or len(salt) != SALT_LEN:
            stats["no_salt"] += 1
            print(f"  跳过 {name}: 无法确定 32 位数据库 salt", file=sys.stderr)
            continue

        # 若给了 database_root，尝试用真实文件头交叉验证
        if database_root and database_root.is_dir():
            name_str = str(name).replace("\\", "/")
            candidate = Path(name_str)
            if candidate.is_absolute() and candidate.is_file():
                actual = probe_salt(candidate)
                if actual and actual == salt:
                    stats["verified_against_file"] += 1
                elif actual:
                    stats["salt_mismatch"] += 1
                    print(f"  跳过 {name}: 文件头 salt {actual[:8]}... 与材料 {salt[:8]}... 不一致", file=sys.stderr)
                    continue

        material_keys[salt] = {"key": key_body}
        stats["accepted"] += 1

    if dry_run:
        return {}

    return {
        "schema_version": 2,
        "keys": material_keys,
        "metadata": {
            "source": "wcdb-key per-database raw keys",
            "database_root": str(database_root) if database_root else "",
            "entry_count": stats["accepted"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="wcdb-key keys.json → rion-wechat-reader schema-2 材料")
    parser.add_argument("--input", help="wcdb-key 输出的 keys.json 路径（--probe 模式不需要）")
    parser.add_argument("--output", help="转换后的授权材料输出路径")
    parser.add_argument("--database-root", help="数据库目录（用于交叉校验 salt）")
    parser.add_argument("--probe", help="只探测单个数据库文件的 salt，不读取内容")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args()

    if args.probe:
        p = Path(args.probe)
        salt = probe_salt(p)
        if salt:
            print(f"加密库 salt: {salt}")
            print("（这是公开的文件头字节，不是密钥，可安全显示）")
        else:
            print("不是 SQLCipher 加密库（文件头为 SQLite 明文格式）")
        return 0

    if not args.input:
        parser.error("需要 --input（--probe 模式除外）")

    source = Path(args.input).expanduser()
    if not source.is_file():
        print(f"找不到输入文件: {source}", file=sys.stderr)
        return 2

    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    entries = normalize_entries(payload)
    if not entries:
        print("输入文件里没找到任何密钥条目", file=sys.stderr)
        return 3

    db_root = Path(args.database_root).expanduser() if args.database_root else None
    material = build_material(entries, db_root, args.dry_run)

    print(f"读到条目: {len(entries)}")
    if args.dry_run:
        print("（dry-run，未写文件）")
        return 0

    assert material, "没有可用条目"
    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(material, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"已写出授权材料: {out}")
    print(f"可导入数据库 key 数: {len(material['keys'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())