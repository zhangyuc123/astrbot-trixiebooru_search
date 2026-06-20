# TrixieBooru 图片搜索插件

> 基于 AstrBot 的图片搜索插件，从 [TrixieBooru](https://trixiebooru.org/) 网站按标签搜索并返回图片。支持中文自然语言输入、LLM 全量分词翻译、自动学习映射、图片去重、安全过滤等多种功能。

---

## 插件基本信息

- **插件名称**：`trixiebooru_search`
- **描述**：以逗号 `,` 或 `，` 开头触发，从 TrixieBooru 搜索并返回图片。支持中文自然语言自动提取关键词并翻译，支持英文标签直接查询，可搭配 `-hot`、`-gif`、`-infinite`、`-tag`、`-debug` 等参数。翻译服务使用 LLM 一次性完成分词、有效词翻译与无效词判定，结果自动学习保存。
- **版本**：v3.4.0
- **作者**：octony
- **平台适配**：aiocqhttp（AstrBot）

---

## ✨ 功能特性

### 1. 触发方式
- 仅当消息**以英文逗号 `,` 或中文逗号 `，` 开头**时触发（忽略前导空格）。
- 示例：`,小蝶` 或 `，来张瑞瑞美图`

### 2. 查询模式与参数

| 模式 | 说明 | 示例 |
|------|------|------|
| **自然语言搜索** | 输入中文句子，LLM 自动完成分词、翻译与无效词过滤，翻译后以 AND 关系查询。 | `,来张云宝帅图` |
| **逗号分隔多标签** | 用中英文逗号分隔多个标签（中英文混用），LLM 统一处理整段原文。 | `,小蝶,夕阳,solo` |
| **纯英文标签** | 不含中文的输入原样作为标签，不经过任何翻译。 | `,fluttershy,solo` |
| **直传标签** `-tag` | 不进行任何分词和翻译，直接将输入作为原始标签查询。 | `,oc:柒染 -tag` |
| **热度优先** `-hot` | 返回指定时间范围内点赞量最高的第一张图片。 | `,云宝 -hot` |
| **动图限定** `-gif` | 只搜索动图（animated）。 | `,pinkie pie -gif` |
| **取消时间限制** `-infinite` | 取消默认 60 天的时间过滤，搜索全站历史图片。 | `,暮光闪闪 -infinite` |
| **调试模式** `-debug` | 额外输出每个标签在图站的图片总数，方便判断标签有效性。 | `,瑞瑞,浴室 -debug` |

> 以上参数可自由组合，如 `,小蝶 -hot -gif -infinite`。

### 3. LLM 全量分析
- 当输入含中文且 jieba 分词后存在未命中本地词典的词时，自动调用 LLM 对**整段原文**进行一次性处理：
  - **分词**（`tokens`）：提取有效搜索词
  - **翻译**（`mappings`）：将有效词翻译为英文标签
  - **无效词判定**（`invalid`）：识别"来张"、"给我"等冗余词
- LLM 要求**只有逗号是分隔符**，空格等符号不算分隔，避免错误拆分。
- 翻译结果在**图片成功输出后**自动写入本地词典（jieba 分词词典、用户映射 `user_mapping.json`、停用词 `custom_stopwords.json`），下次直接命中。
- LLM 调用使用 30 秒超时，失败时自动回退拼音降级，不会阻塞服务器。

### 4. 图片去重与大小限制
- **大小限制**：仅返回 ≤ 1MB 的图片，过大自动跳过，尝试下一张。
- **12 小时去重**：已输出的图片 ID 12 小时内不会重复输出，若所有候选均重复，第 6 次会强制输出。
- 候选图片最多尝试 10 张，全部不符合时提示用户。

### 5. 安全模式

- **内容分级**：TrixieBooru 图片分为 `safe`（安全）、`questionable`（轻微暗示）、`suggestive`（挑逗）、`explicit`（明显色情）。
- **默认安全**：所有群聊/用户默认为安全模式，自动附加 `-explicit -questionable -suggestive`，仅返回 `safe` 图片。
- **白名单控制**：
  - `suggestive_whitelist`：列表中的群/用户仅过滤 explicit，允许 questionable/suggestive。
  - `all_whitelist`：列表中的群/用户不过滤任何内容（优先级最高）。
  - 私聊使用用户 ID 判断，群聊使用群聊 ID 判断。
- **explicit_keywords**：可配置拒绝的关键词列表，仅在非 `all` 模式下生效。

### 6. 并发限制
- 最多同时处理 2 个搜索任务，超出时回复"搜索任务繁忙，请稍后再试～"。

---

## 安装与配置

### 依赖
- Python 3.8+
- AstrBot 框架
- 依赖库：`jieba`, `pypinyin`, `curl_cffi`

### 安装步骤
1. 将插件文件夹放置于 AstrBot 的 `addons/plugins/` 目录下。
2. 安装依赖：
   ```bash
   pip install jieba pypinyin curl_cffi
   ```
3. 在 AstrBot Web 管理界面启用插件（或重启机器人）。

### 配置项说明
以下配置可在 `config.json` 中修改。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `api_key` | string | `""` | Trixiebooru API Key（可选，提升访问额度） |
| `local_translation_dict` | dict | 见源码 | 内置中→英映射表（角色/场景/动作等） |
| `stopwords` | list | 见源码 | 停用词列表，插件会自动追加 LLM 判定的无效词 |
| `tag_min_count` | int | 50 | 标签最少图片数量，低于此值的标签被过滤 |
| `time_range_days` | int | 60 | 默认时间范围（天），-infinite 可覆盖 |
| `request_timeout` | int | 30 | HTTP 请求超时（秒） |
| `max_tags_per_query` | int | 3 | 一次搜索最多使用的标签数（超出将截断） |
| `enable_llm_translate` | bool | true | 是否启用 LLM 全量翻译与无效词判定 |
| `enable_pinyin_fallback` | bool | true | LLM 失败后是否使用拼音降级 |
| `suggestive_whitelist` | list | `[]` | 暗示模式白名单（用户/群聊 ID） |
| `all_whitelist` | list | `[]` | 所有模式白名单（用户/群聊 ID） |
| `explicit_keywords` | list | `[]` | 全局拒绝的关键词列表 |
| `flaresolverr_url` | string | `""` | FlareSolverr 地址，用于绕过 Cloudflare |
| `mlp_dict_file` | string | `"mlp_dict.txt"` | jieba 自定义分词词典路径 |

---

## 使用示例

### 示例 1：自然语言搜索
用户：`,来张云宝`

机器人：LLM 分词提取"云宝" → Rainbow Dash，返回一张图。

### 示例 2：逗号分隔多标签
用户：`,小蝶,夕阳,solo`

机器人：LLM 分析整段原文，三个标签 AND 查询，随机返回一张图。

### 示例 3：参数组合
用户：`,暮光闪闪 -hot -infinite`

机器人：搜索全站 Twilight Sparkle 图片，返回点赞最高的一张。

### 示例 4：直传复杂标签
用户：`,oc:peakveil mist, solo -tag`

机器人：直接以 oc:peakveil mist 和 solo 作为标签查询，不翻译。

### 示例 5：调试模式
用户：`,瑞瑞,浴室 -debug`

机器人：返回图片的同时，附加消息显示每个标签的图片总数。

---

## 工作原理

1. **输入解析**：去除逗号前缀，识别并提取参数（-hot、-gif、-tag、-infinite、-debug）。

2. **分词预检**：纯英文输入直接作为标签；含中文时先用 jieba 分词，判断候选词数量（>10 拒绝）和是否全部命中本地映射。

3. **LLM 全量分析**：若存在未命中词，调用 LLM 对整段原文进行分词、翻译、无效词判定。LLM 失败则回退拼音降级。

4. **标签验证**：逐一查询每个标签的图片总数，剔除数量低于 tag_min_count 的冷门标签。

5. **安全过滤**：根据安全级别决定是否附加 -explicit -questionable -suggestive。

6. **图片获取**：构建查询，从 API 结果中按规则选择候选图片（大小 ≤ 1MB、未在 12 小时内输出过），下载后返回。

7. **自动学习**：图片成功输出后，LLM 的分词结果、翻译映射、无效词分别写入 jieba 词典、user_mapping.json、custom_stopwords.json。

---

## 相关文件

- `config.json` – 插件主配置
- `user_mapping.json` – LLM 学习的映射（自动生成/更新）
- `custom_stopwords.json` – LLM 学习的停用词（自动生成/更新）
- `hot_tags_cache.json` – 热门标签缓存（自动刷新）
- `mlp_dict.txt` – jieba 自定义分词词典（可手动维护）

---

## ⚠️ 注意事项

- **LLM 依赖**：翻译功能依赖 AstrBot 当前配置的大语言模型，若未配置则直接使用拼音降级。
- **Cloudflare 防护**：Trixiebooru 可能开启 Cloudflare 保护，若直连失败，请配置 flaresolverr_url 进行穿透。
- **请求频率**：短时间大量查询可能触发 429 限流，插件已内置自动重试，但仍建议合理使用。
- **词典维护**：建议将常用角色昵称（如"小呆"、"书记"）添加到 mlp_dict.txt 或本地映射中，以提升分词准确率。
- **图片大小**：插件限制返回 ≤ 1MB 的图片，若全部候选均过大，会提示用户。
- **仅供学习交流**：本插件仅用于学习 Python 爬虫与 AstrBot 插件开发，请勿用于商业或违规用途。

---

## 许可证

[MIT License]()
---
数据来源：Trixiebooru
插件平台：AstrBot
