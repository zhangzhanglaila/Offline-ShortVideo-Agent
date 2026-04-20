/**
 * Constants - 字体配置
 *
 * 使用基础回退链，让 Remotion Chromium 自行处理中文字体
 */
export const FONT_FAMILY = "NotoSansSC, sans-serif";

export const FONT_STYLE = {
  fontFamily: FONT_FAMILY,
} as const;

export const COLORS = {
  brand: "#0B84F3",
  white: "#FFFFFF",
  black: "#000000",
  darkBg: "#0D0D0D",
} as const;

// 聚焦系统常量
export const FOCUS_DURATION = 50;
