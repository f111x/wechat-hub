# wechat-hub · 本地微信聊天记录 → 情报日报

把本机微信聊天记录转成可行动情报。**全程只读、纯本地，不发送任何消息。**

```
微信数据库(SQLCipher) → wcdb-key 捕获密钥 → Reader 只读解密
   → 导出 jsonl → Hub 引擎抽信号 → 语义净化 → 日报
```

---

## 依赖条件

### 硬性必需

| # | 依赖 | 版本要求 | 说明 | 检查方式 |
|---|---|---|---|---|
| 1 | **微信 PC 版** | 4.1.x（已在 4.1.15.53 验证） | **必须已登录过**，聊天记录才落盘到本地 | 任务管理器可见 `Weixin.exe` |
| 2 | **Rust 工具链** | 1.82+ | 编译 `wcdb-key`。macOS 需 Xcode CLT | `rustc --version` |
| 3 | **Python** | 3.11+ | 3.13 验证通过 | `python --version` |
| 4 | **sqlcipher3** | 0.6.x | SQLCipher 解密，**编译不过最常见的坑** | `python -c "import sqlcipher3"` |
| 5 | **zstandard** | 0.25+ | 消息内容压缩解码 | `python -c "import zstandard"` |
| 6 | **上游仓库** | — | `Rion-Wu-tech/wechat-intelligence-hub`（AGPL-3.0-only） | `wxconfig.py preflight` |
| 7 | **PowerShell** | 5.1+ / 7 | **仅 Windows 需要**，ACL 权限检查 | `wxconfig.py preflight` |

### 平台支持

| 平台 | Reader 解密 | Hub 引擎 | 备注 |
|---|---|---|---|
| **Windows** | ✅ 已验证（4.1.15.53） | ✅ | 需 PowerShell 做 ACL 检查 |
| macOS | ⚠️ 上游支持但本 Skill 未验证 | ✅ | 需 Xcode CLT，`wcdb-key` 另有 `--wechat-path` |
| Linux | ❌ | ✅ 仅 Hub | Wine 环境路径不同，未验证 |

**微信版本敏感**：`wcdb-key` 官方验证 4.1.13.12，实测 4.1.15.53 也能 20/20 全通过。
但**微信大版本升级后建议重新验证**（跑 `verify_wechat.py --layer 3`）。

### 平台支持边界（手机端）—— **不支持**

本 Skill 的设计前提是**桌面端微信 PC 版**（Windows/macOS），
密钥捕获依赖 `wcdb-key` 扫描 **`Weixin.exe` 进程内存**（Windows 调 `ReadProcessMemory`）。
**手机端没有对等进程，这一步无法复用，后续链路（Reader 解密 / Hub 分析）无从启动。**

| 平台 | 状态 | 障碍 | 能否复用本 Skill |
|---|---|---|---|
| **Android** | ❌ 不支持 | 数据库在 `/data/data/com.tencent.mm/MicroMsg/<32位hex>/EnMicroMsg.db`，**必须 root**；密钥 = MD5(IMEI + UIN) 前 7 位 | 否（旧版架构，与 4.x 每库独立 key 不通用） |
| **iOS** | ❌ 不支持 | 数据库在沙盒，需**未加密的 iTunes/Finder 备份**提取 `MM.sqlite` / `WCDB_Contact.sqlite` | 否（旧版未加密格式） |
| **官方导出** | ⚠️ 仅 zip | 长按「导出聊天记录」只给 zip，**不输出原始数据库** | 否（无数据库可喂给 Reader） |

**现有开源工具（wechat-dump / AndroidWechatSQLiteDecrypt / decrypt-wechat-db）全部针对旧版 Android EnMicroMsg.db，
与微信 4.x 桌面端 `db_storage` / 每库独立 key 架构不通用，不可直接套用。**

#### 唯一可靠的推荐路径：手机 → PC 迁移，再复用本 Skill

```
手机微信 ──「聊天记录迁移与备份」──> 已接入的 PC 微信
   └─> 现有链路：wcdb-key 抓 key → Reader 解密 → Hub 日报
```

用法与你现在完全一致：`verify_wechat.py` → `export_day.py <date>` → `analyze_day.py`。

**硬限制**：迁移只能把手机已有历史推送到 PC，**前提是 PC 端此前已拥有这段历史**
（同一账号在同一台 PC 登录过、且未被清理）。手机独有的早年历史
（换设备、清空过 PC 数据）**无法反向补齐**。

#### 若坚持做手机端直取（需独立开发，不在本 Skill 范围）

- **Android**：root 环境 + 定位 `EnMicroMsg.db` + IMEI/UIN 派生解密 + 适配微信 4.x 新加密格式（社区无成熟公开方案）
- **iOS**：越狱 / 未加密备份提取

工作量与维护成本远高于桌面端，且随时被微信更新打断，**不能直接复用 `wcdb-key` / `rion-wechat-reader`**。

### 资源占用

