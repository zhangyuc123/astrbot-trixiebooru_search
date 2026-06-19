import asyncio
import base64
import json
import random
import re
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict, Any

import jieba.posseg as pseg
from pypinyin import pinyin, Style
from curl_cffi.requests import AsyncSession

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image


@register(
    name="trixiebooru_search",
    author="Developer",
    desc="根据关键词从 TrixieBooru 搜索图片，支持多模式、安全过滤、智能翻译",
    version="1.0.0"
)
class TrixiebooruPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self._init_default_config()
        # 日志对象
        self.logger = context.logger

    def _init_default_config(self):
        """初始化默认配置，确保所有配置项存在"""
        defaults = {
            # Cloudflare 绕过
            "flaresolverr_url": "http://localhost:8191/v1",
            # 安全模式白名单
            "safe_mode_whitelist_groups": [],
            "safe_mode_whitelist_users": [],
            # 网络
            "request_timeout": 30,
            "max_retries": 3,
            # 黑名单
            "explicit_keywords": ["sex", "porn", "explicit", "r18", "gore", "hentai"],
            "questionable_keywords": ["underwear", "swimsuit", "bikini", "suggestive", "暗示"],
            # 分词停用词
            "stopwords": ["来", "张", "图", "的", "了", "吧", "吗", "是"],
            # 热度排序字段
            "hot_order_field": "score",
            "time_range_days": 7,
            # 翻译
            "translation_mapping": {"小蝶": "Fluttershy", "崔克茜": "Trixie"},
            "custom_translate_url": "",
            "custom_translate_api_key": "",
            "custom_translate_extra_params": {},
            "custom_translate_result_path": "trans_result[0].dst",  # JSONPath 风格
            # 全局启用开关（可选）
            "enabled_groups": [],   # 空列表表示所有群都启用
            "enabled_users": []     # 空列表表示所有用户都启用
        }
        for k, v in defaults.items():
            if k not in self.config:
                self.config[k] = v

    @filter.regex(r"^\s*,")
    async def handle_message(self, event: AstrMessageEvent):
        """主消息处理器"""
        message_str = event.message_str.strip()
        
        # 1. 触发条件：严格限定英文逗号开头
        if not message_str.startswith(','):
            return

        # 2. 获取群组和用户ID（适配不同版本）
        group_id = self._get_group_id(event)
        user_id = self._get_user_id(event)

        # 可选：全局启用控制
        if not self._is_enabled(group_id, user_id):
            return

        # 去除前导逗号
        content = message_str[1:].strip()
        if not content:
            yield event.plain_result("未识别到有效关键词，请使用逗号分隔或提供更明确的关键词。")
            return

        # 3. 判定安全模式
        is_safe = self._is_safe_mode(group_id, user_id)

        # 4. 模式解析
        parse_mode = "模式一（显式分隔）"
        if ',' in content:
            # 模式一：显式分隔
            keywords = [k.strip() for k in content.split(',') if k.strip()]
            is_hot, is_recent = False, False
        else:
            # 模式二/三：自然语言
            keywords, is_hot, is_recent = self._parse_natural_language(content)
            parse_mode = "模式三（自然语言+时间/排序）" if (is_hot or is_recent) else "模式二（自然语言随机）"

        if not keywords:
            yield event.plain_result("未识别到有效关键词，请使用逗号分隔或提供更明确的关键词。")
            return

        # 5. 翻译
        eng_tags = []
        translation_log = []
        for kw in keywords:
            translated, source = await self._translate(kw)
            if translated:
                eng_tags.append(translated.lower())
                translation_log.append(f"{kw}->{translated}({source})")
            else:
                self.logger.warning(f"关键词 {kw} 翻译失败，已忽略。")

        if not eng_tags:
            yield event.plain_result("所有关键词翻译均失败，请检查配置或重试。")
            return

        # 6. 安全拦截（黑名单检查）
        if any(tag in self.config["explicit_keywords"] for tag in eng_tags):
            yield event.plain_result("该关键词涉及明显色情内容，系统不予输出。")
            return

        if is_safe and any(tag in self.config["questionable_keywords"] for tag in eng_tags):
            yield event.plain_result("安全模式下无法获取含暗示内容的图片，请切换模式或联系管理员。")
            return

        # 7. 抓取图片
        image_bytes, total_found = await self._fetch_image(eng_tags, is_safe, is_hot, is_recent)

        # 8. 日志记录
        self.logger.info(
            f"Trixiebooru 检索 | 模式: {parse_mode} | 关键词: {keywords} | "
            f"翻译: {', '.join(translation_log)} | 安全模式: {is_safe} | 结果数: {total_found}"
        )

        # 9. 返回结果
        if not image_bytes:
            yield event.plain_result(f"未找到同时包含 {eng_tags} 的图片（或本周内无相关），请尝试其他关键词。")
            return

        # 以 Base64 格式发送图片（兼容 aiocqhttp）
        b64_data = base64.b64encode(image_bytes).decode()
        yield event.chain_result([Image.from_base64(b64_data)])

    # ---------- 辅助方法 ----------
    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """从事件中获取群ID，兼容不同版本"""
        # 优先使用标准方法
        if hasattr(event, 'get_group_id'):
            return event.get_group_id()
        # 尝试从 message_obj 或直接属性
        if hasattr(event, 'group_id'):
            return event.group_id
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'group_id'):
            return event.message_obj.group_id
        return None

    def _get_user_id(self, event: AstrMessageEvent) -> Optional[int]:
        """从事件中获取用户ID"""
        if hasattr(event, 'get_user_id'):
            return event.get_user_id()
        if hasattr(event, 'user_id'):
            return event.user_id
        if hasattr(event, 'message_obj'):
            if hasattr(event.message_obj, 'sender') and hasattr(event.message_obj.sender, 'user_id'):
                return event.message_obj.sender.user_id
        return None

    def _is_enabled(self, group_id: Optional[int], user_id: Optional[int]) -> bool:
        """检查是否启用（全局开关）"""
        enabled_groups = self.config.get("enabled_groups", [])
        enabled_users = self.config.get("enabled_users", [])
        # 若两者都为空，则全部启用
        if not enabled_groups and not enabled_users:
            return True
        # 若群在列表中或用户在列表中则启用
        if group_id is not None and group_id in enabled_groups:
            return True
        if user_id is not None and user_id in enabled_users:
            return True
        return False

    def _is_safe_mode(self, group_id: Optional[int], user_id: Optional[int]) -> bool:
        """
        安全模式判断：群聊覆盖用户
        返回 True 表示安全模式开启（限制 questionable）
        """
        whitelist_groups = self.config.get("safe_mode_whitelist_groups", [])
        whitelist_users = self.config.get("safe_mode_whitelist_users", [])

        if group_id is not None:
            # 群聊中：若群在 whitelist 中则关闭安全模式(False)
            return group_id not in whitelist_groups
        else:
            # 私聊：用户是否在白名单
            return user_id not in whitelist_users if user_id is not None else True

    def _parse_natural_language(self, content: str) -> Tuple[List[str], bool, bool]:
        """
        自然语言分词提取关键词，并识别时间/热度指示
        返回 (关键词列表, is_hot, is_recent)
        """
        words = pseg.cut(content)
        candidate = []
        is_hot = False
        is_recent = False

        time_indicators = {"本周", "这周", "最近"}
        hot_indicators = {"最火", "热度最高", "最热", "最受欢迎"}
        stopwords = set(self.config.get("stopwords", []))

        for word, flag in words:
            # 过滤助词、标点、停用词
            if flag in ['uj', 'ul', 'x', 'm', 'p', 'c'] or word in stopwords:
                continue

            # 时间和排序指示
            if word in time_indicators:
                is_recent = True
                continue
            if word in hot_indicators:
                is_hot = True
                continue

            # 保留名词、动词、形容词（长度>=2 或英文）
            if flag.startswith('n') or flag.startswith('v') or flag.startswith('a') or flag == 'eng':
                if len(word) >= 2 or flag == 'eng':
                    candidate.append(word)

        return candidate, is_hot, is_recent

    async def _translate(self, text: str) -> Tuple[str, str]:
        """
        多级翻译降级：
        1. 本地映射表
        2. 自定义翻译 API（支持 JSONPath 提取）
        3. LLM 翻译
        4. 拼音降级
        返回 (翻译结果, 来源)
        """
        # 1. 本地映射表
        mapping = self.config.get("translation_mapping", {})
        if text in mapping:
            return mapping[text], "映射表"

        # 2. 自定义翻译 API
        custom_url = self.config.get("custom_translate_url", "").strip()
        if custom_url:
            try:
                payload = {"text": text}
                # 合并额外参数（通常包含 from/to）
                extra = self.config.get("custom_translate_extra_params", {})
                payload.update(extra)
                api_key = self.config.get("custom_translate_api_key", "")
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

                async with AsyncSession() as session:
                    resp = await session.post(custom_url, json=payload, headers=headers, timeout=10)
                    data = resp.json()
                    result = self._extract_by_path(data, self.config.get("custom_translate_result_path", "trans_result[0].dst"))
                    if result:
                        return str(result).strip(), "自定义API"
            except Exception as e:
                self.logger.warning(f"自定义翻译失败: {e}，降级到 LLM")

        # 3. LLM 翻译
        try:
            provider = self.context.get_using_provider()
            if provider:
                prompt = f"请将以下中文翻译为英文，只输出翻译结果，不要添加任何解释或标点：{text}"
                # 适配不同 LLM 接口
                if hasattr(provider, 'text_chat'):
                    llm_response = await provider.text_chat(prompt)
                elif hasattr(provider, 'chat'):
                    llm_response = await provider.chat(prompt)
                else:
                    llm_response = None

                if llm_response:
                    # 获取文本内容
                    if hasattr(llm_response, 'get_text'):
                        result = llm_response.get_text()
                    elif hasattr(llm_response, 'completion_text'):
                        result = llm_response.completion_text
                    elif isinstance(llm_response, str):
                        result = llm_response
                    else:
                        result = None
                    if result:
                        return result.strip(), "LLM"
        except Exception as e:
            self.logger.warning(f"LLM 翻译失败: {e}，降级到拼音")

        # 4. 拼音降级
        pinyin_list = pinyin(text, style=Style.NORMAL)
        pinyin_res = ''.join([p[0].capitalize() for p in pinyin_list])
        return pinyin_res, "拼音降级"

    def _extract_by_path(self, data: Dict, path: str) -> Optional[str]:
        """
        简易 JSONPath 提取，支持形如 'trans_result[0].dst' 或 'data.result'
        """
        try:
            parts = re.split(r'\[(\d+)\]\.?|\.', path)
            # parts 如 ['trans_result', '0', 'dst']
            current = data
            i = 0
            while i < len(parts):
                part = parts[i]
                if part == '':
                    i += 1
                    continue
                if part.isdigit():
                    idx = int(part)
                    if isinstance(current, list) and idx < len(current):
                        current = current[idx]
                    else:
                        return None
                else:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        return None
                i += 1
            return str(current) if current is not None else None
        except Exception:
            return None

    async def _fetch_image(self, tags: List[str], is_safe: bool, is_hot: bool, is_recent: bool) -> Tuple[Optional[bytes], int]:
        """
        从 Philomena API 获取图片
        返回 (图片二进制数据, 总匹配数)
        """
        # 构建查询
        query_parts = list(tags)
        query_parts.append("-rating:explicit")
        if is_safe:
            query_parts.append("-rating:questionable")

        if is_recent:
            days = self.config.get("time_range_days", 7)
            threshold = datetime.utcnow() - timedelta(days=days)
            query_parts.append(f"created_at.gte:{threshold.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        params = {"q": ",".join(query_parts), "per_page": 50}
        if is_hot:
            params["sf"] = self.config.get("hot_order_field", "score")
            params["sd"] = "desc"
        else:
            params["sf"] = "random"

        api_url = "https://trixiebooru.org/api/v1/json/search/images"
        timeout = self.config.get("request_timeout", 30)
        max_retries = self.config.get("max_retries", 3)
        flaresolverr_url = self.config.get("flaresolverr_url", "").strip()

        for attempt in range(max_retries):
            try:
                data = None
                if flaresolverr_url:
                    # 通过 FlareSolverr
                    import urllib.parse
                    full_url = f"{api_url}?{urllib.parse.urlencode(params)}"
                    payload = {
                        "cmd": "request.get",
                        "url": full_url,
                        "maxTimeout": timeout * 1000
                    }
                    async with AsyncSession() as session:
                        resp = await session.post(flaresolverr_url, json=payload, timeout=timeout + 10)
                        fs_data = resp.json()
                        if fs_data.get("status") == "ok":
                            html_content = fs_data.get("solution", {}).get("response", "")
                            # 尝试解析 JSON：直接解析或正则提取
                            try:
                                data = json.loads(html_content)
                            except json.JSONDecodeError:
                                # 正则提取花括号中的 JSON
                                match = re.search(r'(\{.*\})', html_content, re.DOTALL)
                                if match:
                                    data = json.loads(match.group(1))
                else:
                    # 使用 curl_cffi 指纹
                    async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                        resp = await session.get(api_url, params=params)
                        if resp.status_code == 200:
                            data = resp.json()

                if not data or "images" not in data:
                    continue

                images = data.get("images", [])
                if not images:
                    return None, 0

                # 选图：热度排序取第一张，否则随机
                selected = images[0] if is_hot else random.choice(images)
                # 获取图片 URL（优先 large，其次 view_url）
                representations = selected.get("representations", {})
                image_url = representations.get("large") or selected.get("view_url")
                if not image_url:
                    # 尝试其他尺寸
                    for size in ["medium", "thumb", "small"]:
                        if size in representations:
                            image_url = representations[size]
                            break
                if not image_url:
                    continue

                if image_url.startswith("//"):
                    image_url = "https:" + image_url

                # 下载图片
                async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                    img_resp = await session.get(image_url)
                    if img_resp.status_code == 200:
                        return img_resp.content, len(images)

            except Exception as e:
                self.logger.error(f"第 {attempt+1} 次抓取异常: {e}")
                await asyncio.sleep(random.uniform(1.0, 2.0))

        return None, 0
