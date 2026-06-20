import asyncio
import base64
import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict, Any

import jieba
import jieba.posseg as pseg
from pypinyin import pinyin, Style
from curl_cffi.requests import AsyncSession

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image, Plain
from astrbot.api import logger


@register(
    name="trixiebooru_search",
    author="Developer",
    desc="呆站中文映射增强的 Trixiebooru 图片搜索插件，支持自然语言输入和 LLM 辅助翻译",
    version="3.4.0"
)
class TrixiebooruPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self._init_default_config()
        self._tag_cache = {}
        self._hot_english_set = set()
        self._user_mapping = {}

        # 并发控制：最多同时处理 2 个搜索任务
        self._search_semaphore = asyncio.Semaphore(2)
        # 暂存 LLM 全量分析结果（图片成功后再持久化）
        self._pending_llm_data = None
        
        self._load_jieba_dict()
        self._load_hot_tags()
        self._load_user_mapping()
        # 图片去重缓存：记录最近输出的图片 ID 与时间戳
        self._recent_image_cache: Dict[str, float] = {}

        # 加载 LLM 自动学习的无效词（自定义停用词）
        self._stopwords_file = os.path.join(os.path.dirname(__file__), "custom_stopwords.json")
        self._custom_stopwords = set()
        if os.path.exists(self._stopwords_file):
            try:
                with open(self._stopwords_file, 'r', encoding='utf-8') as f:
                    self._custom_stopwords = set(json.load(f))
                for w in self._custom_stopwords:
                    if w not in self.config["stopwords"]:
                        self.config["stopwords"].append(w)
                logger.info(f"已加载 {len(self._custom_stopwords)} 个自定义停用词")
            except Exception as e:
                logger.warning(f"加载自定义停用词失败: {e}")

    def _init_default_config(self):
        defaults = {
            "api_key": "",
            "local_translation_dict": {
                "小蝶": "Fluttershy", "柔柔": "Fluttershy",
                "萍琪": "Pinkie Pie", "萍琪派": "Pinkie Pie", "碧琪": "Pinkie Pie",
                "云宝": "Rainbow Dash", "云宝黛茜": "Rainbow Dash", "云宝黛西": "Rainbow Dash",
                "苹果杰克": "Applejack", "阿杰": "Applejack", "苹果嘉儿": "Applejack",
                "珍奇": "Rarity", "瑞瑞": "Rarity",
                "暮光闪闪": "Twilight Sparkle", "紫悦": "Twilight Sparkle",
                "斯派克": "Spike", "穗龙": "Spike",
                "塞拉斯蒂娅": "Princess Celestia", "大公主": "Princess Celestia",
                "露娜": "Princess Luna", "二公主": "Princess Luna", "月亮公主": "Princess Luna",
                "音韵": "Princess Cadance", "音韵公主": "Princess Cadance",
                "崔克茜": "Trixie", "特丽克西": "Trixie",
                "可拉": "Zecora", "泽科拉": "Zecora",
                "小马镇": "Ponyville", "坎特洛特": "Canterlot",
                "水晶帝国": "Crystal Empire",
                "可爱": "cute", "漂亮": "beautiful", "美丽": "beautiful",
                "帅": "cool", "酷": "cool",
                "睡觉": "sleeping", "洗澡": "bathing", "浴室": "bathroom",
                "花园": "garden", "森林": "forest",
                "夕阳": "sunset", "日出": "sunrise", "夜景": "night",
                "飞行": "flying", "魔法": "magic",
                "派对": "party", "蛋糕": "cake",
                "秋天": "autumn", "夏天": "summer",
                "公主": "princess",
                "湖边": "lake"
            },
            "explicit_keywords": [],
            "stopwords": [
                "的", "了", "在", "是", "着", "和", "与", "及", "而", "或", "被", "把", "让", "给", "就", "也", "还", "又",
                "啊", "吧", "呢", "吗", "哦", "呀", "哇", "哈", "嘿", "哼", "呗", "嘛", "哒", "捏", "之", "得", "对于", "关于",
                "请", "帮我", "给我", "麻烦", "谢谢", "能不能", "可以", "可以吗", "请问", "求", "希望", "命令", "速度", "快点",
                "机器人", "bot", "系统", "笨蛋", "傻瓜",
                "来", "发", "看", "瞧", "搜", "查", "寻找", "找找", "发张", "来张", "来点", "整点", "看看", "要要", "给个", "整两个",
                "图", "图片", "照片", "美图", "壁纸", "头像", "同人图", "原图", "插画", "手机壁纸", "电脑壁纸", "背景图", "截图", "萌图",
                "高清", "好看", "精美", "无损", "涩涩", "色色", "极品", "优质", "漂亮", "美丽", "可爱", "帅气", "全彩",
                "一", "一个", "一张", "一些", "一点", "个", "张", "点", "些", "下", "一下", "各种", "批", "有关", "相关", "所有"
            ],
            "tag_min_count": 50,
            "time_range_days": 60,
            "request_timeout": 30,
            "suggestive_whitelist": [],
            "all_whitelist": [],
            "flaresolverr_url": "",
            "enable_llm_translate": True,
            "enable_pinyin_fallback": True,
            "hot_tags_cache_hours": 24,
            "max_tags_per_query": 3,
            "enable_user_mapping": True,
            "user_mapping_file": "user_mapping.json",
            "hot_tags_file": "hot_tags_cache.json",
            "mlp_dict_file": "mlp_dict.txt"
        }
        for k, v in defaults.items():
            if k not in self.config:
                self.config[k] = v

    def _load_jieba_dict(self):
        dict_path = os.path.join(os.path.dirname(__file__), self.config.get("mlp_dict_file", "mlp_dict.txt"))
        if os.path.exists(dict_path):
            try:
                jieba.load_userdict(dict_path)
                logger.info("已加载 MLP 自定义分词词典")
            except Exception as e:
                logger.warning(f"加载自定义词典失败: {e}")
        important_words = {
            "日出": 100, "看日出": 100, "湖边": 100, "洗澡": 100,
            "夕阳": 100, "飞行": 100, "睡觉": 100, "派对": 100,
            "小蝶": 100, "瑞瑞": 100, "云宝": 100, "碧琪": 100,
            "苹果杰克": 100, "暮光闪闪": 100, "珍奇": 100,
        }
        for word, freq in important_words.items():
            jieba.add_word(word, freq)
        logger.info("已动态添加高频分词关键词")

    def _load_user_mapping(self):
        mapping_file = os.path.join(os.path.dirname(__file__), self.config.get("user_mapping_file", "user_mapping.json"))
        if os.path.exists(mapping_file):
            try:
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    self._user_mapping = json.load(f)
                logger.info(f"加载用户映射 {len(self._user_mapping)} 条")
            except Exception as e:
                logger.warning(f"加载用户映射失败: {e}")
                self._user_mapping = {}
        else:
            self._user_mapping = {}

    def _save_user_mapping(self, new_mappings: Dict[str, str]):
        if not new_mappings or not self.config.get("enable_user_mapping", True):
            return
        # 覆盖写入，new_mappings 中的键若已存在则覆盖
        self._user_mapping.update(new_mappings)
        file_path = os.path.join(os.path.dirname(__file__), self.config.get("user_mapping_file", "user_mapping.json"))
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self._user_mapping, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(new_mappings)} 条新映射到用户字典")
        except Exception as e:
            logger.error(f"保存用户映射失败: {e}")

    def _add_stopwords(self, words: List[str]):
        if not words:
            return
        updated = False
        for w in words:
            w = w.strip()
            if w and w not in self.config["stopwords"]:
                self.config["stopwords"].append(w)
                self._custom_stopwords.add(w)
                updated = True
        if updated:
            try:
                with open(self._stopwords_file, 'w', encoding='utf-8') as f:
                    json.dump(list(self._custom_stopwords), f, ensure_ascii=False, indent=2)
                logger.info(f"新增自定义停用词: {words}")
            except Exception as e:
                logger.warning(f"保存自定义停用词失败: {e}")

    def _load_hot_tags(self):
        cache_file = os.path.join(os.path.dirname(__file__), self.config.get("hot_tags_file", "hot_tags_cache.json"))
        need_refresh = True
        if os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                hours = self.config.get("hot_tags_cache_hours", 24)
                if time.time() - mtime < hours * 3600:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._hot_english_set = set(data.get("hot_english", []))
                    need_refresh = False
                    logger.info(f"已加载 {len(self._hot_english_set)} 个热门标签缓存")
            except:
                pass
        if need_refresh:
            asyncio.create_task(self._fetch_and_save_hot_tags(cache_file))

    async def _fetch_and_save_hot_tags(self, file_path: str):
        try:
            api_url = "https://trixiebooru.org/api/v1/json/search/tags"
            params = {"q": "*", "sf": "count", "sd": "desc", "per_page": 50}
            timeout = self.config.get("request_timeout", 30)
            flaresolverr_url = self.config.get("flaresolverr_url", "").strip()

            data = None
            if flaresolverr_url:
                import urllib.parse
                full_url = f"{api_url}?{urllib.parse.urlencode(params)}"
                payload = {"cmd": "request.get", "url": full_url, "maxTimeout": timeout * 1000}
                async with AsyncSession() as session:
                    resp = await session.post(flaresolverr_url, json=payload, timeout=timeout + 10)
                    fs_data = resp.json()
                    if fs_data.get("status") == "ok":
                        html_content = fs_data.get("solution", {}).get("response", "")
                        try:
                            data = json.loads(html_content)
                        except json.JSONDecodeError:
                            match = re.search(r'(\{.*\})', html_content, re.DOTALL)
                            if match:
                                data = json.loads(match.group(1))
            else:
                async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                    resp = await session.get(api_url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()

            if not data or "tags" not in data:
                logger.warning("获取热门标签失败")
                return

            explicit_set = set(self.config.get("explicit_keywords", []))
            hot_english = []
            for tag in data.get("tags", []):
                name = tag.get("name", "")
                if not name:
                    continue
                if any(ex in name.lower() for ex in explicit_set):
                    continue
                hot_english.append(name)

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump({"hot_english": hot_english}, f, ensure_ascii=False, indent=2)
            self._hot_english_set = set(hot_english)
            logger.info(f"已获取并缓存 {len(hot_english)} 个热门标签")
        except Exception as e:
            logger.error(f"获取热门标签失败: {e}")

    def _get_safety_level(self, group_id: Optional[int], user_id: Optional[int]) -> str:
        all_whitelist = self.config.get("all_whitelist", [])
        suggestive_whitelist = self.config.get("suggestive_whitelist", [])

        if group_id is not None:
            if group_id in all_whitelist:
                return "all"
            if group_id in suggestive_whitelist:
                return "suggestive"
            return "safe"
        else:
            if user_id is not None and user_id in all_whitelist:
                return "all"
            if user_id is not None and user_id in suggestive_whitelist:
                return "suggestive"
            return "safe"

    # ---------- 新的 LLM 全量分析与持久化方法 ----------
    async def _llm_analyze_full(self, original_text: str):
        """使用 LLM 一次性完成分词、有效词翻译、无效词判定（直接 await，超时控制）"""
        try:
            provider = self.context.get_using_provider()
            if not provider:
                return None

            prompt = f"""你是一个严格的翻译助手，**只输出 JSON，不输出任何其他内容**。

从用户的中文自然语言中提取搜索关键词，并翻译成图站英文标签。
规则：
1. **分隔符**：只有逗号（, 或 ，）是分隔符。空格、顿号、其他符号都不算分隔符，请将整体当作一个句子处理。
2. 有效词：角色、地点、时间、天气、氛围、动作、状态、情绪、形容词、物品等（如：小马镇→Ponyville，日出→sunrise，飞行→flying，可爱→cute）。
3. 无效词：纯粹的语法词、语气词、冗余请求词（如“来张”、“给我”、“图片”、“的”、“吧”等），放入 "invalid" 列表。
4. 翻译要求：用最常见、最短的英文标签，**严格单个词**，不要短语。
5. 输出格式（必须严格遵守，不要任何 markdown、解释、前后缀）：
{{"tokens":["分词1","分词2"],"mappings":{{"分词1":"english_tag1","分词2":"english_tag2"}},"invalid":["无效词1","无效词2"]}}

用户原文：{original_text}
现在输出 JSON："""

            logger.debug(f"[LLM全量分析] 发送 prompt")
            # 直接 await 异步方法，并用超时包裹
            if hasattr(provider, 'text_chat'):
                resp = await asyncio.wait_for(provider.text_chat(prompt), timeout=30)
            elif hasattr(provider, 'chat'):
                resp = await asyncio.wait_for(provider.chat(prompt), timeout=30)
            else:
                return None

            if not resp:
                return None

            result_text = resp.get_text() if hasattr(resp, 'get_text') else (resp.completion_text if hasattr(resp, 'completion_text') else str(resp))
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if not json_match:
                logger.warning(f"[LLM全量分析] 未找到 JSON，回复: {result_text[:200]}")
                return None
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                logger.warning(f"[LLM全量分析] JSON 解析失败，回复: {result_text[:200]}")
                return None

            tokens = data.get("tokens", [])
            mappings = data.get("mappings", {})
            invalid = data.get("invalid", [])
            cleaned_mappings = {}
            for k, v in mappings.items():
                v = re.sub(r'[^a-zA-Z0-9_\- ]', '', v).strip()
                if v:
                    cleaned_mappings[k] = v
            return (tokens, cleaned_mappings, invalid)

        except asyncio.TimeoutError:
            logger.warning("[LLM全量分析] 超时")
            return None
        except Exception as e:
            logger.warning(f"[LLM全量分析] 出错: {e}")
            return None

    def _apply_llm_data(self, tokens: List[str], mappings: Dict[str, str], invalid: List[str]):
        """将 LLM 结果写入本地词典（图片下载成功后调用）"""
        # 1. tokens 添加到 jieba 分词词典
        for token in tokens:
            if len(token) >= 2:
                jieba.add_word(token, 50)  # 添加或覆盖词频
        # 2. mappings 覆盖写入 user_mapping
        if mappings and self.config.get("enable_user_mapping", True):
            # 直接调用 _save_user_mapping，内部会 update 并持久化
            self._save_user_mapping(mappings)
        # 3. invalid 写入停用词
        if invalid:
            self._add_stopwords(invalid)

    async def _extract_tags(self, content: str) -> Tuple[List[str], List[str], str, Dict[str, str]]:
        clean_content = content.strip()
        logger.info(f"[提取标签] 输入: {clean_content}")
        if not clean_content:
            return [], [], 'empty', {}

        # ===== 统一先按逗号分割，再逐个片段处理 =====
        segments = re.split(r'[，,]+', clean_content)
        segments = [s.strip() for s in segments if s.strip()]
        logger.info(f"[提取标签] 逗号分隔片段: {segments}")

        all_cn_candidates = []
        all_en_candidates = []
        final_cn = []
        final_en = []
        pending_llm_data = None

        for seg in segments:
            # 纯英文片段（无中文字符）：直接作为英文标签
            if not re.search(r'[\u4e00-\u9fa5]', seg):
                all_en_candidates.append(seg)
                logger.info(f"[提取标签] 英文标签片段: {seg}")
            else:
                # 含中文片段：进入分析流程
                # 先用 jieba 分词获取候选词
                candidates = await asyncio.to_thread(self._parse_natural_language, seg)
                logger.info(f"[提取标签] 中文片段 jieba 候选词: {candidates}")

                if len(candidates) > 10:
                    logger.info(f"[提取标签] 片段 '{seg}' 候选词数 {len(candidates)} > 10，视为无效")
                    # 整个片段无效，跳过
                    continue

                # 构建当前可用映射
                raw_local_dict = self.config.get("local_translation_dict", {})
                if isinstance(raw_local_dict, str):
                    try:
                        local_mapping = json.loads(raw_local_dict)
                    except Exception:
                        local_mapping = {}
                else:
                    local_mapping = dict(raw_local_dict) if raw_local_dict else {}
                if self.config.get("enable_user_mapping", True):
                    local_mapping.update(self._user_mapping)

                # 检查是否有未命中本地词典的中文词
                has_unmatched = False
                for w in candidates:
                    if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_\-:./ ]*', w) and not re.search(r'[\u4e00-\u9fa5]', w):
                        continue
                    if w not in local_mapping:
                        has_unmatched = True
                        break

                if has_unmatched and self.config.get("enable_llm_translate", True):
                    # 调用 LLM 全量分析（只针对当前片段）
                    llm_result = await self._llm_analyze_full(seg)
                    if llm_result is not None:
                        tokens, mappings, invalid = llm_result
                        # 过滤无效词，有效词添加到结果
                        valid_tokens = [t for t in tokens if t not in invalid]
                        eng_tags = [mappings.get(t, t) for t in valid_tokens]
                        final_cn.extend(valid_tokens)
                        final_en.extend(eng_tags)
                        # 暂存 LLM 数据（多个片段可能都有，但只保留最后一个？这里简化处理，只保留最后一个片段的 LLM 数据）
                        # 更好的做法是合并，但考虑到通常只有一个片段含中文，暂用最后一个覆盖
                        pending_llm_data = (tokens, mappings, invalid)
                        logger.info(f"[LLM全量分析] 片段 '{seg}' 成功，tokens={tokens}, mappings={mappings}, invalid={invalid}")
                        continue

                # LLM 未启用或失败，使用本地映射 + 拼音降级
                en_candidates = [w for w in candidates if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_\-:./ ]*', w) and not re.search(r'[\u4e00-\u9fa5]', w)]
                cn_candidates = [w for w in candidates if w not in en_candidates]

                matched_cn = []
                matched_en = []
                for word in cn_candidates:
                    if word in local_mapping:
                        matched_cn.append(word)
                        matched_en.append(local_mapping[word])
                    else:
                        if self.config.get("enable_pinyin_fallback", True):
                            pinyin_list = pinyin(word, style=Style.NORMAL)
                            pinyin_res = ''.join([p[0].capitalize() for p in pinyin_list])
                            pinyin_tag = pinyin_res if pinyin_res else word
                            matched_en.append(pinyin_tag)
                            logger.info(f"  拼音兜底: {word} -> {pinyin_tag}")
                final_cn.extend(matched_cn + en_candidates)
                final_en.extend(matched_en + en_candidates)

        # 合并英文片段
        final_cn = final_cn + all_en_candidates
        final_en = final_en + all_en_candidates

        # 设置暂存数据（只保留最后一个 LLM 分析片段的）
        self._pending_llm_data = pending_llm_data

        if not final_en:
            logger.info("[提取标签] 最终未得到任何英文标签")
            return [], [], 'empty', {}

        source = 'llm_full' if pending_llm_data else 'mixed'
        return final_cn, final_en, source, {}

    def _parse_natural_language(self, content: str) -> List[str]:
        words = pseg.cut(content)
        candidate = []
        stopwords = set(self.config.get("stopwords", []))
        extra_stop = {"图", "张", "点", "个", "来", "看", "要", "吧", "吗", "呢", "啊", "哦", "哈", "嘿",
                      "看看", "来来", "要要", "一张", "图片", "照片", "美图"}

        for word, flag in words:
            if flag in ['uj', 'ul', 'x', 'm', 'p', 'c'] or word in stopwords or word in extra_stop:
                continue
            if flag in ['nr', 'ns', 'nz']:
                candidate.append(word)
            elif flag.startswith('n') and len(word) >= 2:
                candidate.append(word)
            elif flag.startswith('a') and len(word) >= 2:
                candidate.append(word)
            elif flag.startswith('v') and len(word) >= 2 and word not in {"看看", "来来", "要要"}:
                candidate.append(word)
            elif flag == 'eng':
                candidate.append(word)

        all_stop = stopwords | extra_stop
        cleaned = content
        for sw in sorted(all_stop, key=lambda x: -len(x)):
            cleaned = cleaned.replace(sw, "")
        cleaned = re.sub(r'[^\u4e00-\u9fa5]', '', cleaned)
        if cleaned:
            extra_words = jieba.cut(cleaned)
            for w in extra_words:
                if len(w) >= 2 and w not in all_stop and w not in extra_stop:
                    candidate.append(w)

        chinese_blocks = re.findall(r'[\u4e00-\u9fa5]{2,}', content)
        for block in chinese_blocks:
            for w in jieba.cut(block):
                if len(w) >= 2 and w not in all_stop and w not in extra_stop:
                    candidate.append(w)

        seen = set()
        unique_candidates = []
        for w in candidate:
            if w not in seen:
                seen.add(w)
                unique_candidates.append(w)
        return unique_candidates

    async def _fetch_image_info(self, tags: List[str], safety_level: str, is_hot: bool, is_gif: bool, is_infinite: bool = False) -> Tuple[Optional[bytes], int, Optional[str]]:
        query_parts = [tag.lower() for tag in tags]

        if not is_infinite:
            days = self.config.get("time_range_days", 60)
            if days > 0:
                query_parts.append(f"created_at.gte:{days} days ago")

        if safety_level == "safe":
            query_parts.append("-explicit")
            query_parts.append("-questionable")
            query_parts.append("-suggestive")
        elif safety_level == "suggestive":
            query_parts.append("-explicit")

        if is_gif:
            query_parts.append("animated")
        else:
            query_parts.append("-animated")

        params = {
            "q": ",".join(query_parts),
            "per_page": 100,
            "sf": "wilson_score",   # 唯一修改：改为按 Wilson score 降序排列
            "sd": "desc",
            "key": self.config.get("api_key", "").strip()
        }

        logger.info(f"API 请求参数: {params}")

        api_url = "https://trixiebooru.org/api/v1/json/search/images"
        timeout = self.config.get("request_timeout", 30)
        max_retries = 3
        flaresolverr_url = self.config.get("flaresolverr_url", "").strip()

        api_accessed = False
        last_error_msg = ""

        for attempt in range(max_retries):
            try:
                data = None
                if flaresolverr_url:
                    import urllib.parse
                    full_url = f"{api_url}?{urllib.parse.urlencode(params)}"
                    payload = {"cmd": "request.get", "url": full_url, "maxTimeout": timeout * 1000}
                    async with AsyncSession() as session:
                        resp = await session.post(flaresolverr_url, json=payload, timeout=timeout + 10)
                        fs_data = resp.json()
                        if fs_data.get("status") == "ok":
                            api_accessed = True
                            html_content = fs_data.get("solution", {}).get("response", "")
                            try:
                                data = json.loads(html_content)
                            except json.JSONDecodeError:
                                match = re.search(r'(\{.*\})', html_content, re.DOTALL)
                                if match:
                                    data = json.loads(match.group(1))
                        else:
                            last_error_msg = fs_data.get("message", "FlareSolverr 穿透失败")
                else:
                    async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                        resp = await session.get(api_url, params=params)
                        if resp.status_code == 200:
                            api_accessed = True
                            data = resp.json()
                        elif resp.status_code == 403:
                            last_error_msg = "403 Forbidden"
                        elif resp.status_code == 429:
                            last_error_msg = "429 Too Many Requests"
                            logger.warning(f"触发 429 限流，等待 5 秒后重试...")
                            await asyncio.sleep(5)
                            continue
                        else:
                            last_error_msg = f"HTTP {resp.status_code}"

                if data and "images" in data:
                    images = data.get("images", [])
                    if not images:
                        return None, 0, "NO_IMAGES"

                    total = data.get("total", 0)

                    # 准备候选图片列表
                    if is_hot:
                        candidates = images[:min(10, len(images))]
                    else:
                        shuffled = random.sample(images, min(len(images), 100))
                        candidates = shuffled[:min(10, len(shuffled))]

                    # 清理 12 小时过期的缓存
                    now = time.time()
                    expired_ids = [img_id for img_id, ts in self._recent_image_cache.items() if now - ts > 12 * 3600]
                    for img_id in expired_ids:
                        del self._recent_image_cache[img_id]

                    # 遍历候选图片，尝试下载并检查大小与重复
                    repeat_skip_count = 0
                    for img in candidates:
                        image_id = str(img.get("id"))
                        # 去重检查：若在缓存中且未跳过 5 次，则跳过
                        if image_id in self._recent_image_cache:
                            repeat_skip_count += 1
                            if repeat_skip_count < 6:
                                logger.info(f"图片 {image_id} 最近已输出过，跳过 (第{repeat_skip_count}次)")
                                continue
                            else:
                                logger.info(f"重复图片已达 5 次，第 6 次允许输出")

                        representations = img.get("representations", {})
                        image_url = representations.get("large") or img.get("view_url")
                        if not image_url:
                            for size in ["medium", "thumb", "small"]:
                                if size in representations:
                                    image_url = representations[size]
                                    break
                        if not image_url:
                            continue

                        if image_url.startswith("//"):
                            image_url = "https:" + image_url

                        try:
                            async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                                img_resp = await session.get(image_url)
                                if img_resp.status_code == 200:
                                    img_bytes = img_resp.content
                                    if len(img_bytes) <= 1 * 1024 * 1024:
                                        logger.info(f"成功下载图片 ID: {image_id}, 大小: {len(img_bytes)} 字节")
                                        return img_bytes, total, image_id
                                    else:
                                        logger.info(f"图片 ID: {image_id} 过大 ({len(img_bytes)} 字节)，尝试下一张")
                                        continue
                                elif img_resp.status_code == 429:
                                    logger.warning("下载图片时触发 429 限流，等待 5 秒")
                                    await asyncio.sleep(5)
                                    continue
                                else:
                                    logger.info(f"图片 ID: {image_id} 下载失败，状态码: {img_resp.status_code}")
                                    continue
                        except Exception as e:
                            logger.warning(f"图片 ID: {image_id} 下载异常: {e}")
                            continue

                    # 所有候选图片都失败或过大或重复
                    return None, total, "ALL_TOO_LARGE"

            except Exception as e:
                last_error_msg = f"网络异常: {str(e)}"
                logger.error(f"第 {attempt+1} 次抓取异常: {last_error_msg}")
                await asyncio.sleep(random.uniform(1.5, 3.0))

        if not api_accessed:
            return None, -1, f"NETWORK_ERROR: {last_error_msg}"
        return None, -1, "DOWNLOAD_FAILED"

    async def _validate_tag(self, tag: str) -> Tuple[bool, int]:
        tag = tag.lower()
        if tag in self._tag_cache:
            count = self._tag_cache[tag]
            threshold = self.config.get("tag_min_count", 50)
            logger.debug(f"标签验证(缓存) {tag}: 数量={count}, 阈值={threshold}, 通过={count >= threshold}")
            return count >= threshold, count

        api_url = "https://trixiebooru.org/api/v1/json/search/images"
        params = {"q": tag, "per_page": 1}
        if self.config.get("api_key"):
            params["key"] = self.config.get("api_key").strip()

        timeout = self.config.get("request_timeout", 30)
        flaresolverr_url = self.config.get("flaresolverr_url", "").strip()

        try:
            data = None
            if flaresolverr_url:
                import urllib.parse
                full_url = f"{api_url}?{urllib.parse.urlencode(params)}"
                payload = {"cmd": "request.get", "url": full_url, "maxTimeout": timeout * 1000}
                async with AsyncSession() as session:
                    resp = await session.post(flaresolverr_url, json=payload, timeout=timeout + 10)
                    fs_data = resp.json()
                    if fs_data.get("status") == "ok":
                        html_content = fs_data.get("solution", {}).get("response", "")
                        try:
                            data = json.loads(html_content)
                        except json.JSONDecodeError:
                            match = re.search(r'(\{.*\})', html_content, re.DOTALL)
                            if match:
                                data = json.loads(match.group(1))
            else:
                async with AsyncSession(impersonate="chrome110", timeout=timeout) as session:
                    resp = await session.get(api_url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                    elif resp.status_code == 429:
                        await asyncio.sleep(3)

            total = data.get("total", 0) if data else 0
            self._tag_cache[tag] = total
            threshold = self.config.get("tag_min_count", 50)
            logger.info(f"标签验证(在线) {tag}: 数量={total}, 阈值={threshold}, 通过={total >= threshold}")
            return total >= threshold, total
        except Exception as e:
            logger.warning(f"验证标签 {tag} 失败: {e}，默认保留")
            return True, 999

    @filter.regex(r"^\s*[,，]")
    async def handle_message(self, event: AstrMessageEvent):
        message_str = event.message_str.strip()
        if not (message_str.startswith(',') or message_str.startswith('，')):
            return

        raw_content = message_str[1:].strip()
        logger.info(f"收到搜索请求，原始内容: {raw_content}")
        if not raw_content:
            yield event.plain_result("未识别到有效关键词，请换个说法吧~")
            return

        if raw_content.lower() == "help":
            yield event.plain_result(self._get_help_text())
            return

        if self._search_semaphore.locked():
            yield event.plain_result("搜索任务繁忙，请稍后再试～")
            return

        async with self._search_semaphore:
            is_hot = "-hot" in raw_content
            is_gif = "-gif" in raw_content
            is_direct_tag = "-tag" in raw_content
            is_infinite = "-infinite" in raw_content
            is_debug = "-debug" in raw_content

            content = (raw_content.replace("-hot", "").replace("-gif", "")
                       .replace("-tag", "").replace("-infinite", "")
                       .replace("-debug", "").strip())

            group_id = self._get_group_id(event)
            user_id = self._get_user_id(event)
            safety_level = self._get_safety_level(group_id, user_id)

            if is_direct_tag:
                # 直传模式，不对内容进行翻译/分词
                segments = re.split(r'[，,]+', content)
                direct_tags = [s.strip() for s in segments if s.strip()]
                chinese_words = direct_tags
                eng_tags = direct_tags
                source = 'direct'
                logger.info(f"直传标签模式，标签: {eng_tags}")
                # 直传模式不产生 LLM 数据
                self._pending_llm_data = None
            else:
                chinese_words, eng_tags, source, _ = await self._extract_tags(content)

            logger.info(f"提取结果 - 中文词: {chinese_words} | 英文标签: {eng_tags} | 来源: {source}")

            if source == 'too_many':
                yield event.plain_result("输入内容过于复杂，提取到的关键词过多，请精简后重试～")
                return

            if not eng_tags:
                msg = "未能识别出有效的关键词，请换个说法试试~"
                if is_debug:
                    msg += "\n\n[调试] 未提取到任何标签，无图片数量数据。"
                yield event.plain_result(msg)
                return

            validated_tags = []
            invalid_tags = []
            tag_counts = {}
            for tag in eng_tags:
                is_valid, count = await self._validate_tag(tag)
                tag_counts[tag] = count
                if is_valid:
                    validated_tags.append(tag)
                else:
                    invalid_tags.append(tag)
                    logger.info(f"标签 {tag} 无效（图片数 {count} < {self.config.get('tag_min_count', 50)}），已过滤")

            debug_info = ""
            if is_debug:
                lines = ["[调试] 标签数量："]
                for tag in eng_tags:
                    valid_mark = "✓" if tag in validated_tags else "✗"
                    lines.append(f"  {tag} → {tag_counts[tag]} 张 {valid_mark}")
                debug_info = "\n".join(lines)

            if not validated_tags:
                msg = (f"所有标签都因图片数量过少（<{self.config.get('tag_min_count', 50)}）被过滤，请尝试其他关键词。")
                if is_debug:
                    msg += "\n\n" + debug_info
                yield event.plain_result(msg)
                return

            if invalid_tags:
                logger.info(f"已过滤无效标签: {invalid_tags}")

            max_tags = self.config.get("max_tags_per_query", 3)
            if len(validated_tags) > max_tags:
                logger.info(f"标签数量超过限制 {max_tags}，截断前: {validated_tags}，截断后: {validated_tags[:max_tags]}")
                validated_tags = validated_tags[:max_tags]

            if safety_level != "all":
                explicit_set = set(self.config.get("explicit_keywords", []))
                if any(tag.lower() in explicit_set for tag in validated_tags):
                    msg = "该关键词涉及敏感内容，已被中断。"
                    if is_debug:
                        msg += "\n\n" + debug_info
                    yield event.plain_result(msg)
                    return

            logger.info(f"开始搜索图片，最终有效标签: {validated_tags}，参数: hot={is_hot}, gif={is_gif}, infinite={is_infinite}, safety={safety_level}")
            image_bytes, total_found, status_or_id = await self._fetch_image_info(
                validated_tags, safety_level, is_hot, is_gif, is_infinite
            )

            logger.info(
                f"Trixiebooru 检索 | 标签: {validated_tags} | "
                f"参数: hot={is_hot}, gif={is_gif}, infinite={is_infinite}, safety={safety_level} | "
                f"结果数: {total_found} | 状态: {status_or_id}"
            )

            if not image_bytes:
                # 图片搜索失败，清空暂存数据，不保存任何 LLM 结果
                self._pending_llm_data = None
                if total_found == -1:
                    msg = f"⚠️ 无法连接到图库服务器。原因: {status_or_id}"
                elif total_found == 0 and status_or_id == "NO_IMAGES":
                    days = self.config.get("time_range_days", 60)
                    msg = (f"🔍 在图库中未找到关于 {validated_tags} 的图片。\n"
                           f"(已过滤 NSFW 及动图，搜索范围: {days}天)")
                else:
                    msg = f"❌ 检索失败，请稍后再试（状态: {status_or_id}）。"
                if is_debug:
                    msg += "\n\n" + debug_info
                yield event.plain_result(msg)
                return

            # 图片下载成功，应用暂存的 LLM 数据（如果有）
            if self._pending_llm_data is not None:
                tokens, mappings, invalid = self._pending_llm_data
                self._apply_llm_data(tokens, mappings, invalid)
                self._pending_llm_data = None  # 清空
            # 将成功输出的图片 ID 加入去重缓存（记录当前时间）
            if status_or_id:
                self._recent_image_cache[str(status_or_id)] = time.time()

            tag_display_parts = []
            en_to_cn = {en.lower(): ch for ch, en in zip(chinese_words, eng_tags)}
            for en in validated_tags:
                ch = en_to_cn.get(en.lower(), "原生标签")
                tag_display_parts.append(f"{ch}→{en}")
            tag_display = ', '.join(tag_display_parts)

            result_msg = f"ID → {status_or_id}\ntag: {tag_display}"

            b64_data = base64.b64encode(image_bytes).decode()
            response_parts = [Plain(result_msg + "\n"), Image(file=f"base64://{b64_data}")]
            if is_debug:
                response_parts.append(Plain("\n" + debug_info))
            yield event.chain_result(response_parts)

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        if hasattr(event, 'get_group_id'):
            return event.get_group_id()
        if hasattr(event, 'group_id'):
            return event.group_id
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'group_id'):
            return event.message_obj.group_id
        return None

    def _get_user_id(self, event: AstrMessageEvent) -> Optional[int]:
        if hasattr(event, 'get_user_id'):
            return event.get_user_id()
        if hasattr(event, 'user_id'):
            return event.user_id
        if hasattr(event, 'message_obj'):
            if hasattr(event.message_obj, 'sender') and hasattr(event.message_obj.sender, 'user_id'):
                return event.message_obj.sender.user_id
        return None

    def _get_help_text(self) -> str:
        return (
            "📷 TrixieBooru 图片搜索插件 v3.4.0\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "触发：使用逗号（不论中英文）触发\n"
            "用法：\n"
            "   ,关键词\n"
            "支持中文自然语言\n"
            "参数（加在末尾，可组合）：\n"
            "   -hot      取点赞最高的第一张\n"
            "   -gif      只返回动图\n"
            "   -tag     直接使用原标签，不翻译\n"
            "   -infinite 取消时间限制\n"
            "   -debug    显示调试信息\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔗 数据源：https://trixiebooru.org/"
        )