- 磁盘：仓库约 50MB + Python 依赖约 80MB + 导出的聊天记录（一天约 13MB）
- 内存：密钥捕获时扫 5 个微信进程，峰值约 200MB
- 耗时：编译 15s / 密钥捕获 8s / 全天导出 80s

### 隐私前提（不可绕过）

- 只允许用于**自己的设备、自己的账号**
- 原始聊天**不出本机**：不发给外部搜索、不发云端服务
- 密钥文件 ACL 会自动收紧为「仅当前用户 + SYSTEM」，**不要手动放开**

---

## 安装

```bash
# 1. 克隆本 Skill
git clone <this-repo> ~/.workbuddy/skills/wechat-hub
cd ~/.workbuddy/skills/wechat-hub

# 2. 克隆上游仓库
git clone https://github.com/Rion-Wu-tech/wechat-intelligence-hub \
  ~/wechat-intelligence-hub

# 3. 装 Python 依赖（sqlcipher3 需要编译工具链）
pip install sqlcipher3 zstandard

# 4. 装 Rust（仅 wcdb-key 需要）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

### 配置

优先级：**环境变量 > 配置文件 > 自动发现**

```bash
# 方式 A：环境变量（临时）
export WECHAT_HUB_HOME=~/wechat-intelligence-hub
export WECHAT_PYTHON=$(which python)

