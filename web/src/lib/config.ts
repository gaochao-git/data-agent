/**
 * 全局配置文件
 */

// API 基础地址配置
// 自动使用 Vite 的 base 配置（默认 /ai）
// 与 vite.config.ts 中的 base 保持同步
// 开发环境：/ai/api → Vite 代理转发到后端
// 生产环境：/ai/api → Nginx 代理转发到后端
export const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, '');

// 其他全局配置可以在这里添加
export const DEFAULT_TIMEOUT = 30000; // 30秒超时

const DEFAULT_MAX_FILE_SIZE_MB = 50;
const maxFileSizeEnv = import.meta.env.VITE_MAX_FILE_SIZE_MB || import.meta.env.VITE_MAX_UPLOAD_MB;
const parsedMaxFileSizeMb = Number.parseInt(maxFileSizeEnv ?? '', 10);
export const MAX_FILE_SIZE_MB =
  Number.isFinite(parsedMaxFileSizeMb) && parsedMaxFileSizeMb > 0
    ? parsedMaxFileSizeMb
    : DEFAULT_MAX_FILE_SIZE_MB;
export const MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024;

export const AUTO_START_ANALYSIS_ON_UPLOAD =
  String(import.meta.env.VITE_AUTO_START_ANALYSIS_ON_UPLOAD ?? 'false').toLowerCase() === 'true';

export const TRACE_PANEL_ENABLED =
  String(import.meta.env.VITE_ENABLE_TRACE_PANEL ?? 'false').toLowerCase() === 'true';
