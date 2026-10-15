---
name: wechat-hub
description: 把本机微信聊天记录转成可行动情报——日报、待回复、商机雷达、复联线索、商机管线。当用户提到「微信情报」「微信日报」「微信商机」「某人群聊到哪」「微信里搜关键词」「哪些人该复联」「昨天微信聊了什么」时使用。全程只读本地，绝不发送消息。
---

# 微信个人情报库

## 这是什么

把本机微信聊天记录变成可行动情报的本地链路：

```
微信数据库(SQLCipher) → wcdb-key 捕获密钥 → Reader 只读解密
   → 导出 jsonl → Hub 引擎抽信号 → 语义净化 → 日报
```

核心能力来自上游 `Rion-Wu-tech/wechat-intelligence-hub`（AGPL-3.0-only），
本 Skill 是适配层：**不改上游一行代码**，只重写入口并修Windows/macOS 兼容问题。

**首次部署或怀疑环境有问题，先跑自检**：

```bash
python scripts/wxconfig.py preflight    # 6 项依赖检查，FAIL 项带修复建议
python scripts/wxconfig.py              # 打印当前生效配置
```

完整依赖清单、平台支持矩阵、安装步骤见 **README.md**。

## 配置

所有路径自动发现，也可显式指定。优先级：**环境变量 > 配置文件 > 自动发现**。

配置文件位置：Windows `%LOCALAPPDATA%\wechat-hub\config.json` / macOS `~/.config/wechat-hub/config.json`

```json
{
  "hub_home": "/path/to/wechat-intelligence-hub",
  "python": "/path/to/python-with-sqlcipher3",
  "db_root": "/path/to/xwechat_files/<账号>/db_storage",
  "private_dir": "/path/to/.wechat-private",
  "data_dir": "/path/to/export"
}
```

## 调用方式（必须用本目录脚本，不要自己拼命令）

```bash
bash <SKILL_DIR>/scripts/wxhub.sh <command> [args]
```

示例：

```bash
bash <SKILL_DIR>/scripts/wxhub.sh home
bash <SKILL_DIR>/scripts/wxhub.sh scan <聊天导出txt> --out <输出目录>
bash <SKILL_DIR>/scripts/wxhub.sh brief --hours 24
bash <SKILL_DIR>/scripts/wxhub.sh today
bash <SKILL_DIR>/scripts/wxhub.sh person "联系人昵称"
bash <SKILL_DIR>/scripts/wxhub.sh topic "AI培训" --days 7
bash <SKILL_DIR>/scripts/wxhub.sh opportunities
```

聊天导出 txt 格式（上游 samples/sample_chat.txt 的真实格式）：

```
[2026-06-30 10:12] NovaAI: 你好，想咨询一下你 X thread 的合作报价
[2026-06-30 11:25] NovaAI: 预算 650 USD，想这周五发布
```

自己发消息用 `我:` 作为发言人。

## 当前状态（2026-10-15 实测：真实接入已打通）

| 层 | 状态 |
|---|---|
| 情报引擎（Hub） | ✅ 可用，`scan` 实测跑通 |
| 只读 Reader | ✅ `doctor` 返回 `readiness: ready`，零 warnings |
| 密钥捕获 | ✅ wcdb-key 0.1.0，20/20 库校验通过 |
| 导入链路 | ✅ `matched_database_count: 20`，`verification_level: sqlite_schema_read` |
| 真实导出 | ✅ FDE初探营2群 500 条 jsonl 已导出并被 Hub 解析出 409 条信号 |

本机微信数据目录：`~/Documents/xwechat_files/<账号>/db_storage`（约 369MB / 20 个加密库）
参考实测：微信 4.1.15.53 + 20 个库 → 1167 会话 / 50723 联系人 / 1084 张消息分表。

### 密钥材料

| 默认位置 | 内容 |
|---|---|
| `~/.wcdb-key/wechat-passphrase.keys.json` | wcdb-key 原始输出，`库名 → 64位hex` |
| `~/.wechat-private/material.json` | Reader schema-2 授权材料 |
| `~/.wechat-private/keys.json` | Reader 实际使用的密钥映射 |
| `%APPDATA%/rion-wechat-reader/config.json` | Reader 配置 |

这些文件的 ACL 会被自动收紧为「仅当前用户 + SYSTEM」。

**wcdb-key 的 keys.json 不带 salt**，salt 必须从数据库文件头前 16 字节读。
`wxkey_to_reader.py` 已加 `--database-root` 自动定位 + 读头逻辑。

### 一键验证（改了配置、升级微信、怀疑链路坏掉时先跑这个）

```bash
python <SKILL_DIR>/scripts/verify_wechat.py
```

分5 层客观检查，每层都不依赖上一次的结论：

