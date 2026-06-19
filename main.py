import asyncio
import json
import random
import time
from datetime import datetime, timedelta
import jieba.posseg as pseg
from pypinyin import pinyin, Style
from curl_cffi.requests import AsyncSession

from astrbot.api.all import *
from astrbot.api.message_components import *

@register("trixiebooru_search", "Developer", "Trixiebooru 图片查询插件（仅供学习）", "1.0.0")
class TrixiebooruPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        # 初始化默认配置
        self._init_default_config()

    def _init_default_config(self):
        """合并默认配置项"""
        defaults = {
            "flaresolverr_url": "", # 若使用 FlareSolverr 可填入
            "safe_mode_whitelist_groups": [],
            "safe_mode_whitelist_users": [],
            "request_timeout": 30,
            "max_retries": 3,
            "explicit_keywords": ["sex", "porn", "explicit", "r18", "gore", "hentai"],
            "questionable_keywords": ["underwear", "swimsuit", "bikini", "suggestive", "暗示"],
            "stopwords": ["来", "张", "图", "的", "了", "吧", "吗", "是", "一下", "点"],
            "hot_order_field": "score",
            "time_range_days": 7,
            "custom_translate_url": "",
            "custom_translate_api_key": "",
            "custom_translate_extra_params": {},
            "translation_mapping": {"小蝶": "Fluttershy", "崔克茜": "Trixie", "紫悦": "Twilight Sparkle", "云宝": "Rainbow Dash", "苹果嘉儿": "Applejack", "珍奇": "Rarity", "碧琪": "Pinkie Pie"}
        }
        for k, v in defaults.items():
            if k not in self.config:
                self.config[k] = v

    @filter.on_message()
    async def handle_message(self, event: AstrMessageEvent):
        message_str = event.message_str.strip()
        
        # 1. 唯一唤醒词判断：必须以英文字符逗号开头
        if not message_str.startswith(','):
            return
            
        content = message_str[1:].strip()
        if not content:
            yield event.plain_result("未识别到有效关键词，请使用逗号分隔或提供更明确的关键词。")
            return

        # 获取环境信息（群组ID/用户ID）
        group_id = event.message_obj.group_id
        user_id = event.message_obj.sender.user_id

        # 2. 判断解析模式
        if ',' in content:
            # 模式一：显式分隔
            keywords = [k.strip() for k in content.split(',') if k.strip()]
            is_hot = False
            is_recent = False
        else:
            # 模式二/三：自然语言分词
            keywords, is_hot, is_recent = self._parse_natural_language(content)
            
        if not keywords:
            yield event.plain_result("未识别到有效关键词，请使用逗号分隔或提供更明确的关键词。")
            return

        # 3. 翻译关键词
        eng_tags = []
        for kw in keywords:
            translated = await self._translate(kw)
            if translated:
                eng_tags.append(translated.lower())

        if not eng_tags:
            yield event.plain_result("翻译关键词失败，请稍后重试。")
            return

        # 4. 安全模式检查
        is_safe = self._is_safe_mode(group_id, user_id)
        
        # 检查 Explicit 黑名单 (全局限制)
        if any(tag in self.config["explicit_keywords"] for tag in eng_tags):
            yield event.plain_result("⚠️ 该关键词涉及明显色情或违规内容，系统不予输出。")
            return
            
        # 检查 Questionable 黑名单 (仅安全模式限制)
        if is_safe and any(tag in self.config["questionable_keywords"] for tag in eng_tags):
            yield event.plain_result("🛡️ 安全模式下无法获取含暗示内容的图片，请切换模式或联系管理员。")
            return

        # 5. 构建并发送请求
        yield event.plain_result("正在通过 Cloudflare 防护查询图片，请稍候...")
        image_bytes = await self._fetch_image(eng_tags, is_safe, is_hot, is_recent)

        if not image_bytes:
            yield event.plain_result(f"未找到同时包含 {eng_tags} 的图片（或时间范围内无结果），请尝试其他关键词。")
            return

        # 6. 返回图片
        yield event.chain_result([Image.from_bytes(image_bytes)])

    def _parse_natural_language(self, content: str):
        """使用 jieba 进行自然语言解析，提取核心词和时间/排序标识"""
        words = pseg.cut(content)
        candidate_keywords = []
        is_hot = False
        is_recent = False
        
        time_indicators = ["本周", "这周", "最近"]
        hot_indicators = ["最火", "热度最高", "最热", "最受欢迎"]
        
        for word, flag in words:
            # 过滤语气词、助词、标点
            if flag in ['uj', 'ul', 'x', 'm', 'p', 'c']:
                continue
            # 过滤停用词
            if word in self.config["stopwords"]:
                continue
                
            # 检查指示词
            if word in time_indicators:
                is_recent = True
                continue
            if word in hot_indicators:
                is_hot = True
                continue
                
            # 保留名词、动词、形容词，且长度 >= 2（除非是特殊单字）
            if flag.startswith('n') or flag.startswith('v') or flag.startswith('a') or flag == 'eng':
                if len(word) >= 2 or flag == 'eng':
                    candidate_keywords.append(word)

        return candidate_keywords, is_hot, is_recent

    async def _translate(self, text: str) -> str:
        """多级降级翻译策略"""
        # 1. 本地映射表
        mapping = self.config.get("translation_mapping", {})
        if text in mapping:
            return mapping[text]

        # 2. 自定义 API 翻译
        custom_url = self.config.get("custom_translate_url", "")
        if custom_url:
            try:
                # 此处以简单的通用 JSON POST 为例，具体需要根据用户配置的 API 适配
                payload = {"text": text, "from": "zh", "to": "en"}
                payload.update(self.config.get("custom_translate_extra_params", {}))
                api_key = self.config.get("custom_translate_api_key", "")
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                
                async with AsyncSession() as session:
                    resp = await session.post(custom_url, json=payload, headers=headers, timeout=5)
                    data = resp.json()
                    # 尝试捕获常见翻译 API 的返回字段
                    result = data.get("translation") or data.get("result") or data.get("target_text")
                    if isinstance(result, list):
                        result = result[0]
                    if result:
                        return result
            except Exception as e:
                self.context.logger.warning(f"自定义翻译 API 失败: {e}，降级至 LLM")

        # 3. LLM 翻译
        try:
            # 获取当前框架配置的默认 LLM provider
            provider = self.context.get_using_provider()
            if provider:
                prompt = f"请将以下中文翻译为英文，只输出翻译结果，不要添加任何解释或标点：{text}"
                # 此处调用 AstrBot 封装的 LLM 会话接口
                llm_response = await provider.chat(prompt) 
                if llm_response and llm_response.completion_text:
                    return llm_response.completion_text.strip()
        except Exception as e:
            self.context.logger.warning(f"LLM 翻译失败: {e}，降级至拼音")

        # 4. 拼音降级
        pinyin_list = pinyin(text, style=Style.NORMAL)
        return ''.join([p[0].capitalize() for p in pinyin_list])

    def _is_safe_mode(self, group_id: str, user_id: str) -> bool:
        """判定当前会话是否为安全模式（核心优先级规则）"""
        whitelist_groups = [str(x) for x in self.config.get("safe_mode_whitelist_groups", [])]
        whitelist_users = [str(x) for x in self.config.get("safe_mode_whitelist_users", [])]
        
        if group_id:
            # 群聊优先级最高：群在白名单则关闭，否则强制安全模式（无视用户）
            if str(group_id) in whitelist_groups:
                return False
            return True
        else:
            # 私聊只判断用户白名单
            if str(user_id) in whitelist_users:
                return False
            return True

    async def _fetch_image(self, tags: list, is_safe: bool, is_hot: bool, is_recent: bool) -> bytes:
        """构建请求与 CF 绕过下载图片"""
        # 构建 Philomena 查询标签
        query_parts = list(tags)
        
        # 强制过滤明显色情
        query_parts.append("-rating:explicit")
        # 安全模式额外过滤暗示内容
        if is_safe:
            query_parts.append("-rating:questionable")
            
        # 时间过滤
        if is_recent:
            days = self.config.get("time_range_days", 7)
            time_threshold = datetime.utcnow() - timedelta(days=days)
            query_parts.append(f"created_at.gte:{time_threshold.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        q_str = ",".join(query_parts)
        params = {"q": q_str, "per_page": 50}

        # 排序机制
        if is_hot:
            params["sf"] = self.config.get("hot_order_field", "score")
            params["sd"] = "desc"
        else:
            # 随机获取，为了提升随机性可以使用 random 排序（如果 API 支持），或拉取后在本地随机选
            params["sf"] = "random"

        api_url = "https://trixiebooru.org/api/v1/json/search/images"
        
        # 请求重试机制与 CF 绕过
        max_retries = self.config.get("max_retries", 3)
        timeout = self.config.get("request_timeout", 30)
        
        for attempt in range(max_retries):
            try:
                # 使用 curl_cffi 模拟浏览器指纹绕过 CF
                async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                    response = await session.get(api_url, params=params)
                    
                    if response.status_code != 200:
                        self.context.logger.error(f"Trixiebooru API 请求失败: HTTP {response.status_code}")
                        continue
                        
                    data = response.json()
                    images = data.get("images", [])
                    
                    if not images:
                        return None
                        
                    # 选择图片（如果按热度排，取第一张；如果是普通查询，由于 per_page=50，可以本地再随机化一次）
                    selected_image = images[0] if is_hot else random.choice(images)
                    
                    # 获取原图或大图 URL
                    image_url = selected_image.get("representations", {}).get("large") or selected_image.get("view_url")
                    if not image_url:
                        continue
                        
                    # 补全 URL
                    if image_url.startswith("//"):
                        image_url = "https:" + image_url
                        
                    # 下载图片二进制数据
                    img_response = await session.get(image_url)
                    if img_response.status_code == 200:
                        return img_response.content
                        
            except Exception as e:
                self.context.logger.warning(f"第 {attempt + 1} 次请求出错: {e}")
                await asyncio.sleep(1) # 简单退避策略
                
        return None