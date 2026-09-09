"use strict";

/**
 * web/config.js — 站点配置常量（Phase 2.5 内容架构 + BugFix/UX v1.0）
 * External Links 视觉层级：品牌名 → 核心数据 → 平台导航（图标降权）。
 * 图标放在 web/img/（由根目录 img/ 经 vercel build 复制而来）。
 *
 * 字段说明：
 *  - brand: 主品牌区（视觉最大）——name 主名 / handle 用户名 / tagline 副标语
 *  - heroMetrics: 从 state 读取的核心 FPL 指标 key 白名单（渲染在品牌下方）
 *  - externalLinks: 平台导航（视觉最弱），空数组时仅隐藏导航行
 */
window.SITE_CONFIG = {
  brand: {
    name: "FPL紫葱酱",
    handle: "@zcj",
    tagline: "FPL Analytics · Data Tracking",
  },
  heroMetrics: ["rank", "points"],
  externalLinks: [
    { label: "小红书", url: "#", icon: "img/小红书icon.png" },
    { label: "B站", url: "#", icon: "img/bilibili.png" },
    { label: "懂球帝", url: "#", icon: "img/dqd.png" },
    { label: "微信", url: "#", icon: "img/wechat-logo.png" },
  ],
};