# 方式 B：配置文件（推荐，一次配好）
#   Windows: %LOCALAPPDATA%\wechat-hub\config.json
#   macOS/Linux: ~/.config/wechat-hub/config.json
cat > ~/.config/wechat-hub/config.json <<'EOF'
{
  "hub_home": "/path/to/wechat-intelligence-hub",
  "python": "/path/to/python-with-sqlcipher3",
  "db_root": "/path/to/xwechat_files/<账号>/db_storage",
  "private_dir": "/path/to/.wechat-private",
  "data_dir": "/path/to/export/output"
}
EOF
```

### 全部环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `WECHAT_HUB_HOME` | 上游仓库路径 | 自动发现 |
| `WECHAT_PYTHON` | 带 sqlcipher3 的解释器 | 自动探测 `python`/`python3` |
| `WECHAT_HUB_PYTHON` | Hub 专用解释器（纯标准库要求更低） | 同上 |
| `WECHAT_DB_ROOT` | 微信 `db_storage` 目录 | 自动发现 `~/Documents/xwechat_files/*/db_storage` |
| `WECHAT_PRIVATE_DIR` | 密钥材料目录 | `~/.wechat-private` |
| `WECHAT_KEY_DIR` | wcdb-key 原始输出目录 | `~/.wcdb-key` |
| `WECHAT_HUB_DATA_DIR` | 导出数据根目录 | `./wechat-export` |
| `WECHAT_HUB_CONFIG_DIR` | 本工具配置目录 | 见「配置文件」 |
| `WECHAT_SELF_NAMES` | 识别「我自己的发言」的昵称，逗号分隔 | `我,自己,self` |

### 自检

```bash
python scripts/wxconfig.py            # 打印当前生效配置
python scripts/wxconfig.py preflight  # 检查全部依赖，FAIL 项带修复提示
```

---

## 首次接入（拿到密钥）

**这一步只能在自己机器上手动做一次。**

```bash
# 1. 保持微信 PC 版运行，然后捕获密钥
cd ~/wechat-intelligence-hub-wcdb   # wcdb-key 的仓库
cargo build --release
./target/release/wcdb-key capture --json
# 多账号时会等stdin 选择，按提示输入序号

# 2. 转成 Reader 格式（salt 从数据库文件头读）
python scripts/wxkey_to_reader.py \
  --input ~/.wcdb-key/wechat-passphrase.keys.json \
  --database-root <db_storage> \
  --output ~/.wechat-private/material.json

# 3. 收紧 ACL
python scripts/secure_acl.py ~/.wechat-private

# 4. 导入验证
bash scripts/rreader.sh import-access \
  --source ~/.wechat-private/material.json \
  --database-root <db_storage> \
  --keys-file ~/.wechat-private/keys.json --verify
# 期望 matched_database_count == 20

# 5. 初始化配置
bash scripts/rreader.sh setup \
  --keys-file ~/.wechat-private/keys.json \
  --database-root <db_storage>
```

> 微信大版本升级后密钥可能失效，重复第1~4 步。
> 若 `.wechat-private` 里出现 `CodexSandboxUsers` 之类的多余 ACL 项，
> 会导致 Reader 判 UNSAFE，用 `icacls <dir> /remove:g <账户> /T` 摘掉。

---

## 日常使用

```bash
S=~/.workbuddy/skills/wechat-hub/scripts
P=<你的 python>

# 一键验证链路（15 项检查）
"$P" $S/verify_wechat.py

# 导出某天全部会话
"$P" $S/export_day.py 2026-10-14

# 语义净化 + 信号分类（重要，见下方「已知缺陷」）
"$P" $S/analyze_day.py <导出目录>

# 交给情报引擎
bash $S/wxhub.sh scan <导出目录>/*.jsonl --out <输出目录>

# 单个会话
bash $S/rreader.sh sessions --limit 20
bash $S/rreader.sh export-messages "<chatroom 或 wxid>" \
  --format jsonl --view agent --limit 500 --path /tmp/chat.jsonl
```

---

## 已知缺陷（必读）

### 1. Hub 引擎会把图片/表情误判为「待创作」

**实测：450 条信号里205 条是 `<img aeskey=...>` / `<sysmsg>` / `<emoji>` 的 XML。**

`signals.json` **绝不能直接用于决策**。必须先跑 `analyze_day.py` 过滤到真实文本
（490 条 → 234 条）再人工阅读。

### 2. Windows 上游三个 bug 已在 `rion_compat.py` 修掉

| bug | 症状 |
|---|---|
| `mkdir(mode=0o700)` | 要 `SeSecurityPrivilege`，永久 `unsafe_key_permissions` |
| `with sqlite3.connect()` 不关连接 | `WinError 32` |
| `secure_write_text()` 对**导出目录**也强制私有 ACL | 往项目目录内导出必失败 |

### 3. exec 加载的模块是「假模块」

- patch 函数必须**原地改写原 namespace**，函数对象的 `__globals__` 指向旧 dict
- 含 `@dataclass` 的模块不能用 exec，须用 `importlib.import_module`

### 4. `sessions` 会返回服务号

筛群聊必须加 `username.endswith("@chatroom")`，否则 `export-messages` 报「找不到聊天对象」。

### 5. GitHub clone 可能失败

网络不稳时改用：
```bash
curl -sL -o repo.zip https://codeload.github.com/<owner>/<repo>/zip/refs/heads/main
unzip repo.zip
```

---

## 安全边界

1. **只读**。永不发送、转发、删除、回复微信。回复只能是本地草稿。
2. **不代取钥**。密钥捕获必须用户本人手动执行。
3. **原始聊天不出本机**。
4. **不编造**。金额、日期、承诺、报价一律不许推断。
5. **区分置信度**：`已确认` / `高概率` / `待核实` 三档，不得混用。

## 发布与安装（给互联网用户）

本仓库自带 `install.sh`，自动拉取上游仓库、安装 Rust、编译 `wcdb-key`、装 Python 依赖并跑自检。

### 安装命令

**Windows 原生（PowerShell，推荐，无需 Git Bash）**

```powershell
irm https://raw.githubusercontent.com/<YOUR_GITHUB>/wechat-hub/main/install.ps1 | iex
```

若系统禁止脚本执行，下载后带仓库参数运行：

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/<YOUR_GITHUB>/wechat-hub/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1 https://github.com/<YOUR_GITHUB>/wechat-hub.git
```

> 自动安装 Rust 时会联网下载 `rustup-init.exe`；wcdb-key 编译依赖 **Visual Studio 生成工具（MSVC）**，未安装会编译失败（装 Python 依赖的 sqlcipher3 也需要 C 编译工具链）。

**方式一：一行命令（脚本自举，macOS / Linux / Windows-Git-Bash）**

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/<YOUR_GITHUB>/wechat-hub/main/install.sh)" _ https://github.com/<YOUR_GITHUB>/wechat-hub.git
```

> 命令末尾的仓库 URL 是传给脚本的参数，必须和前面的 raw 链接用同一个 `<YOUR_GITHUB>`。
> **发布前必做**：把 `<YOUR_GITHUB>` 换成你自己的 GitHub 用户名/仓库，且本仓库已 push 到 GitHub（否则 raw 链接和 clone 链接无效）。
> macOS / Linux 直接跑；Windows 可用 **Git Bash**（安装 Git for Windows 自带）或 WSL。

**方式二：手动 clone 后运行**

```bash
git clone https://github.com/<YOUR_GITHUB>/wechat-hub ~/.workbuddy/skills/wechat-hub
cd ~/.workbuddy/skills/wechat-hub
bash install.sh
```

### `install.sh` 会自动完成

| 步骤 | 动作 |
|---|---|
| 1 | 取得本 Skill（识别当前目录或 clone 到 `~/.workbuddy/skills/wechat-hub`） |
| 2 | 克隆上游 `wechat-intelligence-hub` |
| 3 | 检测 / 安装 Rust |
| 4 | 克隆并编译 `wcdb-key` |
| 5 | `pip install sqlcipher3 zstandard` |
| 6 | 运行 `wxconfig.py preflight` 自检 |

### 首次接入仍需手动（安全边界，工具不代取密钥）

安装器**不会**碰你的微信密钥。装完后按「首次接入」章节手动抓一次密钥即可，之后每天只需 3 条命令。

### 也可下载 zip（不用 git）

```bash
curl -sL -o wechat-hub.zip https://codeload.github.com/<YOUR_GITHUB>/wechat-hub/zip/refs/heads/main
unzip wechat-hub.zip
cd wechat-hub-main && bash install.sh
```

---

## 许可证

本 Skill 为适配层。核心能力来自上游 `Rion-Wu-tech/wechat-intelligence-hub`，
**AGPL-3.0-only** —— 二次分发需遵守 AGPL 条款。
`wcdb-key` 为 MIT。