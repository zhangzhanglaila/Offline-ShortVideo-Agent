/**
 * generator.ts - V8 ImageSource Abstraction + Concept Graph Router
 *
 * 升级路径：
 * V5: topic → 关键词匹配 → 固定模板
 * V6: categoryRules 语义路由
 * V7: ConceptNode[] + extractConcepts() + fuseSceneQuery() + planSceneImage()
 * V8: ImageSource Provider 抽象层 + VisualStyle Token + Provider Router
 *
 * V7 核心：Concept Graph Image Router
 *   topic → extractConcepts() → ConceptNode[] → fuseSceneQuery(scene) → query
 *
 * V8 核心：ImageSource Abstraction Layer
 *   query → applyStyleToken(style) → ImageSourceRouter.resolve() → Provider.resolve()
 *   Provider: PexelsProvider(0) > UnsplashProvider(1) > PicsumProvider(2)
 */

// ============================================================
// V7: Concept Graph Image Router
// ============================================================

/**
 * 短视频场景类型（分镜级视觉规划）
 * - hook:       开场 hook（吸引眼球，情感冲击）
 * - problem:    冲突/痛点（让人感同身受）
 * - solution:   解决方案展示（方法/路径）
 * - steps:      具体步骤演示（清晰可执行）
 * - social:     社会证明（成功案例/他人验证）
 * - cta:        行动号召（推动关注/下一步）
 */
type SceneType = "hook" | "problem" | "solution" | "steps" | "social" | "cta";

/**
 * 概念节点（概念图的基本单元）
 * - id:           唯一标识
 * - keywords:     中文触发词（任意一个命中即激活该概念）
 * - enKeywords:   英文触发词（同上）
 * - sceneQueries: 各场景下的视觉语义 query（核心升级点）
 * - weight:       命中权重（用于多概念融合时排序）
 */
interface ConceptNode {
  id: string;
  keywords: string[];               // 中文触发词
  enKeywords: string[];              // 英文触发词
  sceneQueries: Record<SceneType, string>;  // 场景视觉 query
  weight: number;                    // 融合权重
}

/**
 * 图像规划结果（V7 核心输出）
 * - scene:   当前场景类型
 * - query:   融合后的语义 query
 * - concepts: 触发融合的概念 ID 列表
 */
interface ImagePlan {
  scene: SceneType;
  query: string;
  concepts: string[];
}

/**
 * 概念图谱（V7 核心数据结构）
 *
 * 每个概念节点包含该概念在「不同场景下」应有的视觉语义。
 * 例如 "money" 概念：
 * - hook:     "success achievement wealth celebration"
 * - problem: "financial stress debt struggle"
 * - solution:"investment growth passive income"
 * - steps:   "money business laptop strategy"
 *
 * 这实现了：从"关键词映射" → "场景感知语义融合"
 */
const conceptGraph: ConceptNode[] = [
  {
    id: "money",
    keywords: ["赚钱", "副业", "变现", "收入", "月入", "财务", "财富"],
    enKeywords: ["money", "earn", "income", "profit", "wealth", "rich"],
    sceneQueries: {
      hook:     "luxury lifestyle success achievement",
      problem:  "financial stress debt struggle anxiety",
      solution: "side hustle laptop coffee shop passive income",
      steps:    "money business laptop strategy growth",
      social:   "bank account growth celebration success",
      cta:      "start earning today financial freedom",
    },
    weight: 1,
  },
  {
    id: "ai",
    keywords: ["AI", "人工智能", "GPT", "ChatGPT", "机器学习", "AI工具"],
    enKeywords: ["AI", "artificial intelligence", "GPT", "machine learning", "chatgpt"],
    sceneQueries: {
      hook:     "futuristic technology AI robot circuit",
      problem:  "overwhelmed by technology confusion learning",
      solution: "AI automation dashboard interface futuristic",
      steps:    "AI tools software laptop productivity automation",
      social:   "AI startup technology innovation success",
      cta:      "start using AI today smart assistant",
    },
    weight: 1,
  },
  {
    id: "english",
    keywords: ["英语", "口语", "语言", "学英语", "英语学习"],
    enKeywords: ["english", "language", "speaking", "IELTS", "TOEFL"],
    sceneQueries: {
      hook:     "confident speaker multilingual success global",
      problem:  "language barrier frustration communicate struggle",
      solution: "english study book coffee shop conversation",
      steps:    "english learning laptop course study vocabulary",
      social:   "fluent speaker confident international travel",
      cta:      "start speaking english today language app",
    },
    weight: 1,
  },
  {
    id: "study",
    keywords: ["学习", "知识", "读书", "课程", "自我提升"],
    enKeywords: ["study", "learn", "knowledge", "book", "course", "education"],
    sceneQueries: {
      hook:     "library books knowledge wisdom inspiration",
      problem:  "overwhelmed information overload confusion study",
      solution: "organized desk notebook laptop study space",
      steps:    "study desk laptop notes focus concentration",
      social:   "graduation cap success certificate achievement",
      cta:      "start learning today online course",
    },
    weight: 1,
  },
  {
    id: "fitness",
    keywords: ["健身", "减脂", "减肥", "运动", "体能", "健康"],
    enKeywords: ["fitness", "gym", "workout", "health", "exercise", "diet", "weight loss"],
    sceneQueries: {
      hook:     "fit athletic body transformation motivation",
      problem:  "overweight tired lack energy motivation struggle",
      solution: "gym workout fitness training healthy lifestyle",
      steps:    "fitness gym weights exercise training cardio",
      social:   "body transformation before after fitness success",
      cta:      "start your fitness journey today gym",
    },
    weight: 1,
  },
  {
    id: "startup",
    keywords: ["创业", "职场", "工作", "事业", "晋升", "加薪"],
    enKeywords: ["startup", "business", "career", "job", "office", "promotion", "entrepreneur"],
    sceneQueries: {
      hook:     "startup office team success celebration",
      problem:  "office stress bored meeting frustration career",
      solution: "startup office laptop team meeting success",
      steps:    "business laptop strategy meeting presentation",
      social:   "business success promotion achievement team",
      cta:      "level up your career today professional",
    },
    weight: 1,
  },
  {
    id: "creative",
    keywords: ["创作", "自媒体", "内容", "写作", "博主", "短视频"],
    enKeywords: ["creative", "content", "creator", "youtube", "blog", "writing", "自媒体"],
    sceneQueries: {
      hook:     "creative artist studio colorful creative workspace",
      problem:  "creative block no ideas no inspiration struggle",
      solution: "content creator laptop camera studio creative",
      steps:    "creative laptop camera content filming setup",
      social:   "youtube subscriber milestone creative success",
      cta:      "start creating content today creator economy",
    },
    weight: 1,
  },
  {
    id: "relationship",
    keywords: ["恋爱", "感情", "关系", "情感", "脱单", "婚姻"],
    enKeywords: ["love", "relationship", "couple", "dating", "romance", "marriage"],
    sceneQueries: {
      hook:     "couple romantic sunset beach love happiness",
      problem:  "lonely sad single relationship struggle",
      solution: "happy couple communication love relationship",
      steps:    "dating tips romantic setup relationship advice",
      social:   "happy couple wedding love success story",
      cta:      "find your love today dating app",
    },
    weight: 1,
  },
  {
    id: "student",
    keywords: ["大学生", "校园", "毕业", "学生", "开学"],
    enKeywords: ["student", "university", "campus", "college", "graduation", "school"],
    sceneQueries: {
      hook:     "university campus student graduation success",
      problem:  "student stress exam pressure future anxiety",
      solution: "student laptop library study campus life",
      steps:    "student desk study laptop notes university",
      social:   "graduation ceremony success career achievement",
      cta:      "start your student journey today campus",
    },
    weight: 1,
  },
  {
    id: "health",
    keywords: ["健康", "养生", "睡眠", "饮食", "营养", "冥想"],
    enKeywords: ["health", "wellness", "sleep", "nutrition", "meditation", "diet"],
    sceneQueries: {
      hook:     "healthy lifestyle wellness vibrant energy",
      problem:  "tired exhausted burnout health issues sleep",
      solution: "healthy food salad nutrition wellness lifestyle",
      steps:    "healthy cooking nutrition diet wellness routine",
      social:   "health transformation wellness success energy",
      cta:      "start your wellness journey today health",
    },
    weight: 1,
  },
];

/**
 * 概念提取：从 topic 字符串中提取所有匹配的概念节点
 *
 * 复合语义融合（多概念同时命中）：
 * "大学生AI副业赚钱"
 * → student(2字命中) + ai(1字命中) + money(2字命中)
 * → [student, ai, money] 三个概念同时参与 query 融合
 *
 * @param topic 原始主题字符串
 * @returns 按权重排序的概念节点列表
 */
function extractConcepts(topic: string): ConceptNode[] {
  const cn = topic.replace(/[^\u4e00-\u9fa5]/g, "");
  const enPart = topic.replace(/[\u4e00-\u9fa5]/g, " ").trim();
  const enWords = enPart.split(/\s+/).filter(Boolean);

  // 收集所有命中的概念及其命中强度（命中词长度之和）
  const matched: Array<{ node: ConceptNode; score: number }> = [];

  for (const node of conceptGraph) {
    let score = 0;
    for (const kw of node.keywords) {
      if (cn.includes(kw)) {
        score += kw.length; // 长词优先
      }
    }
    for (const ekw of node.enKeywords) {
      if (enWords.some(w => w.toLowerCase().includes(ekw.toLowerCase()))) {
        score += ekw.length;
      }
    }
    if (score > 0) {
      matched.push({ node, score });
    }
  }

  // 按权重 × 命中分数排序，高分概念优先
  matched.sort((a, b) => (b.score * b.node.weight) - (a.score * a.node.weight));
  return matched.map(m => m.node);
}

