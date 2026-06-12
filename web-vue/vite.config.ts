import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const baseUrl = 'http://127.0.0.1:8080' // 后端接口
const agentUrl = 'http://127.0.0.1:5001' // AI Agent 后端接口

function buildAgentProxyHeaders(env: Record<string, string>) {
  const headers: Record<string, string> = {}
  if (env.AGENT_SERVICE_TOKEN) headers['X-Service-Token'] = env.AGENT_SERVICE_TOKEN
  if (env.AGENT_USER_ID) headers['X-User-Id'] = env.AGENT_USER_ID
  if (env.AGENT_USERNAME) headers['X-Username'] = env.AGENT_USERNAME
  return headers
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const agentProxyHeaders = buildAgentProxyHeaders(env)

  return {
    base: '/',
    plugins: [
      vue(),
      vueDevTools(),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      },
    },
    server: {
      port: 8089,
      host: true,
      open: false,
      proxy: {
        // AI Agent 后端（写在 /dev-api 前面，优先匹配）
        '/dev-api/agents': {
          target: env.VITE_API_BASE_URL_AGENTS || agentUrl,
          changeOrigin: true,
          headers: agentProxyHeaders,
          //外网配置
          rewrite: (path) => path.replace(/^\/dev-api\/agents/, ''),
          //内网配
          //rewrite: (path) => path.replace(/^\/dev-api/, ''),
        },
        // 业务后端
        '/dev-api': {
          target: env.VITE_API_BASE_URL || baseUrl,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/dev-api/, ''),
        },
      },
    },
  }
})
