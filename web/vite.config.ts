/** WARNING: DON'T EDIT THIS FILE */
/** WARNING: DON'T EDIT THIS FILE */
/** WARNING: DON'T EDIT THIS FILE */

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";

function getPlugins() {
  const plugins = [react(), tsconfigPaths()];
  return plugins;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  // 默认保持现有的 /ai 前缀，同时允许通过 VITE_BASE_PATH 覆盖
  const configuredBase = env.VITE_BASE_PATH ?? "/ai";
  const base = configuredBase.endsWith("/") ? configuredBase : `${configuredBase}/`;
  const canonicalBasePath =
    base === "/" || base.startsWith("http") ? "/" : base.replace(/\/$/, "");

  // 获取后端 API 地址
  const apiBaseUrl = env.VITE_API_BASE_URL || "http://127.0.0.1:5001";

  const plugins = getPlugins();

  if (canonicalBasePath !== "/") {
    plugins.push({
      name: "canonical-base-path",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const originalUrl = req.url ?? "";
          const [path, search] = originalUrl.split("?");

          if (path === `${canonicalBasePath}/`) {
            const location = canonicalBasePath + (search ? `?${search}` : "");
            res.statusCode = 301;
            res.setHeader("Location", location);
            res.end();
            return;
          }

          if (path === canonicalBasePath) {
            req.url = `${path}/${search ? `?${search}` : ""}`;
          }

          next();
        });
      },
    });
  }

  return {
    base,
    plugins,
    server: {
      // 允许局域网访问（0.0.0.0），便于用本机IP在其他设备访问
      host: true,
      port: 3000,
      strictPort: true,
      cors: true,
      // 显式固定 HMR 的端口，避免浏览器或 VPN 阻塞随机端口（如 10090）
      hmr: {
        port: 3000,
        clientPort: 3000,
        protocol: 'ws',
      },
      // 代理配置：将 /ai/api 请求代理到后端服务器，解决跨域问题
      // 自动匹配 base path（默认 /ai）+ /api
      proxy: {
        [`${canonicalBasePath}/api`]: {
          target: apiBaseUrl,
          changeOrigin: true,
          secure: false,
          rewrite: (path) => path.replace(new RegExp(`^${canonicalBasePath}`), ''),
        },
      },
    },
    preview: {
      host: true,
      port: 4173,
      strictPort: true,
    },
  };
});