/**
 * 融合多概念的 scene query
 *
 * 单概念 → 直接用该概念的 sceneQuery
 * 多概念 → 取权重最高的3个概念的 sceneQuery 词汇融合
 *
 * 融合策略：按空格拼接，语义相近的词自然组合
 * "student ai money" → "student laptop AI side hustle business money"
 */
function fuseSceneQuery(concepts: ConceptNode[], scene: SceneType): string {
  if (concepts.length === 0) {
    return "business success professional"; // 默认兜底 query
  }
  if (concepts.length === 1) {
    return concepts[0].sceneQueries[scene];
  }
  // 多概念融合：取 top-3 概念的 sceneQuery 词汇
  const topConcepts = concepts.slice(0, 3);
  const queryParts = topConcepts.map(c => c.sceneQueries[scene]);
  // 融合：去重后拼接
  const allWords = queryParts.flatMap(q => q.split(/\s+/));
  const seen = new Set<string>();
  const unique: string[] = [];
  for (const w of allWords) {
    const lower = w.toLowerCase();
    if (!seen.has(lower)) {
      seen.add(lower);
      unique.push(w);
    }
  }
  return unique.join(" ");
}

/**
 * 根据 topic 和场景类型，生成分镜级图像规划
 *
 * @param topic     视频主题（如 "大学生AI副业赚钱"）
 * @param scene     当前场景类型
 * @returns ImagePlan{scene, query, url, concepts}
 */
function planSceneImage(topic: string, scene: SceneType): Omit<ImagePlan, 'url'> & { query: string; concepts: string[] } {
  const concepts = extractConcepts(topic);
  const query = fuseSceneQuery(concepts, scene);
  return {
    scene,
    query,
    concepts: concepts.map(c => c.id),
  };
}

// ============================================================
// V8: ImageSource Abstraction Layer
// ============================================================

/**
 * 视觉风格 Token（V8 升级：统一视觉语言）
 *
 * 短视频分镜需要一致的视觉风格，使用 style bias 让所有 query
 * 都携带统一的视觉氛围关键词，而不是随机风格。
 *
 * 使用方式：
 *   const styled = applyStyleToken(query, "cinematic");
 *   → "money business laptop cinematic dark moody professional"
 */
type VisualStyle = "cinematic" | "minimalist" | "tech" | "warm" | "bold";

const STYLE_TOKENS: Record<VisualStyle, string> = {
  // 电影感：深色调 + 戏剧光 + 电影感构图
  cinematic:  "cinematic dark moody dramatic lighting professional",
  // 极简：干净留白 + 简洁排版
  minimalist:  "minimal clean white simple modern",
  // 科技感：蓝紫色 + 数字元素 + 现代
  tech:        "tech futuristic blue purple modern digital",
  // 暖色调：金色 + 自然光 + 活力
  warm:        "warm golden natural sunlight vibrant",
  // 强对比：饱和色彩 + 高对比 + 冲击力
  bold:        "bold vibrant high contrast colorful striking",
};

/**
 * 将 style bias 应用到 query 上
 */
function applyStyleToken(query: string, style: VisualStyle = "cinematic"): string {
  // 从 concept graph 来的 query 已经是语义丰富的词组
  // style token 在末尾追加风格约束词
  return `${query} ${STYLE_TOKENS[style]}`;
}



/**
 * Canonical Concept（语义规范化层 — V9 关键升级）
 *
 * 问题：LLM 生成 "city night street" / "night city street" / "urban night street"
 *       三个完全不同的 raw keyword，但视觉需求一样
 *
 * 解决：
 *   raw keyword → normalizeConcept() → CanonicalConcept{id, tags}
 *   → cacheKey = hash(canonicalId + scene + style)
 *
 * 效果：语义等价的 keyword 共享同一 cache entry，命中率大幅提升
 */
interface CanonicalConcept {
  /** 规范 ID（字母数字下划线，语义稳定） */
  id: string;
  /** 语义标签（用于 debug 和扩展） */
  tags: string[];
}

/**
 * 语义等价词表（同义词 → 同一 canonical tag）
 */
const SYNONYM_MAP: Record<string, string> = {
  // 城市/建筑
  city: "urban", urban: "urban",
  street: "street", avenue: "street", road: "street",
  downtown: "urban_center", center: "urban_center",
  skyline: "cityscape", cityscape: "cityscape",
  building: "architecture", buildings: "architecture",
  // 自然/天空
  night: "night", evening: "night", dark: "night",
  sky: "sky", clouds: "sky",
  sunrise: "dawn", sunset: "dusk", dawn: "dawn", dusk: "dusk",
  moon: "night_sky",
  // 灯光/氛围
  lights: "lighting", light: "lighting", glow: "lighting",
  neon: "neon_lights", neon_light: "neon_lights",
  // 人物/情感
  people: "people", person: "people", crowd: "people",
  happy: "positive_emotion", joy: "positive_emotion", smile: "positive_emotion",
  sad: "negative_emotion", tired: "negative_emotion", stress: "negative_emotion",
  // 颜色/风格
  colorful: "vibrant_colors", vibrant: "vibrant_colors",
  dark_moody: "dark_moody", moody_dark: "dark_moody", dramatic: "dark_moody",
  // 时间
  morning: "daytime", afternoon: "daytime", day: "daytime",
  // 科技/数字
  tech: "technology", digital: "technology", computer: "technology",
  // 金钱/商业
  money: "money", cash: "money", dollar: "money",
  business: "business", office: "business",
};

/**
 * 将 raw keyword 转换为 CanonicalConcept
 *
 * 算法：
 * 1. 提取英文 token（去除中文和符号）
 * 2. 去除 stop words
 * 3. 标准化同义词（查 SYNONYM_MAP）
 * 4. 排序后 join → canonical id
 *
 * @example
 *   normalizeConcept("city night street lights")
 *   → { id: "lighting_night_street_urban", tags: ["lighting", "night", "street", "urban"] }
 */
function normalizeConcept(keyword: string): CanonicalConcept {
  // 提取英文 token（保留中文作为独立语义标签）
  const enPart = keyword.replace(/[一-龥]/g, " ");
  const tokens = enPart
    .toLowerCase()
    .replace(/[^wa-z]/g, " ")
    .split(/s+/)
    .filter(t => t.length > 1);

  const STOP_WORDS = new Set([
    "the", "and", "for", "with", "from", "this", "that", "these", "those",
    "beautiful", "amazing", "awesome", "nice", "good", "new", "old", "big", "small",
  ]);

  const filtered = tokens.filter(t => !STOP_WORDS.has(t));
  // 同义词标准化
  const normalized = filtered.map(t => SYNONYM_MAP[t] ?? t);
  // 排序保证同一语义集合 → 同一 id
  const sorted = [...new Set(normalized)].sort();
  const id = sorted.join("_") || "default";

  return { id, tags: sorted };
}


/**
 * 生成 deterministic cache key（基于规范概念 ID）
 *
 * V9: cacheKey = hash(canonicalConceptId + scene + style)
 * 使用 normalizeConcept 确保语义等价的 keyword 映射到同一 cacheKey
 */
function buildCacheKey(keyword: string, scene: SceneType, style: VisualStyle = "cinematic"): string {
  const concept = normalizeConcept(keyword);
  const raw = `${concept.id}::${scene}::${style}`;
  // Simple deterministic hash using built-in approach
  let hash = 0;
  for (let i = 0; i < raw.length; i++) {
    const char = raw.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32bit integer
  }
  return Math.abs(hash).toString(36);
}

/**
 * 内存图片缓存（V8 生产级）
 *
 * Phase 0: cache lookup（O(1)）
 * Phase 1: async fetch（仅 cache miss）
 * Phase 2: store resolved asset
 *
 * 特点：
 * - deterministic: 相同 cacheKey 永远返回相同 asset
 * - in-memory: 进程内共享，同一 video 生成内去重
 * - production 扩展：可替换为 Redis / file system cache
 */
class SimpleImageCache {
  private map = new Map<string, ImageAsset>();

  get(key: string): ImageAsset | undefined {
    return this.map.get(key);
  }

  set(key: string, asset: ImageAsset): void {
    this.map.set(key, asset);
  }

  has(key: string): boolean {
    return this.map.has(key);
  }
}

/** 全局单例 cache（per server process） */
const globalImageCache = new SimpleImageCache();

/**
 * In-flight 请求去重 Map（V9 关键升级）
 *
 * 问题：同一 batch 内多个 step 可能同时请求相同语义图片
 *       "city night" + "city night street" + "city night lights"
 *       → 并发 3 次 Pexels API（浪费 + 触发 rate limit）
 *
 * 解决：相同 canonicalId + scene + style 的请求，共享同一个 in-flight Promise
 *       第一个请求发起 API，后续请求等待同一个 Promise
 *
 * 效果：零额外 API 调用，零 rate limit 风险
 */
const inFlightPromises = new Map<string, Promise<ImageAsset>>();

/**
 * ImageSource Provider 接口
 *
 * 所有图片源都实现这个统一接口，router 通过它调用，
 * 而不直接知道背后用的是哪个 provider。
 *
 * V8 核心抽象：图片获取方式对上层完全透明
 */

/**
 * 图片资产（V8 生产级）
 *
 * type 说明：
 * - cdn:        CDN 直连 asset（Pexels images.pexels.com）
 * - proxy:       需经后端 proxy 转发（Unsplash 官方 API）
 * - deterministic: 确定性噪声图片，可直连（picsum）
 */
