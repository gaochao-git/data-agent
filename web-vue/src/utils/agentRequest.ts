/**
 * AI Agent 后端请求工具
 * 专门用于调用 AI Agent 服务（5001端口）
 */

import axios from 'axios'
import { ElMessage } from 'element-plus'

// 创建 axios 实例
const service = axios.create({
  baseURL: '', // 使用代理，不需要完整 URL
  timeout: 600000,
  withCredentials: true,
})

// 请求拦截器
service.interceptors.request.use(
  (config) => {
    return config
  },
  (error) => {
    console.log(error)
    return Promise.reject(error)
  }
)

// 响应拦截器
service.interceptors.response.use(
  (res) => {
    // 二进制数据则直接返回
    if (res.request.responseType === 'blob' || res.request.responseType === 'arraybuffer') {
      return res.data
    }

    // 兼容后端返回格式
    if (res.data.status === 'success') {
      return res.data
    }

    // 检查是否有错误
    if (res.data.code && res.data.code !== 200) {
      const msg = res.data.msg || res.data.message || '请求失败'
      ElMessage.error(msg)
      return Promise.reject(new Error(msg))
    }

    return res.data
  },
  (error) => {
    console.log('err:', error)
    let message = '请求失败'

    if (error.response) {
      if (error.response.status === 401) {
        message = '未授权，请先登录'
      } else if (error.response.status === 404) {
        message = '请求的资源不存在'
      } else if (error.response.status === 500) {
        message = '服务器错误'
      } else {
        message = error.response.data?.msg || error.response.data?.message || `请求失败 (${error.response.status})`
      }
    } else if (error.message) {
      message = error.message
    }

    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default service
