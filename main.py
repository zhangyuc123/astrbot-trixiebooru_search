import asyncio
import json
import random
import re
from datetime import datetime, timedelta

import jieba.posseg as pseg
from pypinyin import pinyin, Style
from curl_cffi.requests import AsyncSession

from astrbot.api.all import *
from astrbot.api.message_components import *

@register("trixiebooru_search", "Developer", "Trixiebooru 图片查询插件（优化版）", "1.1.0")
class TrixiebooruPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self._init_default_config()

    def _init_default_config(self):
        """初始化与合并默认配置"""
        defaults = {
            "flaresolverr_url": "",  # 若配置，则优先通过此通道绕过 CF
            "safe_mode_whitelist_groups": [],
            "safe_mode_whitelist_users": [],
            "request_timeout": 30,
            "max_retries": 3,
            "explicit_keywords": ["sex", "porn", "explicit", "r18", "gore", "hentai"],
            "questionable_keywords": ["underwear", "swimsuit", "bikini", "suggestive", "暗示"],
            "stopwords": ["来", "张", "图", "的", "了", "吧", "吗", "是", "一下", "点", "看", "这周", "最近", "最火"],
            "hot_order_field": "score",
            "time_range_days": 7,
            "custom_translate_url": "",
            "custom_translate_api_key": "",
            "custom_translate_extra_params": {},
            "custom_translate_result_path": "translation", # 支持用点分隔提取，如 trans_result.0.dst
            "translation_mapping": {
                "小蝶": "Fluttershy", "崔克茜": "Trixie", "紫悦": "Twilight Sparkle",
                "云宝": "Rainbow Dash", "苹果嘉儿": "Applejack", "珍奇": "Rarity", "碧琪": "Pinkie Pie"
            }
        }
        for k, v in defaults.items():
            if k not in self.config:
                self.config[k] = v

    @filter.on_message()
    async def handle_message(self, event: AstrMessageEvent):
        message_str = event.message_str.strip()
        
        # 1. 唯一触发条件：首字符为英文逗号
        if not message_str.startswith(','):
            return
            
        content = message_str[1:].strip()
        if not content:
            yield event.plain_result("未识别到有效关键词，请使用逗号分隔或提供更明确的关键词。")
            return

        group_id = event.message_obj.group_id
        user_id = event.message_obj.sender.user_id
        is_safe = self._is_safe_mode(group_id, user_id)

        # 2. 模式判断与关键词提取
        parse_mode = "Mode 1 (显式分隔)"
        if ',' in content:
            keywords = [k.strip() for k in content.split(',') if k.strip()]
            is_hot, is_recent = False, False
        else:
            keywords, is_hot, is_recent = self._parse_natural_language(content)
            parse_mode = "Mode 3 (排序/时间)" if (is_hot or is_recent) else "Mode 2 (自然语言)"
            
        if not keywords:
            yield event.plain_result("未识别到有效关键词，请使用逗号分隔或提供更明确的关键词。")
            return

        # 3. 翻译关键词并记录来源
        eng_tags = []
        translation_sources = []
        for kw in keywords:
            translated, source = await self._translate(kw)
            if translated:
                eng_tags.append(translated.lower())
                translation_sources.append(f"{kw}->{translated}({source})")
            else:
                self.context.logger.warning(f"单个关键词翻译彻底失败并忽略: {kw}")

        if not eng_tags:
            yield event.plain_result("所有关键词翻译均失败，请稍后重试或尝试其他词汇。")
            return

        # 4. 安全模式拦截过滤 (黑名单)
        if any(tag in self.config["explicit_keywords"] for tag in eng_tags):
            yield event.plain_result("⚠️ 该关键词涉及明显色情内容，系统不予输出。")
            return
            
        if is_safe and any(tag in self.config["questionable_keywords"] for tag in eng_tags):
            yield event.plain_result("🛡️ 安全模式下无法获取含暗示内容的图片，请切换模式或联系管理员。")
            return

        yield event.plain_result("正在突破次元壁检索图片，请稍候...")

        # 5. 构建请求并抓取
        image_bytes, result_count = await self._fetch_image(eng_tags, is_safe, is_hot, is_recent)

        # 6. 记录标准审查日志
        self.context.logger.info(
            f"Trixiebooru请求 | 模式: {parse_mode} | 提取关键词: {keywords} | "
            f"翻译详情: {', '.join(translation_sources)} | 安全模式: {is_safe} | 结果数: {result_count}"
        )

        # 7. 返回结果
        if not image_bytes:
            yield event.plain_result(f"未找到同时包含 {eng_tags} 的图片（或时间范围内无相关图片），请尝试其他关键词。")
            return

        yield event.chain_result([Image.from_bytes(image_bytes)])

    def _parse_natural_language(self, content: str):
        """自然语言分词与指示词识别"""
        words = pseg.cut(content)
        candidate_keywords = []
        is_hot, is_recent = False, False
        
        time_indicators = ["本周", "这周", "最近"]
        hot_indicators = ["最火", "热度最高", "最热", "最受欢迎"]
        
        for word, flag in words:
            if flag in ['uj', 'ul', 'x', 'm', 'p', 'c'] or word in self.config["stopwords"]:
                continue
                
            if word in time_indicators:
                is_recent = True
                continue
            if word in hot_indicators:
                is_hot = True
                continue
                
            # 过滤长度 < 2 的词，除非是英文
            if (flag.startswith('n') or flag.startswith('v') or flag.startswith('a') or flag == 'eng'):
                if len(word) >= 2 or flag == 'eng':
                    candidate_keywords.append(word)

        return candidate_keywords, is_hot, is_recent

    async def _translate(self, text: str) -> tuple[str, str]:
        """按优先级翻译，返回 (翻译结果, 翻译来源)"""
        # 1. 优先：本地映射表
        mapping = self.config.get("translation_mapping", {})
        if text in mapping:
            return mapping[text], "mapping"

        # 2. 次优先：自定义翻译 API
        custom_url = self.config.get("custom_translate_url", "")
        if custom_url:
            try:
                payload = {"text": text, "from": "zh", "to": "en"}
                payload.update(self.config.get("custom_translate_extra_params", {}))
                api_key = self.config.get("custom_translate_api_key", "")
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                
                async with AsyncSession() as session:
                    resp = await session.post(custom_url, json=payload, headers=headers, timeout=10)
                    data = resp.json()
                    
                    # 动态路径解析 (例如 trans_result.0.dst)
                    path = self.config.get("custom_translate_result_path", "translation").split('.')
                    result = data
                    for key in path:
                        if isinstance(result, list) and key.isdigit():
                            result = result[int(key)]
                        elif isinstance(result, dict):
                            result = result.get(key)
                        else:
                            result = None
                            break
                            
                    if isinstance(result, str) and result.strip():
                        return result.strip(), "api"
            except Exception as e:
                self.context.logger.warning(f"自定义翻译API异常: {e}，将降级到LLM。")

        # 3. 降级：大语言模型 (LLM)
        try:
            provider = self.context.get_using_provider()
            if provider:
                prompt = f"请将以下中文翻译为英文，只输出翻译结果，不要添加任何解释或标点：{text}"
                llm_response = await provider.chat(prompt) 
                if llm_response and llm_response.completion_text:
                    return llm_response.completion_text.strip(), "llm"
        except Exception as e:
            self.context.logger.warning(f"LLM翻译异常: {e}，将降级到拼音。")

        # 4. 最终降级：拼音
        pinyin_list = pinyin(text, style=Style.NORMAL)
        result = ''.join([p[0].capitalize() for p in pinyin_list])
        return result, "pinyin"

    def _is_safe_mode(self, group_id: str, user_id: str) -> bool:
        """鉴权判断：群聊黑白名单优于个人"""
        whitelist_groups = [str(x) for x in self.config.get("safe_mode_whitelist_groups", [])]
        whitelist_users = [str(x) for x in self.config.get("safe_mode_whitelist_users", [])]
        
        if group_id:
            return str(group_id) not in whitelist_groups
        else:
            return str(user_id) not in whitelist_users

    async def _fetch_image(self, tags: list, is_safe: bool, is_hot: bool, is_recent: bool) -> tuple[bytes, int]:
        """构建检索参数、绕过 CF 并抓取图片 (返回图片字节流, 命中结果数量)"""
        query_parts = list(tags)
        query_parts.append("-rating:explicit")
        if is_safe:
            query_parts.append("-rating:questionable")
            
        if is_recent:
            days = self.config.get("time_range_days", 7)
            time_threshold = datetime.utcnow() - timedelta(days=days)
            query_parts.append(f"created_at.gte:{time_threshold.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        params = {"q": ",".join(query_parts), "per_page": 50}
        
        if is_hot:
            params["sf"] = self.config.get("hot_order_field", "score")
            params["sd"] = "desc"
        else:
            params["sf"] = "random"

        api_url = "https://trixiebooru.org/api/v1/json/search/images"
        timeout = self.config.get("request_timeout", 30)
        max_retries = self.config.get("max_retries", 3)
        flaresolverr_url = self.config.get("flaresolverr_url", "")

        for attempt in range(max_retries):
            try:
                data = None
                
                # 若配置了 FlareSolverr，则通过代理端点获取
                if flaresolverr_url:
                    # 将 query param 直接拼接入 URL 提供给 FlareSolverr
                    import urllib.parse
                    full_url = f"{api_url}?{urllib.parse.urlencode(params)}"
                    payload = {
                        "cmd": "request.get",
                        "url": full_url,
                        "maxTimeout": timeout * 1000
                    }
                    async with AsyncSession() as session:
                        resp = await session.post(flaresolverr_url, json=payload, timeout=timeout+10)
                        fs_data = resp.json()
                        if fs_data.get("status") == "ok":
                            html_content = fs_data.get("solution", {}).get("response", "")
                            # FlareSolverr 请求 API 时，JSON 常被包裹在 <html><body><pre> 中
                            json_match = re.search(r'(\{.*\})', html_content, re.DOTALL)
                            if json_match:
                                data = json.loads(json_match.group(1))
                            else:
                                data = json.loads(html_content)
                else:
                    # 默认使用 curl_cffi 模拟浏览器指纹硬解
                    async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                        resp = await session.get(api_url, params=params)
                        if resp.status_code == 200:
                            data = resp.json()

                if not data:
                    self.context.logger.warning(f"第 {attempt + 1} 次请求未获取到有效 JSON 数据。")
                    await asyncio.sleep(2)
                    continue
                    
                images = data.get("images", [])
                if not images:
                    return None, 0
                    
                selected_image = images[0] if is_hot else random.choice(images)
                image_url = selected_image.get("representations", {}).get("large") or selected_image.get("view_url")
                
                if image_url:
                    if image_url.startswith("//"):
                        image_url = "https:" + image_url
                        
                    # 无论配置何种模式，下发图片本身不经过 Cloudflare 人机验证验证码盾，直接 cffi 即可
                    async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                        img_response = await session.get(image_url)
                        if img_response.status_code == 200:
                            return img_response.content, len(images)
                            
            except Exception as e:
                self.context.logger.error(f"第 {attempt + 1} 次请求异常: {str(e)}")
                await asyncio.sleep(random.uniform(1.0, 3.0)) # 随机延迟防封
                
        return None, 0