interface ImageAsset {
  /** 最终可用于 <img src> 的 URL */
  url: string;
  /** 资产类型（决定加载策略） */
  type: "cdn" | "proxy" | "deterministic";
  /** 来源 provider */
  provider: string;
  /** Provider 原生 ID（如 Pexels photo.id） */
  id?: string;
  /** 缓存 key：hash(canonicalConceptId + scene + style)，用于 cache lookup */
  cacheKey: string;
  /** 规范化后的概念 ID（语义稳定性） */
  conceptId: string;
  /** 渲染提示（告诉 Remotion 层如何处理这张图） */
  renderHints?: {
    /** 图片填充模式 */
    fit?: "cover" | "contain";
    /** 焦点区域（影响 contain 模式下的裁剪参考） */
    focus?: "center" | "face" | "auto";
    /** 亮度补偿（叠加在图上，-1~1） */
    brightnessBoost?: number;
    /** 是否背景虚化（产生景深效果） */
    blurBackground?: boolean;
  };
}

interface ImageSourceProvider {
  /** provider 名称（用于日志和调试） */
  name: string;
  /** 优先级（数字越小越高），0 = 最高优先 */
  priority: number;
  /** 用 query 解析图片 URL，返回 null 表示该 provider 无法处理 */
  resolve(query: string, width?: number, height?: number): Promise<ImageAsset | null>;
}

/**
 * Pexels Provider（生产环境推荐）
 *
 * Pexels vs Unsplash：
 * - Pexels：视频友好 / 商业授权 / 风格统一 / 搜索结果稳定
 * - Unsplash：艺术感强 / 随机性大 / 视频兼容性一般
 *
 * 使用条件：PEXELS_API_KEY 环境变量
 * 申请地址：https://www.pexels.com/api/
 */
class PexelsProvider implements ImageSourceProvider {
  name = "pexels";
  priority = 0;

  constructor(private apiKey: string) {}

  async resolve(query: string, width = 540, height = 960): Promise<ImageAsset | null> {
    if (!this.apiKey) return null;
    try {
      const encoded = encodeURIComponent(query);
      const res = await fetch(
        `https://api.pexels.com/v1/search?query=${encoded}&per_page=1&orientation=portrait`,
        { headers: { Authorization: this.apiKey } }
      );
      if (!res.ok) {
        console.warn(`[ImageSource] Pexels API ${res.status} for query "${query}"`);
        return null;
      }
      const data = await res.json() as { photos: Array<{ id: number }> };
      if (!data.photos?.length) {
        console.warn(`[ImageSource] Pexels no results for "${query}"`);
        return null;
      }
      const id = data.photos[0].id;
      const rawUrl = `https://images.pexels.com/photos/${id}/pexels-photo-${id}.jpeg?auto=compress&cs=tinysrgb&fit=crop&h=${height}&w=${width}`;
      // V9: cacheKey uses canonical concept (via buildCacheKey which now calls normalizeConcept)
      // conceptId injected by preResolveAllImages.resolveOne wrapper
      return { id: String(id), provider: "pexels", url: rawUrl, type: "cdn" as const, cacheKey: "", conceptId: "" };
    } catch (err) {
      console.warn(`[ImageSource] Pexels failed for "${query}":`, err);
      return null;
    }
  }
}

/**
 * Unsplash Provider（开发/预览环境）
 *
 * 官方 API（需 key）：https://api.unsplash.com/photos/random
 * 免费 CDN（无需 key，不稳定）：source.unsplash.com
 *
 * 注意：source.unsplash.com 已停止服务！
 * 应使用官方 API + 自建 proxy，或直接用 Pexels 替代
 */
class UnsplashProvider implements ImageSourceProvider {
  name = "unsplash";
  priority = 1;

  constructor(private apiKey?: string) {}

  resolve(query: string, width = 540, height = 960): Promise<ImageAsset | null> {
    const q = encodeURIComponent(query);
    if (this.apiKey) {
      // 需自建 server-side proxy（避免 CORS + key 暴露）
      const rawUrl = `/proxy/unsplash?query=${q}&w=${width}&h=${height}`;
      const cacheKey = buildCacheKey(q, "steps", "cinematic");
      return Promise.resolve({ provider: "unsplash", url: rawUrl, type: "proxy" as const, cacheKey: "", conceptId: "" });
    }
    console.warn("[ImageSource] source.unsplash.com 已停止，production 请配 PEXELS_API_KEY");
    const rawUrl = `https://source.unsplash.com/${width}x${height}/?${q}`;
    return Promise.resolve({ provider: "source-unsplash", url: rawUrl, type: "deterministic" as const, cacheKey: "", conceptId: "" });
  }
}

/**
 * Picsum Provider（debug / offline / deterministic preview）
 *
 * 特点：
 * - 确定性：seed 相同 → 图片相同（适合预览/测试）
 * - 噪声大：不保证语义相关性（只适合 fallback）
 * - 永不挂：picsum.photos 极其稳定
 *
 * 用途：debug 模式 / CI 测试 / Pexels/Unsplash 均不可用时的最后兜底
 */
class PicsumProvider implements ImageSourceProvider {
  name = "picsum";
  priority = 2; // 最低优先，只作 fallback

  resolve(query: string, width = 540, height = 960): Promise<ImageAsset | null> {
    const seed = query.slice(0, 30);
    const rawUrl = `https://picsum.photos/seed/${encodeURIComponent(seed)}/${width}/${height}`;
    return Promise.resolve({ provider: "picsum", url: rawUrl, type: "deterministic" as const, cacheKey: "", conceptId: "" });
  }
}

/**
 * 图片源路由器（V8 核心）
 *
 * 职责：
 * 1. 管理所有 ImageSourceProvider（按 priority 排序）
 * 2. 依次尝试各 provider，直到某个返回非 null URL
 * 3. 对上层（planSceneImage）完全屏蔽 provider 选择逻辑
 *
 * 使用方式：
 *   const router = createImageSourceRouter();
 *   const url = router.resolve("money business laptop AI", 540, 960);
 */
class ImageSourceRouter {
  private providers: ImageSourceProvider[];

  constructor(providers: ImageSourceProvider[]) {
    // 按 priority 升序排列（priority 小的先尝试）
    this.providers = [...providers].sort((a, b) => a.priority - b.priority);
  }

  /**
   * 依次异步尝试各 provider，返回第一个有效 URL
   * Pexels(0) 和 Unsplash(1) 都失败后，最后尝试 Picsum(2) 作为绝对兜底
   *
   * @param query           语义 query（经过 concept normalization）
   * @param width           输出宽度
   * @param height          输出高度
   * @param forceProviderId  强制使用指定 provider（用于测试/debug）
   */
  async resolve(
    query: string,
    width = 540,
    height = 960,
    forceProviderId?: string
  ): Promise<ImageAsset> {
    const providersToTry = forceProviderId
      ? this.providers.filter(p => p.name === forceProviderId)
      : this.providers;

    for (const provider of providersToTry) {
      if (provider.priority === 2) continue;
      const asset = await provider.resolve(query, width, height);
      if (asset) {
        console.info(`[ImageSource] ${provider.name}: "${query}" → ${asset.url.slice(0, 80)}`);
        return asset;
      }
    }
    console.info(`[ImageSource] picsum fallback: "${query}"`);
    const fallback = await new PicsumProvider().resolve(query, width, height);
    return fallback!;
  }

  /** 返回当前可用 provider 列表（用于调试） */
  availableProviders(): string[] {
    return this.providers.map(p => p.name);
  }
}

/**
 * 创建 ImageSource Router（工厂函数）
 *
 * Provider 优先级顺序：
 * 1. Pexels  （生产环境首选，需 PEXELS_API_KEY）
 * 2. Unsplash（预览/开发，需 ANTHROPIC_API_KEY 同目录申请）
 * 3. Picsum  （debug fallback， deterministic）
 *
 * 环境变量：
 *   PEXELS_API_KEY    — Pexels API key（推荐生产使用）
 *   UNSPLASH_API_KEY  — Unsplash API key（可选）
 */
function createImageSourceRouter(): ImageSourceRouter {
  const providers: ImageSourceProvider[] = [];

  const pexelsKey = process.env.PEXELS_API_KEY;
  if (pexelsKey) {
    providers.push(new PexelsProvider(pexelsKey));
  }

  const unsplashKey = process.env.UNSPLASH_API_KEY;
  providers.push(new UnsplashProvider(unsplashKey));

  // Picsum 始终注册为最低优先级兜底
  providers.push(new PicsumProvider());

  return new ImageSourceRouter(providers);
}

/** 全局单例 router（延迟初始化） */
let _router: ImageSourceRouter | null = null;

function getImageSourceRouter(): ImageSourceRouter {
  if (!_router) {
    _router = createImageSourceRouter();
    console.info(`[ImageSource] initialized: ${_router.availableProviders().join(" > ")}`);
  }
  return _router;
}

async function resolveWithStyle(
  query: string,
  style: VisualStyle = "cinematic",
  width = 540,
  height = 960
): Promise<string> {
  const styledQuery = applyStyleToken(query, style);
  return getImageSourceRouter().resolve(styledQuery, width, height).then(a => a.url);
}

/**
 * 顶层兼容接口：keyword → 图片 URL（V8 版本）
 *
 * 保持向后兼容，内部走完整的 ImageSource 路由链路：
 *   query → applyStyleToken() → ImageSourceRouter.resolve() → provider.resolve()
 */
