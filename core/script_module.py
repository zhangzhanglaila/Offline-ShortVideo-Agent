# -*- coding: utf-8 -*-
"""
脚本&分镜生成模块 - 本地Ollama推理
输入赛道+选题，自动生成：黄金3秒钩子、15-60s口播脚本、分镜表、时长、标签
"""
import json
import time
import re
from typing import Dict, List, Optional
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT

class ScriptModule:
    """脚本生成模块 - 基于Ollama本地LLM"""

    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL):
        """初始化脚本生成模块"""
        self.base_url = base_url
        self.model = model
        self.api_url = f"{base_url}/api/generate"

    def generate_script(self, topic: Dict, platform: str = "抖音",
                       video_duration: int = 30, style: str = "爆款") -> Dict:
        """
        生成完整口播脚本

        参数:
            topic: 选题字典，包含 title, hook, category, tags 等
            platform: 目标平台 (抖音/小红书/视频号)
            video_duration: 视频时长(秒)
            style: 风格 (爆款/温和/专业)

        返回:
            包含脚本、分镜、标题、描述、话题标签的字典
        """
        # 构建Prompt
        prompt = self._build_script_prompt(topic, platform, video_duration, style)

        # 调用Ollama生成，失败则尝试云端API
        script_content = self._call_ollama(prompt)
        if 'error' in script_content or not script_content.strip():
            script_content = self._call_cloud_api(prompt)

        # 解析生成的内容
        result = self._parse_script_content(script_content, topic, platform)

        return result

    def _build_script_prompt(self, topic: Dict, platform: str, duration: int, style: str) -> str:
        """构建脚本生成Prompt"""
        category = topic.get("category", "通用")
        title = topic.get("title", "")
        hook = topic.get("hook", "")
        tags = ",".join(topic.get("tags", []))

        return f"""你是一位顶级短视频爆款文案专家。请根据以下选题信息，生成一段{duration}秒的口播脚本。

【选题信息】
- 赛道: {category}
- 标题: {title}
- 钩子: {hook}
- 标签: {tags}

【要求】
1. 必须包含"黄金3秒开头" - 用悬念或痛点抓住观众
2. 口播脚本控制在{duration}秒内（约{duration*3}个字）
3. 语言风格: {style}，口语化，有感染力
4. 必须包含行动号召(CTA)引导点赞关注
5. 输出格式为JSON，包含以下字段:
   - "hook": 黄金3秒开头文案
   - "body": 主体内容(3-5个短句)
   - "cta": 结尾行动号召
   - "full_script": 完整口播文案(hook+body+cta)
   - "storyboard": 分镜表(数组，每个元素包含: 时间点, 画面描述, 字幕要点, 时长)

【分镜表格式示例】
[
  {{"time": "0-3秒", "scene": "开场画面", "subtitle": "关键字幕", "duration": 3}},
  {{"time": "3-8秒", "scene": "问题抛出", "subtitle": "痛点文字", "duration": 5}}
]

请直接输出JSON，不要有其他文字:"""

    def _call_ollama(self, prompt: str, timeout: int = OLLAMA_TIMEOUT) -> str:
        """调用Ollama API生成内容"""
        import urllib.request
        import urllib.error

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.8,
                "top_p": 0.9,
                "num_predict": 1024,
            }
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("response", "")
        except urllib.error.URLError as e:
            return f"{{'error': 'Ollama连接失败: {str(e)}', 'script': {{}}}}"
        except json.JSONDecodeError as e:
            return f"{{'error': '响应解析失败', 'script': {{}}}}"

    def _call_cloud_api(self, prompt: str) -> str:
        """调用云端API（DeepSeek/MiniMax）生成内容"""
        import os
        try:
            import requests
            # 优先使用 DeepSeek
            api_key = os.environ.get('DEEPSEEK_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')
            api_base = os.environ.get('DEEPSEEK_API_BASE', '') or os.environ.get('OPENAI_API_BASE', 'https://api.deepseek.com/v1')
            model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')

            if not api_key:
                return "{'error': '未配置云端API密钥', 'script': {}}"

            response = requests.post(
                f'{api_base}/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 1024,
                    'temperature': 0.8
                },
                timeout=60,
                proxies={'http': None, 'https': None}
            )
            result = response.json()
            return result['choices'][0]['message']['content']
        except Exception as e:
            return f"{{'error': '云端API调用失败: {str(e)}', 'script': {{}}}}"

    def _parse_script_content(self, content: str, topic: Dict, platform: str) -> Dict:
        """解析Ollama返回的内容"""
        # 提取JSON部分
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            try:
                parsed = json.loads(json_str)
            except json.JSONDecodeError:
                parsed = self._fallback_parse(content)
        else:
            parsed = self._fallback_parse(content)

        # 构建完整结果
        result = {
            "topic_id": topic.get("id"),
            "topic_title": topic.get("title"),
            "category": topic.get("category"),
            "platform": platform,
            "hook": parsed.get("hook", topic.get("hook", "")),
            "body": parsed.get("body", ""),
            "cta": parsed.get("cta", "觉得有用的话，点个赞呗！"),
            "full_script": parsed.get("full_script", ""),
            "storyboard": parsed.get("storyboard", []),
            "suggested_tags": topic.get("tags", []),
            "raw_llm_response": content,
        }

        return result

    def _fallback_parse(self, content: str) -> Dict:
        """降级解析 - 当JSON解析失败时"""
        # 尝试按行分割提取内容
        lines = content.split("\n")

        hook = ""
        body = ""
        cta = ""
        storyboard = []

        for line in lines:
            line = line.strip()
            if "钩子" in line or "开头" in line or "hook" in line.lower():
                hook = line.split("：")[-1].split('"')[-1].strip()
            elif "结尾" in line or "CTA" in line or "行动" in line:
                cta = line.split("：")[-1].split('"')[-1].strip()
            elif line and len(line) > 10:
                body += line + " "

        return {
            "hook": hook or "学会这个技能，你也可以做到！",
            "body": body.strip() or "这是一个非常实用的技巧",
            "cta": cta or "喜欢的话点个赞！",
            "full_script": f"{hook} {body} {cta}".strip(),
            "storyboard": storyboard,
        }

    def generate_platform_content(self, script_result: Dict, platform: str) -> Dict:
        """为指定平台生成适配的内容"""
        platform_configs = {
            "抖音": {
                "title_style": "悬念型",
                "desc_style": "引导互动型",
                "hashtag_style": "热门挑战型",
            },
            "小红书": {
                "title_style": "种草分享型",
                "desc_style": "经验分享型",
                "hashtag_style": "生活方式型",
            },
            "视频号": {
                "title_style": "新闻资讯型",
                "desc_style": "朋友圈风格",
                "hashtag_style": "正能量型",
            },
        }

        config = platform_configs.get(platform, platform_configs["抖音"])

        prompt = f"""基于以下短视频脚本，为{platform}平台生成标题和描述:

【脚本内容】
{script_result.get('full_script', '')}

【选题】
{script_result.get('topic_title', '')}

【平台特点】
- 平台: {platform}
- 标题风格: {config['title_style']}
- 描述风格: {config['desc_style']}

请生成:
1. 吸引人的标题(30字以内)
2. 引导互动的描述(200字以内)
3. 10个相关话题标签

输出JSON格式:
{{
  "platform_title": "标题",
  "platform_desc": "描述",
  "platform_hashtags": ["#标签1", "#标签2", ...]
}}

直接输出JSON:"""

        response = self._call_ollama(prompt, timeout=60)

        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass

        # 降级返回
        return {
            "platform_title": f"必看！{script_result.get('topic_title', '')}",
            "platform_desc": f"{script_result.get('hook', '')}\n\n{script_result.get('body', '')}",
            "platform_hashtags": [f"#{tag.strip()}" for tag in script_result.get('suggested_tags', [])[:10]],
        }

    def save_script_to_db(self, script_data: Dict) -> int:
        """保存脚本到数据库"""
        from core.db_init import init_topics_db

        conn = init_topics_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO scripts (topic_id, platform, script_content, storyboard, title, description, hashtags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            script_data.get("topic_id"),
            script_data.get("platform"),
            script_data.get("full_script", ""),
            json.dumps(script_data.get("storyboard", []), ensure_ascii=False),
            script_data.get("title", ""),
            script_data.get("description", ""),
            json.dumps(script_data.get("hashtags", []), ensure_ascii=False),
        ))

        script_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return script_id

    def batch_generate(self, topics: List[Dict], platform: str = "抖音",
                      duration: int = 30) -> List[Dict]:
        """批量生成脚本"""
        results = []

        for i, topic in enumerate(topics):
            print(f"  [{i+1}/{len(topics)}] 正在生成: {topic.get('title', '')[:30]}...")
            try:
                result = self.generate_script(topic, platform, duration)
                results.append(result)
                time.sleep(0.5)  # 避免请求过快
            except Exception as e:
                print(f"  生成失败: {str(e)}")
                continue

        return results


# ==================== 便捷函数 ====================
_module_instance = None

def get_script_module() -> ScriptModule:
    """获取脚本模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = ScriptModule()
    return _module_instance

def generate_script(topic: Dict, platform: str = "抖音",
                    duration: int = 30) -> Dict:
    """快速生成脚本"""
    return get_script_module().generate_script(topic, platform, duration)
