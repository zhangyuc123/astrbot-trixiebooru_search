# TrixieBooru 图片搜索插件

> 基于 AstrBot 的图片搜索插件，从 [TrixieBooru](https://trixiebooru.org/) 网站按标签搜索并返回图片。支持多关键词、自然语言分词、热度/时间筛选、安全模式，翻译服务支持本地映射表、自定义 API、LLM 和拼音降级。

---

## 📦 插件基本信息

- **插件名称**：`trixiebooru_search`
- **描述**：根据用户输入的关键词（以英文逗号开头）从 TrixieBooru 网站搜索并返回匹配的图片，支持多标签查询、热度排序、时间过滤和安全模式控制；翻译服务支持本地映射表、自定义 API、LLM 及拼音降级。
- **版本**：1.0.0
- **作者**：octony
- **平台适配**：aiocqhttp（AstrBot）

---

## ✨ 功能特性

### 1. 触发方式
- 仅当消息**以英文逗号 `,` 为首字符**时触发（忽略前导空格），避免误触发。
- 示例：`,小蝶,夕阳` 或 `,来张小蝶这周最火的图`

### 2. 三种查询模式

| 模式 | 输入示例 | 说明 |
|------|----------|------|
| **模式一：显式分隔** | `,小蝶,夕阳,花坛` | 用逗号分隔多个关键词，全部翻译后以 **AND** 关系查询，随机返回一张图片。 |
| **模式二：自然语言（随机）** | `,来张小蝶美图` | 使用 jieba 分词自动提取关键词（名词/动词/形容词），过滤语气词和停用词，随机返回一张图片。 |
| **模式三：自然语言（热度/时间）** | `,来张小蝶这周最火的图` | 识别“本周”“最近”等时间词和“最火”“热度最高”等排序词，返回筛选时间段内热度最高的一张图片。 |

### 3. 翻译服务（优先级从高到低）
- **本地映射表**：内置常见角色名映射（如 `"小蝶" → "Fluttershy"`），可 Web 配置扩展，速度最快。
- **自定义翻译 API**：可配置任意翻译 API（如百度翻译、腾讯翻译等），支持自定义请求参数和响应提取路径。
- **LLM 翻译**：若未配置自定义 API，自动使用 AstrBot 当前配置的大语言模型进行翻译。
- **拼音降级**：若所有翻译方式均失败，使用 `pypinyin` 转为拼音，确保可用性。

### 4. 安全模式（严格分级）
- **内容分级**：
  - `safe`：完全安全
  - `questionable`：轻微暗示
  - `explicit`：明显色情
- **全局禁止**：任何情况下，**绝不返回 `explicit` 级别的图片**。
- **默认安全模式**：所有群聊和用户默认处于安全模式，仅返回 `safe` 图片。
- **白名单控制**：
  - 可分别配置群聊 ID 和用户 ID 白名单，关闭安全模式（允许返回 `questionable`）。
  - **优先级**：群聊白名单 **覆盖** 用户白名单。若群聊关闭安全模式，群内所有用户均不受限；若群聊未关闭，即使某用户个人白名单，在群内也受限制。
- **关键词黑名单**：
  - `explicit` 黑名单（如 `sex`、`porn`、`explicit`）全局生效，命中即拒绝。
  - `questionable` 黑名单（如 `underwear`、`swimsuit`）仅在安全模式下拒绝，关闭后允许搜索。

---

## 🚀 安装与配置

### 依赖
- Python 3.8+
- AstrBot 框架（支持 aiocqhttp）
- 依赖库：`jieba`, `pypinyin`, `requests`, `cloudscraper`（或 `flaresolverr` 客户端）

### 安装步骤
1. 将插件文件夹放置于 AstrBot 的 `plugins` 目录下。
2. 在 `requirements.txt` 中列出所有依赖。
3. 在 AstrBot Web 管理界面中启用插件。
4. 配置以下参数（支持 Web 界面动态修改，无需重启）。

### 配置项说明

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `flaresolverr_url` | string | FlareSolverr 服务地址（绕过 Cloudflare） | `"http://localhost:8191/v1"` |
| `safe_mode_whitelist_groups` | list[int] | 关闭安全模式的群聊 ID 列表 | `[]` |
| `safe_mode_whitelist_users` | list[int] | 关闭安全模式的用户 ID 列表（仅私聊生效） | `[]` |
| `request_timeout` | int | 请求超时（秒） | `30` |
| `max_retries` | int | 最大重试次数 | `3` |
| `explicit_keywords` | list[str] | 始终拒绝的关键词（中英） | `["sex","porn","explicit","r18","gore","hentai"]` |
| `questionable_keywords` | list[str] | 安全模式下拒绝的关键词 | `["underwear","swimsuit","bikini","suggestive","暗示"]` |
| `stopwords` | list[str] | 自然语言分词停用词 | `["来","张","图","的","了","吧","吗","是"]` |
| `hot_order_field` | string | 热度排序字段（如 `score` 或 `favorites_count`） | `"score"` |
| `time_range_days` | int | “本周”等时间范围（天） | `7` |
| **翻译相关** | | | |
| `translation_mapping` | dict | 本地中英映射表 | `{"小蝶":"Fluttershy","崔克茜":"Trixie"}` |
| `custom_translate_url` | string | 自定义翻译 API 完整 URL | `""`（空则使用 LLM） |
| `custom_translate_api_key` | string | 自定义翻译 API 密钥 | `""` |
| `custom_translate_extra_params` | dict | 额外固定参数（如 `{"from":"zh","to":"en"}`） | `{}` |

---

## 🧩 使用示例

### 示例 1：显式分隔多标签
用户：,小蝶,夕阳,花坛
机器人：（随机返回一张同时包含 Fluttershy、sunset、flower 的图片）
### 示例 2：自然语言随机
用户：,来张小蝶美图
机器人：（分词提取“小蝶”、“美”，随机返回一张图片）
### 示例 3：本周最热
用户：,来张小蝶这周最火的图
机器人：（提取“小蝶”，时间限定本周，按热度排序取第一张）
### 示例 4：安全模式拦截
用户：,sex
机器人：（由于命中 explicit 黑名单，拒绝并提示）
---

## 🔒 安全模式详解

- **默认安全**：所有群/用户初始为安全模式，仅返回 `safe` 图片。
- **关闭方式**：将群聊 ID 或用户 ID 加入 `safe_mode_whitelist_groups` / `safe_mode_whitelist_users`。
- **优先级逻辑**：
  - 若群聊在白名单 → 全群关闭安全模式（允许 `questionable`）。
  - 若群聊不在白名单 → 即使某用户在白名单，该用户在群内仍受限制（群聊覆盖用户）。
  - 私聊时：仅判断用户 ID 白名单。
- **内容过滤**：
  - `explicit` 级别图片以及黑名单关键词在任何模式下均被禁止。
  - `questionable` 级别仅安全模式被屏蔽，关闭后允许。

---

## 🌐 翻译服务配置指南

### 1. 本地映射表（推荐）
在配置 `translation_mapping` 中添加键值对，例如 `"暮光闪闪":"Twilight Sparkle"`，命中后直接使用，无需网络。

### 2. 自定义翻译 API
- 填写 `custom_translate_url` 和 `custom_translate_api_key`（若需要）。
- 可在 `custom_translate_extra_params` 中固定参数（如 `from`、`to`）。
- 插件会以 POST JSON 格式发送 `{"text": "中文", ...}`，并尝试从响应中提取 `trans_result[0].dst` 或 `target_text` 等常见字段。若您的 API 返回结构不同，可通过调整代码适配。

### 3. LLM 翻译（默认）
若未配置自定义 API，则自动调用 AstrBot 当前的 LLM，发送提示词 `“请将以下中文翻译为英文，只输出翻译结果……”`，取返回文本。

### 4. 拼音降级
若以上均失败，使用 `pypinyin` 转为拼音（如 `"xiao_die"`），并记录警告日志。

---

## 📝 日志与调试
- 每次请求会记录：群聊/用户 ID、关键词、翻译来源、安全模式状态、查询结果数量。
- 日志使用 `astrbot.api.logger` 输出，可在 Web 管理界面查看。

---

## ⚠️ 注意事项
- **Cloudflare 防护**：必须配置 FlareSolverr 或使用 cloudscraper 等方案，否则无法访问网站。
- **翻译 API 密钥**：请勿硬编码，所有敏感信息从 Web 配置读取。
- **请求频率**：建议在代码中添加随机延迟，避免对目标服务器造成压力。
- **仅供学习**：本插件仅用于学习 Python 爬虫和 AstrBot 开发，请勿用于商业或恶意用途。

---

## 📄 许可证
（开发者自行选择，如 MIT、GPL 等）