async function buildImageUrl(strategy: { type: "unsplash" | "picsum"; query: string }): Promise<string> {
  const { query } = strategy;
  if (strategy.type === "picsum") {
    return resolveWithStyle(query, "bold");
  }
  return resolveWithStyle(query, "cinematic");
}

/**
 * 顶层兼容接口：keyword → 图片 URL
 *
 * 保持向后兼容，内部委托给 planSceneImage(scene="steps")
 * 新代码应直接使用 planSceneImage(topic, scene) 以获得场景感知能力
 */
// Deprecated: use preResolveAllImages + generateVideoLayoutFromScript instead
function keywordToImage(_keyword: string): string {
  return ''; // URLs are now pre-resolved via preResolveAllImages
}

import type { TimelineLayout, BoxData, ArrowData } from "@remotion/types";
import type { VideoLayout } from "@remotion/types";
import type { DirectorIntent, SubtitleCue, WordCue } from "./director";

// ============================================================
// 短视频脚本结构（真正的 Agent 输出）
// ============================================================
export type StepLayoutType = "full-image" | "split" | "cinematic" | "text-only";

export interface VideoScript {
  topic?: string;       // 原始主题（用于 scene-aware 图像规划，可由 hook.text 推导）
  hook: {
    text: string;       // 钩子文案
    icon: string;       // 配图emoji
    color: string;      // 强调色
  };
  steps: Array<{
    title: string;      // 步骤标题
    desc: string;       // 1句话说明
    icon: string;      // emoji
    image?: string;     // 可选配图URL（已废弃，用 imageKeyword）
    imageKeyword: string; // Unsplash 搜索关键词
    layoutType: StepLayoutType; // LLM 决定版式
  }>;
  cta: {
    text: string;       // 行动引导
    icon: string;
  };
  colorScheme: {
    primary: string;
    fill: string;
    text: string;
  };
}

// ============================================================
// 图标/颜色映射（规则层，但为脚本服务）
// ============================================================
const ICON_MAP: Record<string, string> = {
  money: "💰", wealth: "💎", income: "💵", profit: "📈", business: "🏢",
  freelance: "💼", startup: "🚀", marketing: "📢", sales: "🤝",
  ai: "🤖", tech: "⚡", coding: "💻", data: "📊", automation: "🔧",
  gpt: "🧠", chatgpt: "💬", midjourney: "🎨", stable: "✨",
  learn: "📚", course: "🎓", book: "📖", study: "✏️", growth: "📈",
  skill: "🎯", knowledge: "🧩", practice: "🔁",
  content: "✍️", writing: "📝", video: "🎬", thumbnail: "🖼️",
  twitter: "🐦", youtube: "▶️", tiktok: "🎵", blog: "📝", podcast: "🎙️",
  life: "🌱", health: "💪", fitness: "🏃", mindset: "🧠",
  habit: "✅", productivity: "⚡", time: "⏰",
  relationship: "💕", network: "🌐", community: "👥", social: "🔗",
  language: "🗣️", english: "🇬🇧", chinese: "🇨🇳", japanese: "🇯🇵",
  body: "🏃", diet: "🥗", sleep: "😴", mental: "🧘",
  student: "🎓", campus: "🏫", exam: "📝", parttime: "💼",
  default: "👉",
};

function matchIcon(text: string): string {
  const lower = text.toLowerCase();
  for (const [key, icon] of Object.entries(ICON_MAP)) {
    if (lower.includes(key)) return icon;
  }
  return ICON_MAP.default;
}

// ============================================================
// 脚本生成层（Agent 核心）
// 不再是固定模板，而是按 topic 生成不同结构
// ============================================================

function inferColorScheme(topic: string, steps: { title: string }[]): {
  primary: string; fill: string; text: string; hook: string;
} {
  const lower = topic.toLowerCase();

  if (lower.includes("副业") || lower.includes("赚钱") || lower.includes("变现") || lower.includes("收入")) {
    return { primary: "#FFD700", fill: "rgba(255,215,0,0.15)", text: "#FFD700", hook: "#FF6B6B" };
  }
  if (lower.includes("ai") || lower.includes("人工智能") || lower.includes("gpt") || lower.includes("chat")) {
    return { primary: "#4EC9B0", fill: "rgba(78,201,176,0.15)", text: "#4EC9B0", hook: "#CE9178" };
  }
  if (lower.includes("英语") || lower.includes("language") || lower.includes("口语") || lower.includes("speaking")) {
    return { primary: "#569CD6", fill: "rgba(86,156,214,0.15)", text: "#FFFFFF", hook: "#DCDCAA" };
  }
  if (lower.includes("健身") || lower.includes("减脂") || lower.includes("减肥") || lower.includes("body") || lower.includes("fitness")) {
    return { primary: "#FF6B6B", fill: "rgba(255,107,107,0.15)", text: "#FF6B6B", hook: "#4EC9B0" };
  }
  if (lower.includes("学习") || lower.includes("成长") || lower.includes("提升") || lower.includes("知识")) {
    return { primary: "#569CD6", fill: "rgba(86,156,214,0.15)", text: "#FFFFFF", hook: "#DCDCAA" };
  }
  if (lower.includes("创业") || lower.includes("职场") || lower.includes("工作")) {
    return { primary: "#CE9178", fill: "rgba(206,145,120,0.15)", text: "#CE9178", hook: "#4EC9B0" };
  }
  if (lower.includes("创作") || lower.includes("内容") || lower.includes("写作") || lower.includes("自媒体")) {
    return { primary: "#DCDCAA", fill: "rgba(220,220,170,0.15)", text: "#DCDCAA", hook: "#CE9178" };
  }
  if (lower.includes("大学生") || lower.includes("student") || lower.includes("校园")) {
    return { primary: "#4EC9B0", fill: "rgba(78,201,176,0.15)", text: "#FFFFFF", hook: "#DCDCAA" };
  }
  if (lower.includes("恋爱") || lower.includes("感情") || lower.includes("关系") || lower.includes("relationship")) {
    return { primary: "#FF6B9D", fill: "rgba(255,107,157,0.15)", text: "#FF6B9D", hook: "#FFD700" };
  }
  return { primary: "#4EC9B0", fill: "rgba(78,201,176,0.12)", text: "#FFFFFF", hook: "#CE9178" };
}

