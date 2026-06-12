// vue.config.js
const { defineConfig } = require('@vue/cli-service')

module.exports = defineConfig({
  transpileDependencies: true,
  lintOnSave: false,
  devServer: {
    port: 8081, // 自定义端口（避免与本机8080冲突）
    open: true, // 自动打开浏览器
    hot: true, // 热更新
    host: 'localhost',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5001',
        changeOrigin: true
      }
    }
  },
  css: {}
})
