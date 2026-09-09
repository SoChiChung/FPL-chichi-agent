"use strict";

/**
 * web/config.js — 站点配置常量（Phase 2.5 内容架构 + BugFix/UX v1.0 + v1.5）
 * External Links 视觉层级：头像 → 品牌名 → 平台导航（图标降权）。
 * 「关于我」不再展示 Overall Rank/总分（v1.4）；tagline 已废弃（v1.5）。
 * 头像放在 web/img/ava.png（由根目录 img/ 经 Vercel build `cp img/*.png` 复制而来）。
 *
 * 字段说明：
 *  - avatar: 「关于我」头像图路径（显示于品牌名上方）
 *  - brand: 主品牌区——name 主名 / handle 用户名（无副标语）
 *  - externalLinks: 平台导航（视觉最弱），空数组时仅隐藏导航行
 */
window.SITE_CONFIG = {
  avatar: "img/ava.png",
  brand: {
    name: "FPL紫葱酱",
    handle: "@zcj",
  },
  externalLinks: [
    { label: "小红书", url: "#", icon: "img/小红书icon.png" },
    { label: "B站", url: "#", icon: "img/bilibili.png" },
    { label: "懂球帝", url: "#", icon: "img/dqd.png" },
    { label: "微信", url: "#", icon: "img/wechat-logo.png" },
  ],
};