// 核心：真正的脚本生成（不再是固定模板）
// 注意：此为规则版本，LLM版本在 llm.ts
export function ruleBasedScript(topic: string): VideoScript {
  const lower = topic.toLowerCase();

  // === 副业赚钱类 ===
  if (lower.includes("副业") || lower.includes("赚钱") || lower.includes("变现")) {
    return {
      hook: {
        text: `普通人如何在 ${topic.replace(/[^a-zA-Z\u4e00-\u9fa5]/g, "")} 里赚到第一桶金？`,
        icon: "💰",
        color: "#FFD700",
      },
      steps: [
        enrichStep({ title: "锁定一个高需求方向", desc: "别什么都做，专注一个能持续变现的领域", icon: "🎯" }, 0),
        enrichStep({ title: "用AI放大效率", desc: "一个人顶一个团队，成本几乎为零", icon: "🤖" }, 1),
        enrichStep({ title: "快速拿到第一笔收入", desc: "小闭环验证，48小时内出第一单", icon: "💵" }, 2),
        enrichStep({ title: "复制放大，月入过万", desc: "找到可复制的路径，持续放大", icon: "📈" }, 3),
      ],
      cta: { text: "评论区扣'赚钱'，送你完整攻略", icon: "🔥" },
      colorScheme: inferColorScheme(topic, []),
    };
  }

  // === AI / GPT 类 ===
  if (lower.includes("ai") || lower.includes("人工智能") || lower.includes("gpt") || lower.includes("chat")) {
    return {
      hook: {
        text: `用AI做${topic.replace(/AI|人工智能/g, "")}，效率提升10倍的方法`,
        icon: "🤖",
        color: "#4EC9B0",
      },
      steps: [
        enrichStep({ title: "AI能做什么", desc: "了解AI的核心能力和边界", icon: "🧠" }, 0),
        enrichStep({ title: "选对工具", desc: "GPT Midjourney Stable 各有所长", icon: "⚙️" }, 1),
        enrichStep({ title: "实战操作", desc: "手把手演示完整工作流", icon: "💻" }, 2),
        enrichStep({ title: "立刻变现", desc: "用AI接单或做产品赚钱", icon: "💰" }, 3),
      ],
      cta: { text: "关注我，持续分享AI搞钱干货", icon: "👉" },
      colorScheme: inferColorScheme(topic, []),
    };
  }

  // === 英语/语言学习类 ===
  if (lower.includes("英语") || lower.includes("language") || lower.includes("口语") || lower.includes("speaking")) {
    return {
      hook: {
        text: `学 ${topic.replace(/[^a-zA-Z\u4e00-\u9fa5]/g, "")} ，90%的人在第一步就错了`,
        icon: "🗣️",
        color: "#569CD6",
      },
      steps: [
        enrichStep({ title: "打破心理障碍", desc: "敢说比说对更重要", icon: "💪" }, 0),
        enrichStep({ title: "核心句型思维", desc: "不背单词，背高频句型", icon: "🧠" }, 1),
        enrichStep({ title: "沉浸式练习", desc: "每天30分钟胜过背1小时", icon: "⏰" }, 2),
        enrichStep({ title: "实战输出", desc: "找语伴或AI对话练习", icon: "🤝" }, 3),
      ],
      cta: { text: "需要口语纠音，评论区找我", icon: "🇬🇧" },
      colorScheme: inferColorScheme(topic, []),
    };
  }

  // === 健身/减脂类 ===
  if (lower.includes("健身") || lower.includes("减脂") || lower.includes("减肥") || lower.includes("body")) {
    return {
      hook: {
        text: `${topic}，做好这4点，少走3年弯路`,
        icon: "🏃",
        color: "#FF6B6B",
      },
      steps: [
        enrichStep({ title: "饮食三分练七分吃", desc: "不吃对，练死也没用", icon: "🥗" }, 0),
        enrichStep({ title: "力量训练优先", desc: "增肌才能持续燃脂", icon: "💪" }, 1),
        enrichStep({ title: "睡眠决定效果", desc: "睡不好，激素乱，脂肪堆", icon: "😴" }, 2),
        enrichStep({ title: "坚持比强度重要", desc: "每天30分钟，胜过一周猛练2小时", icon: "✅" }, 3),
      ],
      cta: { text: "想要完整训练计划，评论区扣'计划'", icon: "🏆" },
      colorScheme: inferColorScheme(topic, []),
    };
  }

  // === 大学生/校园类 ===
  if (lower.includes("大学生") || lower.includes("student") || lower.includes("校园")) {
    return {
      hook: {
        text: `大学四年，${topic.replace(/大学[^,，]*,?/g, "")}一定要趁早做`,
        icon: "🎓",
        color: "#4EC9B0",
      },
      steps: [
        enrichStep({ title: "搞钱技能优先", desc: "别只读书，技能才是硬通货", icon: "💰" }, 0),
        enrichStep({ title: "建立人脉网", desc: "同学、老师、学长都是资源", icon: "🌐" }, 1),
        enrichStep({ title: "尝试低成本创业", desc: "自媒体、接单、成本几乎为零", icon: "🚀" }, 2),
        enrichStep({ title: "打造个人IP", desc: "毕业后你已经有粉丝基础", icon: "📱" }, 3),
      ],
      cta: { text: "大学搞钱群，评论区扣'大学'拉你", icon: "🎯" },
      colorScheme: inferColorScheme(topic, []),
    };
  }

  // === 创作/自媒体类 ===
  if (lower.includes("创作") || lower.includes("内容") || lower.includes("写作") || lower.includes("自媒体")) {
    return {
      hook: {
        text: `做${topic.replace(/[^a-zA-Z\u4e00-\u9fa5]/g, "")}，普通人从0到1的最快路径`,
        icon: "✍️",
        color: "#DCDCAA",
      },
      steps: [
        enrichStep({ title: "找准细分定位", desc: "大领域里切一个足够小的点", icon: "🎯" }, 0),
        enrichStep({ title: "先完成再完美", desc: "别等准备好了，先发100条再说", icon: "⚡" }, 1),
        enrichStep({ title: "建立内容模板", desc: "流水线生产，降低创作成本", icon: "📝" }, 2),
        enrichStep({ title: "找到变现路径", desc: "接广告、带货、卖课，选一个", icon: "💵" }, 3),
      ],
      cta: { text: "想要内容模板，评论区扣'模板'", icon: "📋" },
      colorScheme: inferColorScheme(topic, []),
    };
  }

  // === 恋爱/情感类 ===
  if (lower.includes("恋爱") || lower.includes("感情") || lower.includes("关系")) {
    return {
      hook: {
        text: `关于${topic.replace(/[^a-zA-Z\u4e00-\u9fa5]/g, "")}，这3个真相没人告诉你`,
        icon: "💕",
        color: "#FF6B9D",
      },
      steps: [
        enrichStep({ title: "先爱自己", desc: "自己都不够好，别人凭什么爱你", icon: "💪" }, 0),
        enrichStep({ title: "降低期待", desc: "没有完美的人，只有适合的人", icon: "🧠" }, 1),
        enrichStep({ title: "主动但不失框架", desc: "敢表达，但不做舔狗", icon: "✨" }, 2),
        enrichStep({ title: "持续自我提升", desc: "你变好了，好的关系自然会来", icon: "📈" }, 3),
      ],
      cta: { text: "有情感问题，私信我", icon: "💬" },
      colorScheme: inferColorScheme(topic, []),
    };
  }

  // === 默认通用类 ===
  return {
    hook: {
      text: `关于${topic}，你可能还不知道的事`,
      icon: "💡",
      color: "#4EC9B0",
    },
    steps: [
      enrichStep({ title: "看清本质", desc: "理解底层逻辑最重要", icon: "🧠" }, 0),
      enrichStep({ title: "找对方法", desc: "方向不对，努力白费", icon: "🎯" }, 1),
      enrichStep({ title: "快速行动", desc: "想一万遍不如做一遍", icon: "⚡" }, 2),
      enrichStep({ title: "持续迭代", desc: "复盘优化，越做越好", icon: "🔁" }, 3),
    ],
    cta: { text: `觉得有用，关注我，持续分享${topic}干货`, icon: "👉" },
    colorScheme: inferColorScheme(topic, []),
  };
}

// ============================================================
// Layout 生成层（基于脚本）
// ============================================================

// 接收已生成的脚本，构建 layout
export function generateLayoutFromScript(script: VideoScript): TimelineLayout {
  const WIDTH = 1080;
  const HEIGHT = 1920;

  const { primary, fill, text } = script.colorScheme;
  const hookColor = script.hook.color;

  const boxes: BoxData[] = [];
  const arrows: ArrowData[] = [];

  // ---- Hook（开场大标题）----
  // 比普通 step 更早出现，更大字体，更强的视觉冲击
  boxes.push({
    id: "hook",
    label: script.hook.text,
    subLabel: undefined,
    x: 60,
    y: 160,
    width: 960,
    height: 220,
    color: hookColor,
    fillColor: `${hookColor}20`,
    textColor: hookColor,
    fontSize: 44,
    showFrom: 0,
    durationInFrames: 75,
    tag: "title",
    icon: script.hook.icon,
    zIndex: 100,
  });

  // ---- Steps（时间线卡片）----
  const stepCount = script.steps.length;
  const stepBoxW = 420;
  const stepBoxH = 170;
  const stepStartX = (WIDTH - stepBoxW) / 2;
  const stepStartY = 480;
  const stepGap = 220;

  script.steps.forEach((step, i) => {
    const id = `step${i + 1}`;
    const y = stepStartY + i * stepGap;
    const showFrom = 30 + i * 65; // 错峰65帧

    boxes.push({
      id,
      label: step.title,
      subLabel: step.desc,
      x: stepStartX,
      y,
      width: stepBoxW,
      height: stepBoxH,
      color: primary,
      fillColor: fill,
      textColor: text,
      fontSize: 34,
      showFrom,
      durationInFrames: 160,
      icon: step.icon,
      image: step.image,
      zIndex: 10 + i,
    });

    // 连接箭头
    if (i === 0) {
      arrows.push({
        id: `arrow-hook-${id}`,
        fromBoxId: "hook",
        toBoxId: id,
        color: primary,
        showFrom: 25 + i * 65,
      });
    } else {
      arrows.push({
        id: `arrow-step${i}-${id}`,
        fromBoxId: `step${i}`,
        toBoxId: id,
        color: primary,
        showFrom: 25 + i * 65,
      });
    }
  });

  // ---- CTA（结尾行动号召）----
  const lastStepY = stepStartY + (stepCount - 1) * stepGap;
  const ctaShowFrom = 30 + stepCount * 65;

  boxes.push({
    id: "cta",
    label: script.cta.text,
    subLabel: "看完觉得有用，点个关注",
    x: (WIDTH - 460) / 2,
    y: lastStepY + stepGap,
    width: 460,
    height: 150,
    color: script.cta.icon === "🔥" ? "#FF6B6B" : primary,
    fillColor: script.cta.icon === "🔥" ? "rgba(255,107,107,0.15)" : fill,
    textColor: script.cta.icon === "🔥" ? "#FF6B6B" : text,
    fontSize: 32,
    showFrom: ctaShowFrom,
    durationInFrames: 130,
    icon: script.cta.icon,
    zIndex: 10 + stepCount,
  });

  arrows.push({
    id: "arrow-last-cta",
    fromBoxId: `step${stepCount}`,
    toBoxId: "cta",
    color: script.cta.icon === "🔥" ? "#FF6B6B" : primary,
    showFrom: ctaShowFrom + 10,
  });

  return {
    width: WIDTH,
    height: HEIGHT,
    backgroundImage: "",
    backgroundImageAlt: "",
    boxes,
    arrows,
  };
}

// 同步版本（使用规则脚本生成，保持向后兼容）
export function generateLayoutFromTopic(topic: string): TimelineLayout {
  return generateLayoutFromScript(ruleBasedScript(topic));
}

// 快速测试用
export function generateMiniLayout(label: string, count = 3): TimelineLayout {
  const WIDTH = 1080;
  const HEIGHT = 1920;
  const primary = "#4EC9B0";
  const fill = "rgba(78,201,176,0.12)";
  const text = "#FFFFFF";

  const boxes: BoxData[] = [];
  const arrows: ArrowData[] = [];

  boxes.push({
    id: "title",
    label,
    x: 200,
    y: 100,
    width: 680,
    height: 140,
    color: primary,
    fillColor: fill,
    textColor: "#FFFFFF",
    fontSize: 56,
    showFrom: 0,
    durationInFrames: 60,
    tag: "title",
    zIndex: 100,
  });

  for (let i = 0; i < count; i++) {
    const id = `step${i + 1}`;
    boxes.push({
      id,
      label: `步骤 ${i + 1}`,
      subLabel: `这是第 ${i + 1} 步说明`,
      x: 200,
      y: 350 + i * 200,
      width: 680,
      height: 150,
      color: primary,
      fillColor: fill,
      textColor: text,
      fontSize: 38,
      showFrom: 20 + i * 50,
      durationInFrames: 120,
      icon: ["📚", "💡", "🚀"][i % 3],
      zIndex: 10 + i,
    });

    if (i > 0) {
      arrows.push({
        id: `arrow${i - 1}-${i}`,
        fromBoxId: `step${i}`,
        toBoxId: id,
        color: primary,
        showFrom: 20 + i * 50 + 10,
      });
    }
  }

  return {
    width: WIDTH,
    height: HEIGHT,
    backgroundImage: "",
    backgroundImageAlt: "",
    boxes,
    arrows,
  };
}