| 层 | 检查内容 | 通过标准 |
|---|---|---|
| 1 | 密钥材料 | wcdb-key 输出条数 == Reader 材料条数，key 均 64 位 hex |
| 2 | 文件权限 | `secure_acl.py --check` 返回 `RESULT SAFE` |
| 3 | **逐库解密** | 所有库都能读出 `sqlite_master`（最硬的证据） |
| 4 | 真实数据读取 | 会话/联系人/消息分表 > 0，且能导出带正文和发送者时间戳的真实消息 |
| 5 | 情报引擎 | Hub 读出消息数 > 0、识别信号 > 0、产出 ≥3 个情报文件 |

`--layer N` 可单跑某一层。全绿时退出码 0。

### 标准链路：Reader 导出 → Hub 分析

Hub 的 `scan` 吃的是**聊天文本或 JSON 文件**，不是密钥。三步：

```bash
S=<SKILL_DIR>/scripts
P=<带 sqlcipher3 的 Python>

# 0. 一键导出某天的全部会话（推荐，省去逐个找ID）
"$P" $S/export_day.py 2026-10-14
#   → <data_dir>/2026-10-14/*.jsonl + manifest.json

# 1. 或者手动找单个会话
bash $S/rreader.sh sessions --limit 20

# 2. 导出为 jsonl（--view agent 输出适合喂给模型的精简字段）
bash $S/rreader.sh export-messages "<chatroom 或 wxid>" \
  --format jsonl --view agent --limit 500 --path <输出>/xxx.jsonl

# 3. 交给情报引擎
bash $S/wxhub.sh scan <输出>/*.jsonl --out <输出>/hub
```

Hub 产出 `signals.json` / `signals.csv` / `deals.csv` / `daily_digest.md`。

**必做第 4 步 —— 语义净化**：

```bash
"$P" $S/analyze_day.py <导出目录>
```

Hub 会把 `<img aeskey=...>` / `<sysmsg>` / `<emoji>` 这类 XML **误判为「待创作」**
（实测 450 条信号里 205 条是误判）。`signals.json` **绝不能直接用于决策**，
必须先跑 `analyze_day.py` 过滤到真实文本，再人工阅读生成日报。

**注意**：`export-messages` 会因输出目录 ACL 检查失败。`rion_compat.py` 已在 export 子命令下
把 `prepare_private_output` / `protect_new_file` 换成 no-op（只放宽输出目录，不动密钥校验）。
若报错，先确认走的是 `rreader.sh` 而非直接执行上游脚本。

### 筛选群聊的坑

`sessions` 会返回服务号（`brandservicesessionholder` 这类），它们没有可导出正文，
`export-messages` 直接报「找不到聊天对象」。筛群聊必须加：

```python
if s["username"].endswith("@chatroom")
```

### 三个上游 Windows bug（已在 rion_compat.py 修掉）

1. `mkdir(mode=0o700)` → 触发 `SeSecurityPrivilege` 要求 → 永久 `unsafe_key_permissions`
2. `with sqlite3.connect(...)` 不关连接 → Windows 文件句柄占用 → `WinError 32` 崩溃
3. `secure_write_text()` 对**导出目录**也强制私有 ACL → 项目目录内导出必然失败

补丁原理坑：改函数必须**原地改写 Reader 的 exec namespace**。换成新 dict 的话，
函数对象的 `__globals__` 仍指向旧 dict，patch 静默失效（这个坑我踩了半小时）。

另一个坑：**有 `@dataclass` 的模块不能用 exec 加载**。`dataclasses` 解析注解时需要
`sys.modules[cls.__module__]` 存在，裸 exec 未注册模块 →
`AttributeError: 'NoneType' object has no attribute '__dict__'`。必须用 `importlib.import_module`。

## 铁律

1. **只读**。永不发送、转发、删除、回复微信。回复只能是本地草稿。
2. **不重新取钥**。密钥材料已就位，直接用。确需重取（微信升级后）必须老大本人确认，
   且必须在微信 PC 版运行状态下执行 `wcdb-key capture`。
3. **原始聊天不出本机**。不发给外部搜索、不发给云端研究服务。
4. **区分置信度**。`已确认` / `高概率` / `待核实` 三档，不能混。
5. **不编造**。金额、日期、承诺、报价一律不许推断。
6. **情报库状态以 `opportunities` 为准**，日报和搜索只是证据层。

## 汇报格式（老大偏好：结论先行，不啰嗦）

拿到情报后按这个顺序，跳过空栏目：

1. **覆盖范围** —— 扫了哪段时间、哪些会话、索引新鲜度、有没有缺口
2. **立即处理** —— 最多 10 项，先列逾期的、有明确询价的、新回复的
3. **商单机会** —— 项目 / 联系人 / 角色 / 证据 / 下一步
4. **等待与复联** —— 谁在等谁、什么时候跟进；没到期的别冒充今日任务
5. **主要话题** —— 按真实事件聚类，不用「AI」「出海」这种宽泛标签糊弄

## 与 Obsidian 知识库联动

情报产出可以沉淀进知识库编译层：

- 商单/客户线索 → `02_Wiki/entities/` 建实体页
- 反复出现的行业信号 → `02_Wiki/concepts/` 补概念页
- 完整日报 → `03_Output/` 归档

登记时记得更新 `02_Wiki/index.md` 并追加 `02_Wiki/log.md`。