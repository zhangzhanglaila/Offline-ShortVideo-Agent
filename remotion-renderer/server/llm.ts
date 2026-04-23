/**
 * llm.ts - LLM 驱动的内容生成
 *
 * 支持: DeepSeek (优先) / Anthropic (MiniMax relay) / 规则 fallback
 */

import { ruleBasedScript, generateLayoutFromScript, generateVideoLayoutFromScript, preResolveAllImages, enrichStep } from "./generator";
import type { VideoScript } from "./generator";
import type { TimelineLayout, VideoLayout } from "@remotion/types";
import { buildDirector, type DirectorIntent } from "./director";

// ============================================================
// API 配置 (支持 DeepSeek / Anthropic)
// ============================================================

function getLlmConfig() {
  // DeepSeek (OpenAI-compatible)
  if (process.env.OPENAI_API_KEY && process.env.OPENAI_API_BASE?.includes("deepseek")) {
    return {
      provider: "deepseek",
      url: `${process.env.OPENAI_API_BASE}/chat/completions`,
      model: process.env.OPENAI_API_MODEL || "deepseek-chat",
      apiKey: process.env.OPENAI_API_KEY,
    };
  }
  // Anthropic via MiniMax relay
  if (process.env.ANTHROPIC_API_KEY && process.env.ANTHROPIC_BASE_URL) {
    return {
      provider: "anthropic",
      url: `${process.env.ANTHROPIC_BASE_URL}/v1/messages`,
      model: "claude-sonnet-4-20250514",
      apiKey: process.env.ANTHROPIC_API_KEY,
    };
  }
  return null;
}

function buildScriptPrompt(topic: string): string {
  return `你是一个科普视频导演。你的任务是让一个完全不懂这个概念的人，通过你的视频真正理解它。

【主题】
${topic}

【硬性要求】
1. Hook：用一个生活中熟悉的例子或反直觉的事实开场，让人"啊原来是这样"
2. 核心解释：必须用一句话讲清楚本质，不能用术语解释术语
3. 类比：至少有一个生活中的类比（越日常越好）
4. 真实例子：至少一个大家知道的实际应用
5. CTA：引导思考或讨论，不要"关注我"这种营销话术（例如："想深入了解可以搜XXX"）
6. 每个step要同时回答"What" + "为什么这很重要"

【输出格式】（纯JSON，不要任何解释）
{
  "hook": { "text": "钩子（15-25字）", "icon": "emoji", "color": "#hex" },
  "steps": [
    { "title": "核心概念名", "desc": "一句话类比或解释", "icon": "emoji" },
    ...
  ],
  "cta": { "text": "引导思考的结尾语", "icon": "emoji" }
}

【颜色主题】
- AI/科技 → #4EC9B0（青色）
- 学习/认知 → #569CD6（蓝色）
- 健康/身体 → #FF6B6B（红色）
- 商业/经济 → #CE9178（橙色）
- 情感/心理 → #FF6B9D（粉色）

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
      steps: parsed.steps.slice(0, 5).map((s: Record<string, unknown>, i: number) =>
        enrichStep({
          title: String(s.title || "").slice(0, 50),
          desc: String(s.desc || "").slice(0, 80),
          icon: String(s.icon || "👉"),
        }, i)
      ),
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
  const cfg = getLlmConfig();

  if (!cfg) {
    console.info(`[llm] 无有效 LLM API 配置，使用规则生成: "${topic}"`);
    return ruleBasedScript(topic);
  }

  try {
    console.info(`[llm] 调用 ${cfg.provider} API 生成脚本: "${topic}"`);

    const prompt = buildScriptPrompt(topic);

    let text: string;
    if (cfg.provider === "deepseek") {
      // OpenAI-compatible format
      const response = await fetch(cfg.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${cfg.apiKey}`,
        },
        body: JSON.stringify({
          model: cfg.model,
          max_tokens: 1024,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!response.ok) {
        const errText = await response.text();
        console.error(`[llm] DeepSeek API 错误 ${response.status}:`, errText.slice(0, 200));
        return ruleBasedScript(topic);
      }
      const data = await response.json() as { choices?: { message?: { content?: string } }[] };
      text = data.choices?.[0]?.message?.content ?? "";
    } else {
      // Anthropic format
      const response = await fetch(cfg.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": cfg.apiKey,
          "anthropic-version": "2023-06-01",
          "anthropic-dangerous-direct-browser-access": "true",
        },
        body: JSON.stringify({
          model: cfg.model,
          max_tokens: 1024,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!response.ok) {
        const errText = await response.text();
        console.error(`[llm] Anthropic API 错误 ${response.status}:`, errText.slice(0, 200));
        return ruleBasedScript(topic);
      }
      const data = await response.json() as { content?: { content?: string }[] };
      text = data.content?.[0]?.content ?? "";
    }

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