// ============================================================
// 步骤版式分配（4种版式循环）
// ============================================================
const LAYOUT_TYPES: StepLayoutType[] = ["full-image", "split", "cinematic", "text-only"];

// 为单个 step 注入 imageKeyword 和 layoutType
function enrichStep(step: { title: string; desc: string; icon: string }, i: number) {
  // 图片关键词：取标题前20字转英文
  const kw = step.title.replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, " ").trim().slice(0, 25);
  return {
    ...step,
    imageKeyword: kw,
    layoutType: LAYOUT_TYPES[i % 4],
  };
}

// ============================================================
// VideoLayout 生成（V6 新版元素系统）
// ============================================================


/**
 * Phase 1（async）：预获取所有图片资产
 *
 * 在 render 之前，先把所有图片 URL 都解析好。
 * 返回的 assets 直接注入 layout builder，保证 render pipeline 是 sync 的。
 *
 * @param topic         视频主题（用于 hook 背景图）
 * @param stepKeywords  所有 step 的 imageKeyword 数组
 * @returns 预解析好的 hookAsset 和 stepAssets
 */
/**
 * Phase 1（async）：预获取所有图片资产（V9 工业版）
 *
 * 三层优化：
 * 1. Canonical concept 规范化 → cacheKey 基于语义等价的 concept id
 * 2. In-flight dedupe → 相同 conceptId 的并发请求共享同一 Promise
 * 3. 2-layer cache → globalImageCache (resolved) + inFlightPromises (pending)
 *
 * @param topic        视频主题（用于 hook 背景图）
 * @param stepKeywords 所有 step 的 imageKeyword 数组
 */
export async function preResolveAllImages(
  topic: string,
  stepKeywords: string[]
): Promise<{ hookAsset: ImageAsset; stepAssets: ImageAsset[] }> {
  const router = getImageSourceRouter();

  // Normalize all concepts first (deterministic, same concept = same canonicalId)
  const hookConcept = normalizeConcept(topic);
  const hookQuery = applyStyleToken(topic, "cinematic");
  const hookCacheKey = buildCacheKey(topic, "hook", "cinematic");
  const stepConcepts = stepKeywords.map(kw => normalizeConcept(kw));
  const stepCacheKeys = stepKeywords.map((kw, i) => ({
    keyword: kw,
    conceptId: stepConcepts[i].id,
    query: applyStyleToken(kw, "cinematic"),
    cacheKey: buildCacheKey(kw, "steps", "cinematic"),
  }));

  // Phase 0: cache lookup (deterministic, no API call)
  const hookCached = globalImageCache.get(hookCacheKey);
  const stepCached = stepCacheKeys.map(sk => globalImageCache.get(sk.cacheKey));

  // If all cache hits → return immediately (zero API calls)
  if (hookCached && stepCached.every((a): a is ImageAsset => a !== undefined)) {
    console.info(`[ImageCache] all hit: hook + ${stepCached.length} steps (canonicalIds: hook=${hookConcept.id})`);
    return { hookAsset: hookCached, stepAssets: stepCached as ImageAsset[] };
  }

  /**
   * Resolve a single image with in-flight dedupe
   * - Cache hit → return cached asset
   * - In-flight → return existing Promise (dedupe concurrent requests)
   * - Cold → call API, store Promise
   */
  async function resolveOne(
    query: string,
    cacheKey: string,
    conceptId: string,
    w: number,
    h: number
  ): Promise<ImageAsset> {
    // Cache hit
    const cached = globalImageCache.get(cacheKey);
    if (cached) return cached;

    // In-flight dedupe: same conceptId in same batch shares the same Promise
    if (inFlightPromises.has(conceptId)) {
      console.info('[ImageCache] in-flight dedupe: "' + conceptId + '"');
      return inFlightPromises.get(conceptId)!;
    }

    // Cold: call router, store Promise
    const promise = router.resolve(query, w, h).then(asset => {
      // Inject canonical conceptId + conceptId into asset
      const enriched: ImageAsset = {
        ...asset,
        conceptId,
        cacheKey,
        renderHints: inferRenderHints(asset),
      };
      globalImageCache.set(cacheKey, enriched);
      inFlightPromises.delete(conceptId);
      return enriched;
    });

    inFlightPromises.set(conceptId, promise);
    return promise;
  }

  // Resolve hook (if not cached)
  const hookPromise = hookCached
    ? Promise.resolve(hookCached)
    : resolveOne(hookQuery, hookCacheKey, hookConcept.id, 1080, 600);

  // Resolve steps with in-flight dedupe
  const stepPromises = stepCacheKeys.map((sk, i) =>
    stepCached[i]
      ? Promise.resolve(stepCached[i])
      : resolveOne(sk.query, sk.cacheKey, sk.conceptId, 540, 960)
  );

  const results = await Promise.all([hookPromise, ...stepPromises]);

  // Clear all in-flight after batch resolves
  console.info(`[ImageCache] resolved: hook=${results[0].provider} conceptId=${results[0].conceptId}, steps=[${results.slice(1).map(a => a.conceptId).join(",")}]`);
  return { hookAsset: results[0], stepAssets: results.slice(1) };
}

/**
 * 从 ImageAsset 推断渲染提示（V9 新增）
 *
 * Pexels 返回的 avg_color 可用于亮度补偿决策
 * type=deterministic 的 picsum 图偏"噪声感"，适合做 overlay 背景
 */
function inferRenderHints(asset: ImageAsset): ImageAsset["renderHints"] {
  // Picsum deterministic 图：偏噪声，适合做模糊背景
  if (asset.type === "deterministic") {
    return { fit: "cover", blurBackground: true, brightnessBoost: -0.15 };
  }
  // CDN (Pexels) 图：尽量 cover 填充，中心裁剪
  if (asset.type === "cdn") {
    return { fit: "cover", focus: "center" };
  }
  // Proxy 图（Unsplash）：contain 保留全图
  return { fit: "contain", focus: "auto" };
}

