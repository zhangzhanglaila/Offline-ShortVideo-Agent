/**
 * llm.ts - LLM 驱动的内容生成
 *
 * 核心：generateScriptFromTopic → 真实 AI 生成（Claude API）
 * 规则版本作为 fallback
 */

import { ruleBasedScript, generateLayoutFromScript, generateVideoLayoutFromScript, preResolveAllImages } from "./generator";
import type { VideoScript } from "./generator";
import type { TimelineLayout, VideoLayout } from "@remotion/types";
import { buildDirector, type DirectorIntent } from "./director";

// ============================================================
// Anthropic Claude API 调用
// ============================================================

const ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages";
const CLAUDE_MODEL = "claude-sonnet-4-20250514";

interface ClaudeMessage {
  role: "user" | "assistant";
  content: string;
}

function buildScriptPrompt(topic: string): string {
  return `你是一个短视频爆款编剧，擅长写有感染力、有节奏感的短视频脚本。

请根据以下主题，生成一个适合抖音/短视频平台的内容脚本：

主题：${topic}

要求：
1. 开头必须有强钩子（一句话抓住注意力，让人想看下去）
2. 内容要具体、有干货，不要空话套话
3. 控制在3-5个核心步骤
4. 每个步骤要有"what" + "why"（做什么 + 为什么重要）
5. 结尾必须有行动引导（CTA），引导评论/关注/领取
6. 选合适的emoji图标

输出纯JSON格式（不要任何其他文字）：

{
  "hook": {
    "text": "钩子文案（15-30字，有冲击力）",
    "icon": "emoji",
    "color": "#hex颜色码"
  },
  "steps": [
    { "title": "步骤标题", "desc": "一句话说明", "icon": "emoji" },
    ...
  ],
  "cta": {
    "text": "行动引导文案",
    "icon": "emoji"
  }
}

颜色主题选择：
- 副业/赚钱类 → #FFD700（金色）
- AI/科技类 → #4EC9B0（青色）
- 英语/语言类 → #569CD6（蓝色）
- 健身/健康类 → #FF6B6B（红色）
- 学习/成长类 → #569CD6（蓝色）
- 创作/内容类 → #DCDCAA（黄色）
- 情感/恋爱类 → #FF6B9D（粉色）
- 职场/创业类 → #CE9178（橙色）

只输出JSON，不要任何解释。`;
}

function parseScriptResponse(raw: string): VideoScript | null {
  try {
    let jsonStr = raw.trim();
    if (jsonStr.startsWith("```json")) jsonStr = jsonStr.slice(7);
    else if (jsonStr.startsWith("```")) jsonStr = jsonStr.slice(3);
    if (jsonStr.endsWith("```")) jsonStr = jsonStr.slice(0, -3);
    jsonStr = jsonStr.trim();

    const parsed = JSON.parse(jsonStr);

    if (!parsed.hook?.text || !Array.isArray(parsed.steps) || !parsed.cta?.text) {
      return null;
    }

    return {
      hook: {
        text: String(parsed.hook.text).slice(0, 100),
        icon: String(parsed.hook.icon || "💡"),
        color: String(parsed.hook.color || "#4EC9B0"),
      },
      steps: parsed.steps.slice(0, 5).map((s: Record<string, unknown>) => ({
        title: String(s.title || "").slice(0, 50),
        desc: String(s.desc || "").slice(0, 80),
        icon: String(s.icon || "👉"),
      })),
      cta: {
        text: String(parsed.cta.text).slice(0, 60),
        icon: String(parsed.cta.icon || "👉"),
      },
      colorScheme: { primary: "#4EC9B0", fill: "rgba(78,201,176,0.15)", text: "#FFFFFF" },
    };
  } catch {
    return null;
  }
}

// ============================================================
// 异步脚本生成（LLM 优先，规则 fallback）
// ============================================================

export async function generateScriptFromTopic(topic: string): Promise<VideoScript> {
  const apiKey = process.env.ANTHROPIC_API_KEY;

  if (!apiKey) {
    console.info(`[llm] 无 ANTHROPIC_API_KEY，使用规则生成: "${topic}"`);
    return ruleBasedScript(topic);
  }

  try {
    console.info(`[llm] 调用 Claude API 生成脚本: "${topic}"`);

    const response = await fetch(ANTHROPIC_API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify({
        model: CLAUDE_MODEL,
        max_tokens: 1024,
        messages: [{ role: "user", content: buildScriptPrompt(topic) }],
      }),
    });

    if (!response.ok) {
      const errText = await response.text();
      console.error(`[llm] API 错误 ${response.status}:`, errText.slice(0, 200));
      return ruleBasedScript(topic);
    }

    const data = (await response.json()) as { content?: ClaudeMessage[] };
    const text = data.content?.[0]?.content ?? "";

    if (!text) {
      return ruleBasedScript(topic);
    }

    const script = parseScriptResponse(text);
    if (!script) {
      return ruleBasedScript(topic);
    }

    console.info(`[llm] 生成成功: hook="${script.hook.text.slice(0, 30)}...", steps=${script.steps.length}`);
    return script;
  } catch (err) {
    console.error("[llm] 调用失败:", err);
    return ruleBasedScript(topic);
  }
}

// ============================================================
// 异步 Layout 生成（LLM 脚本 → layout）
// ============================================================

export async function generateLayoutFromTopic(topic: string): Promise<TimelineLayout> {
  const script = await generateScriptFromTopic(topic);
  return generateLayoutFromScript(script);
}

// LLM 驱动 → VideoLayout（新元素系统）
export async function generateVideoLayoutFromTopic(topic: string): Promise<VideoLayout> {
  // Phase 0 (Orchestrator): 导演意图收集 — 所有决策的单一出口
  const script = await generateScriptFromTopic(topic);
  const director = buildDirector(topic, script); // ← 唯一的决策点

  // Phase 1 (async): 预解析所有图片资产
  const preResolved = await preResolveAllImages(
    script.topic ?? script.hook.text,
    script.steps.map(s => s.imageKeyword)
  );

  // Phase 2 (sync): 构建 VideoLayout（deterministic，无 async 依赖）
  // DirectorIntent 传给 layout builder，指导后续渲染决策
  return generateVideoLayoutFromScript(script, preResolved, director);
}
