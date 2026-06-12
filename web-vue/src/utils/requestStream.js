/**
 * 流式请求封装
 * 用于 SSE 等流式响应场景，和 request 保持一致的调用风格
 */

import { getToken } from './auth'

const BASE_PREFIX = import.meta.env.VITE_APP_BASE_API

export function requestStream(options) {
  let url = `${BASE_PREFIX}${options.url}`

  // 处理 query params
  if (options.params) {
    const searchParams = new URLSearchParams()
    Object.entries(options.params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) {
        searchParams.append(k, String(v))
      }
    })
    const queryString = searchParams.toString()
    if (queryString) {
      url += `?${queryString}`
    }
  }

  const headers = {
    ...options.headers,
  }

  // 添加 token
  const token = getToken()
  if (token) {
    headers['Authorization'] = 'Bearer ' + token
  }

  if (options.data !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  return fetch(url, {
    method: options.method || 'GET',
    credentials: 'include',
    headers,
    body: options.data !== undefined ? JSON.stringify(options.data) : undefined,
    signal: options.signal,
  })
}

export default requestStream