export function generateVideoLayoutFromScript(
  script: VideoScript,
  preResolved?: { hookAsset: ImageAsset; stepAssets: ImageAsset[] },
  _director?: DirectorIntent,
  subtitleCues?: SubtitleCue[]
): VideoLayout {
  const WIDTH = 1080;
  const HEIGHT = 1920;
  const { primary, fill, text } = script.colorScheme;
  const hookColor = script.hook.color;

  // V7: scene-aware 图像规划
  // topic 优先用 script.topic，没有则从 hook.text 推导（保持向后兼容）
  const topic = script.topic ?? script.hook.text.replace(/[^\u4e00-\u9fa5a-zA-Z0-9\s]/g, " ").trim();
  // Use pre-resolved hook image URL if available, otherwise leave transparent
  const hookImageUrl = preResolved?.hookAsset?.url ?? '';

  interface AnyEl {
    id: string; type: string; start: number; duration: number; zIndex: number;
    [key: string]: unknown;
  }
  const elements: AnyEl[] = [];
  let zIdx = 0;

  /** 从 subtitleCues 中提取指定 sceneIdx 的 wordCues */
  const getWordCues = (sceneIdx: number): WordCue[] | undefined => {
    if (!subtitleCues) return undefined;
    const cue = subtitleCues.find((c) => c.id === `scene-${sceneIdx}`);
    return cue?.words;
  };

  // ============================================================
  // 背景
  // ============================================================
  elements.push({
    id: "bg", type: "background", start: 0, duration: 9999, zIndex: zIdx++,
    gradient: `linear-gradient(160deg, #0f2027 0%, #1a2a3a 40%, #203a43 70%, #0f2027 100%)`,
  });

  // ============================================================
  // Hook - 爆炸式开场（V7: scene-aware 背景图）
  // ============================================================
  // scene-aware hook 背景图（zIndex=1，在 glow 之下）
  elements.push({
    id: "hook-bgimg", type: "image",
    src: hookImageUrl,
    x: 0, y: 0, width: WIDTH, height: 600,
    borderRadius: 0,
    start: 0, duration: 70, zIndex: zIdx,
    animation: { enter: "zoom-in", duration: 30 },
  });
  // 深色遮罩（让文字可读）
  elements.push({
    id: "hook-bgmask", type: "shape", shape: "rect",
    x: 0, y: 0, width: WIDTH, height: 600,
    color: "transparent", fillColor: "rgba(10,14,20,0.65)",
    start: 0, duration: 70, zIndex: zIdx + 1,
    animation: { enter: "fade", duration: 20 },
  });
  // glow 在最上层
  zIdx += 2;
  elements.push({
    id: "hook-glow", type: "shape", shape: "circle",
    x: WIDTH / 2 - 200, y: 60, width: 400, height: 400,
    color: hookColor, fillColor: `${hookColor}18`,
    start: 0, duration: 50, zIndex: zIdx,
    animation: { enter: "zoom-in", duration: 18 },
  });

  elements.push({
    id: "hook-emoji", type: "sticker",
    emoji: script.hook.icon,
    x: 490, y: 70, size: 96,
    start: 0, duration: 90, zIndex: zIdx + 1,
    animation: { enter: "bounce-in", duration: 16 },
  });

  elements.push({
    id: "hook-text", type: "text", start: 5, duration: 90, zIndex: zIdx + 2,
    text: script.hook.text,
    x: 40, y: 200,
    fontSize: 52, color: hookColor, fontWeight: 900,
    wordCues: getWordCues(0),
    animation: { enter: "bounce-in", exit: "fade", duration: 22 },
  });

  elements.push({
    id: "hook-line", type: "shape", shape: "rect",
    x: 200, y: 320, width: 680, height: 4,
    color: hookColor, fillColor: hookColor,
    start: 15, duration: 75, zIndex: zIdx + 1,
    animation: { enter: "slide-up", duration: 14 },
  });

  elements.push({
    id: "hook-flash", type: "shape", shape: "rect",
    x: 0, y: 0, width: WIDTH, height: HEIGHT,
    color: "#FFFFFF", fillColor: "rgba(255,255,255,0.06)",
    start: 28, duration: 8, zIndex: 999,
    animation: { enter: "fade", exit: "fade", duration: 8 },
  });

  // ============================================================
  // Steps - 4种版式 + 时间轴错位
  // ============================================================
  const stepCount = script.steps.length;
  const stepGap = 380;
  const stepStartY = 340;
  const baseShowFrom = 35;

  // V8: 使用预解析的 step 图片（Phase 1 已完成）
  const stepImageUrls = preResolved?.stepAssets.map(a => a.url) ??
    script.steps.map(() => '');

  script.steps.forEach((step, i) => {
    const sz = zIdx + i * 15;
    const showFrom = baseShowFrom + i * 70; // 拉开间距
    const layoutType = step.layoutType ?? LAYOUT_TYPES[i % 4];

    // ---- 版式0：封面沉浸式（图片全屏 + 居中大字）----
    if (layoutType === "full-image") {
      const imgH = 500;
      const imgY = stepStartY + i * stepGap;

      elements.push({
        id: `s${i+1}-img`, type: "image",
        src: stepImageUrls[i],
        x: 30, y: imgY, width: WIDTH - 60, height: imgH,
        borderRadius: 16,
        start: showFrom, duration: 170, zIndex: sz,
        animation: { enter: "zoom-in", duration: 18 },
      });
      elements.push({
        id: `s${i+1}-overlay`, type: "shape", shape: "rect",
        x: 30, y: imgY, width: WIDTH - 60, height: imgH,
        color: "transparent", fillColor: "rgba(10,14,20,0.55)",
        borderRadius: 16,
        start: showFrom, duration: 170, zIndex: sz + 1,
        animation: { enter: "fade", duration: 12 },
      });
      // 编号
      elements.push({
        id: `s${i+1}-n`, type: "shape", shape: "circle",
        x: 60, y: imgY + 20, width: 52, height: 52,
        color: primary, fillColor: `${primary}30`,
        start: showFrom + 4, duration: 166, zIndex: sz + 2,
        animation: { enter: "bounce-in", duration: 12 },
      });
      elements.push({
        id: `s${i+1}-num`, type: "text",
        text: String(i + 1),
        x: 60, y: imgY + 26,
        fontSize: 24, color: primary, fontWeight: 900, textAlign: "center",
        start: showFrom + 6, duration: 164, zIndex: sz + 3,
      });
      // 大标题居中
      elements.push({
        id: `s${i+1}-title`, type: "text",
        text: step.title,
        x: 60, y: imgY + 180,
        fontSize: 42, color: "#FFFFFF", fontWeight: 800, textAlign: "center", maxWidth: WIDTH - 120,
        start: showFrom + 8, duration: 162, zIndex: sz + 2,
        wordCues: getWordCues(i + 1),
        animation: { enter: "bounce-in", duration: 16 },
      });
      // 描述
      elements.push({
        id: `s${i+1}-desc`, type: "text",
        text: step.desc,
        x: 60, y: imgY + 310,
        fontSize: 22, color: "#CCCCCC", fontWeight: 400, textAlign: "center", maxWidth: WIDTH - 120,
        start: showFrom + 20, duration: 150, zIndex: sz + 2,
        animation: { enter: "fade", duration: 14 },
      });
      // emoji
      elements.push({
        id: `s${i+1}-emoji`, type: "sticker",
        emoji: step.icon,
        x: WIDTH - 120, y: imgY + 20, size: 52,
        start: showFrom + 12, duration: 158, zIndex: sz + 3,
        animation: { enter: "bounce-in", duration: 14 },
      });
      return;
    }

    // ---- 版式1：左图右文（分栏）----
    if (layoutType === "split") {
      const imgW = 480, imgH = 340;
      const cardX = 540;
      const cardY = stepStartY + i * stepGap + 20;
      const imgY = cardY;

      elements.push({
        id: `s${i+1}-img`, type: "image",
        src: stepImageUrls[i],
        x: 40, y: imgY, width: imgW, height: imgH,
        borderRadius: 16,
        start: showFrom, duration: 170, zIndex: sz,
        animation: { enter: "zoom-in", duration: 16 },
      });
      elements.push({
        id: `s${i+1}-overlay`, type: "shape", shape: "rect",
        x: 40, y: imgY, width: imgW, height: imgH,
        color: "transparent", fillColor: "rgba(10,14,20,0.35)",
        borderRadius: 16,
        start: showFrom, duration: 170, zIndex: sz + 1,
        animation: { enter: "fade", duration: 10 },
      });
      // 编号
      elements.push({
        id: `s${i+1}-n`, type: "shape", shape: "circle",
        x: cardX, y: cardY - 10, width: 48, height: 48,
        color: primary, fillColor: `${primary}30`,
        start: showFrom + 6, duration: 164, zIndex: sz + 2,
        animation: { enter: "bounce-in", duration: 12 },
      });
      elements.push({
        id: `s${i+1}-num`, type: "text",
        text: String(i + 1),
        x: cardX, y: cardY - 4,
        fontSize: 22, color: primary, fontWeight: 900, textAlign: "center",
        start: showFrom + 8, duration: 162, zIndex: sz + 3,
      });
      // 标题（右侧）
      elements.push({
        id: `s${i+1}-title`, type: "text",
        text: step.title,
        x: cardX + 60, y: cardY,
        fontSize: 32, color: "#FFFFFF", fontWeight: 700, textAlign: "left", maxWidth: WIDTH - cardX - 60,
        start: showFrom + 8, duration: 162, zIndex: sz + 2,
        wordCues: getWordCues(i + 1),
        animation: { enter: "slide-up", duration: 14 },
      });
      // 描述
      elements.push({
        id: `s${i+1}-desc`, type: "text",
        text: step.desc,
        x: cardX + 60, y: cardY + 55,
        fontSize: 18, color: "#BBBBBB", fontWeight: 400, textAlign: "left", maxWidth: WIDTH - cardX - 80,
        start: showFrom + 18, duration: 152, zIndex: sz + 2,
        animation: { enter: "slide-up", duration: 14 },
      });
      // emoji
      elements.push({
        id: `s${i+1}-emoji`, type: "sticker",
        emoji: step.icon,
        x: cardX + 420, y: cardY - 5, size: 50,
        start: showFrom + 12, duration: 158, zIndex: sz + 3,
        animation: { enter: "bounce-in", duration: 14 },
      });
      return;
    }

    // ---- 版式2：电影感（深遮罩 + 底部小字）----
    if (layoutType === "cinematic") {
      const imgY = stepStartY + i * stepGap;
      const imgH = 450;

      elements.push({
        id: `s${i+1}-img`, type: "image",
        src: stepImageUrls[i],
        x: 0, y: imgY, width: WIDTH, height: imgH,
        borderRadius: 0,
        start: showFrom, duration: 170, zIndex: sz,
        animation: { enter: "zoom-in", duration: 20 },
      });
      // 深遮罩
      elements.push({
        id: `s${i+1}-overlay`, type: "shape", shape: "rect",
        x: 0, y: imgY, width: WIDTH, height: imgH,
        color: "transparent", fillColor: "rgba(10,14,20,0.72)",
        borderRadius: 0,
        start: showFrom, duration: 170, zIndex: sz + 1,
        animation: { enter: "fade", duration: 12 },
      });
      // 编号（小，底部左侧）
      elements.push({
        id: `s${i+1}-n`, type: "shape", shape: "circle",
        x: 50, y: imgY + imgH - 80, width: 44, height: 44,
        color: primary, fillColor: `${primary}25`,
        start: showFrom + 8, duration: 162, zIndex: sz + 2,
        animation: { enter: "fade", duration: 12 },
      });
      elements.push({
        id: `s${i+1}-num`, type: "text",
        text: String(i + 1),
        x: 50, y: imgY + imgH - 74,
        fontSize: 20, color: primary, fontWeight: 900, textAlign: "center",
        start: showFrom + 10, duration: 160, zIndex: sz + 3,
      });
      // 标题（底部，大）
      elements.push({
        id: `s${i+1}-title`, type: "text",
        text: step.title,
        x: 110, y: imgY + imgH - 90,
        fontSize: 36, color: "#FFFFFF", fontWeight: 800, textAlign: "left", maxWidth: WIDTH - 220,
        start: showFrom + 10, duration: 160, zIndex: sz + 2,
        wordCues: getWordCues(i + 1),
        animation: { enter: "slide-up", duration: 16 },
      });
      // 描述（底部，小字）
      elements.push({
        id: `s${i+1}-desc`, type: "text",
        text: step.desc,
        x: 110, y: imgY + imgH - 45,
        fontSize: 17, color: "#AAAAAA", fontWeight: 400, textAlign: "left", maxWidth: WIDTH - 220,
        start: showFrom + 22, duration: 148, zIndex: sz + 2,
        animation: { enter: "fade", duration: 12 },
      });
      // emoji（右上角）
      elements.push({
        id: `s${i+1}-emoji`, type: "sticker",
        emoji: step.icon,
        x: WIDTH - 100, y: imgY + 15, size: 48,
        start: showFrom + 14, duration: 156, zIndex: sz + 3,
        animation: { enter: "bounce-in", duration: 14 },
      });
      return;
    }

    // ---- 版式3：纯文字冲击（不要图片）----
    // text-only：超大字 + bounce 冲击
    const ty = stepStartY + i * stepGap + 30;

    elements.push({
      id: `s${i+1}-glow`, type: "shape", shape: "circle",
      x: WIDTH / 2 - 180, y: ty - 40, width: 360, height: 360,
      color: primary, fillColor: `${primary}15`,
      start: showFrom, duration: 170, zIndex: sz,
      animation: { enter: "zoom-in", duration: 18 },
    });
    // 编号（超大）
    elements.push({
      id: `s${i+1}-n`, type: "shape", shape: "circle",
      x: WIDTH / 2 - 40, y: ty, width: 80, height: 80,
      color: primary, fillColor: `${primary}25`,
      start: showFrom, duration: 170, zIndex: sz + 1,
      animation: { enter: "bounce-in", duration: 14 },
    });
    elements.push({
      id: `s${i+1}-num`, type: "text",
      text: String(i + 1),
      x: WIDTH / 2 - 40, y: ty + 12,
      fontSize: 40, color: primary, fontWeight: 900, textAlign: "center",
      start: showFrom + 4, duration: 166, zIndex: sz + 2,
    });
    // 超大标题
    elements.push({
      id: `s${i+1}-title`, type: "text",
      text: step.title,
      x: 80, y: ty + 110,
      fontSize: 44, color: "#FFFFFF", fontWeight: 800, textAlign: "center", maxWidth: WIDTH - 160,
      start: showFrom + 8, duration: 162, zIndex: sz + 2,
      wordCues: getWordCues(i + 1),
      animation: { enter: "bounce-in", duration: 18 },
    });
    // 描述
    elements.push({
      id: `s${i+1}-desc`, type: "text",
      text: step.desc,
      x: 80, y: ty + 220,
      fontSize: 20, color: "#BBBBBB", fontWeight: 400, textAlign: "center", maxWidth: WIDTH - 160,
      start: showFrom + 24, duration: 146, zIndex: sz + 2,
      animation: { enter: "fade", duration: 14 },
    });
    // emoji
    elements.push({
      id: `s${i+1}-emoji`, type: "sticker",
      emoji: step.icon,
      x: WIDTH - 120, y: ty + 130, size: 56,
      start: showFrom + 14, duration: 156, zIndex: sz + 3,
      animation: { enter: "bounce-in", duration: 14 },
    });
  });

  // ============================================================
  // CTA - 爆炸式结尾
  // ============================================================
  const ctaShowFrom = baseShowFrom + stepCount * 70;
  const ctaY = stepStartY + stepCount * stepGap;

  elements.push({
    id: "cta-glow", type: "shape", shape: "circle",
    x: WIDTH / 2 - 280, y: ctaY - 60, width: 560, height: 340,
    color: "#FF6B6B", fillColor: "rgba(255,107,107,0.12)",
    start: ctaShowFrom, duration: 60, zIndex: zIdx + stepCount * 15,
    animation: { enter: "zoom-in", duration: 22 },
  });

  elements.push({
    id: "cta-bg", type: "shape", shape: "rect",
    x: (WIDTH - 640) / 2, y: ctaY, width: 640, height: 170,
    color: "#FF6B6B", fillColor: "rgba(255,107,107,0.2)", borderRadius: 28,
    start: ctaShowFrom, duration: 140, zIndex: zIdx + stepCount * 15 + 1,
    animation: { enter: "bounce-in", exit: "fade", duration: 22 },
  });

  elements.push({
    id: "cta-emoji", type: "sticker",
    emoji: script.cta.icon,
    x: WIDTH / 2 + 230, y: ctaY + 10, size: 80,
    start: ctaShowFrom + 5, duration: 135, zIndex: zIdx + stepCount * 15 + 2,
    animation: { enter: "bounce-in", duration: 18 },
  });

  elements.push({
    id: "cta-text", type: "text",
    text: script.cta.text,
    x: 80, y: ctaY + 35,
    fontSize: 36, color: "#FF6B6B", fontWeight: 800, textAlign: "center", maxWidth: WIDTH - 380,
    start: ctaShowFrom + 12, duration: 128, zIndex: zIdx + stepCount * 15 + 2,
    wordCues: getWordCues(script.steps.length + 1),
    animation: { enter: "bounce-in", duration: 20 },
  });

  elements.push({
    id: "cta-sub", type: "text",
    text: "看完觉得有用，点个关注",
    x: 80, y: ctaY + 95,
    fontSize: 18, color: "#AAAAAA", fontWeight: 400, textAlign: "center", maxWidth: WIDTH - 380,
    start: ctaShowFrom + 28, duration: 112, zIndex: zIdx + stepCount * 15 + 2,
    animation: { enter: "fade", duration: 16 },
  });

  elements.push({
    id: "cta-flash", type: "shape", shape: "rect",
    x: 0, y: 0, width: WIDTH, height: HEIGHT,
    color: "#FF6B6B", fillColor: "rgba(255,107,107,0.06)",
    start: ctaShowFrom + 22, duration: 12, zIndex: 999,
    animation: { enter: "fade", exit: "fade", duration: 12 },
  });

  // ============================================================
  // 进度条（底部，用户停留神器）
  // ============================================================
  const totalDuration = ctaShowFrom + 140;
  elements.push({
    id: "progress-bar-bg", type: "shape", shape: "rect",
    x: 0, y: HEIGHT - 8, width: WIDTH, height: 8,
    color: "rgba(255,255,255,0.1)", fillColor: "rgba(255,255,255,0.1)",
    start: 0, duration: totalDuration, zIndex: 998,
    animation: { enter: "fade", duration: 1 },
  });
  // 进度条前景（frame 对应宽度，由 VideoScene interpolate 计算）
  elements.push({
    id: "progress-bar", type: "shape", shape: "rect",
    x: 0, y: HEIGHT - 8, width: 0, height: 8,
    color: "#FF6B6B", fillColor: "#FF6B6B",
    start: 0, duration: totalDuration, zIndex: 999,
    animation: { enter: "fade", duration: 1 },
  });

  // ============================================================
  // v10: Shot System（镜头系统）
  // 每张 step 图片生成 2-3 个镜头（camera motion），素材不再是静态背景
  // ============================================================
  type ShotCamera = "push-in" | "pan-left" | "pan-right" | "pull-out" | "static";
  const CAMERA_POOL: ShotCamera[] = ["push-in", "pan-left", "pan-right", "static", "pull-out"];

  function buildShots(
    stepImageUrls: string[],
    stepStartFrames: number[],
    stepDurations: number[]
  ): VideoLayout["shots"] {
    const shots: VideoLayout["shots"] = [];

    stepImageUrls.forEach((src, i) => {
      if (!src) return;
      const stepStart = stepStartFrames[i];
      const stepDur = stepDurations[i] ?? 150;
      const numShots = src.startsWith("http") ? 2 + (i % 2) : 0; // 有图才生成镜头

      for (let s = 0; s < numShots; s++) {
        const cameraIdx = (i * 2 + s) % CAMERA_POOL.length;
        const camera = CAMERA_POOL[cameraIdx];
        const shotDur = Math.floor(stepDur / numShots);
        const shotStart = stepStart + s * shotDur;

        // pan 类镜头：crop 偏移产生运动感
        let cropX = 0, cropY = 0, cropW = 1, cropH = 1;
        if (camera === "pan-right") {
          cropX = 0.08 * s; cropW = 0.92;
        } else if (camera === "pan-left") {
          cropX = 0.08 * s; cropW = 0.92;
        } else if (camera === "push-in") {
          cropW = 0.92 - s * 0.06; cropH = 0.92 - s * 0.06;
          cropX = (1 - cropW) / 2; cropY = (1 - cropH) / 2;
        } else if (camera === "pull-out") {
          cropW = 0.75 + s * 0.1; cropH = 0.75 + s * 0.1;
          cropX = (1 - cropW) / 2; cropY = (1 - cropH) / 2;
        }

        shots.push({
          start: shotStart,
          duration: shotDur,
          src,
          camera,
          cropX,
          cropY,
          cropW,
          cropH,
          opacity: 0.95,
        });
      }
    });

    return shots;
  }

  // 收集每个 step 的起始帧和持续帧（用于生成 shots）
  const stepStartFrames: number[] = [];
  const stepDurations: number[] = [];
  let _frameCursor = 0;
  // hook: 0 ~ 90 帧
  stepStartFrames.push(0);
  stepDurations.push(90);
  _frameCursor = 90;
  // steps
  for (let i = 0; i < script.steps.length; i++) {
    const stepStart = _frameCursor;
    const stepDur = 70 + i * 70;
    stepStartFrames.push(stepStart);
    stepDurations.push(stepDur);
    _frameCursor += stepDur;
  }

  const shots = buildShots(stepImageUrls, stepStartFrames, stepDurations);

  return {
    width: WIDTH,
    height: HEIGHT,
    fps: 30,
    background: "#0A0E14",
    elements: elements as VideoLayout["elements"],
    director: _director,
    subtitleCues,
    shots,
  };
}
