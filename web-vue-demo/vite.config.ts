import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const baseUrl = 'http://127.0.0.1:8080' // 后端接口
const agentUrl = 'http://127.0.0.1:5002' // AI Agent 后端接口

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

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
          target: agentUrl,
          changeOrigin: true,
          //外网配置
          rewrite: (path) => path.replace(/^\/dev-api\/agents/, ''),
          //内网配
          //rewrite: (path) => path.replace(/^\/dev-api/, ''),
        },
        // 业务后端
        '/dev-api': {
          target: baseUrl,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/dev-api/, ''),
        },
      },
    },
  }
})
