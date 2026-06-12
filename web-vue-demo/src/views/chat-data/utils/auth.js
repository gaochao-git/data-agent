/**
 * ChatData 模块认证
 * 自动调用 inner_login 获取 inner_token cookie，用于 AI Agent 后端认证
 * 不侵入主项目 auth.js
 */

import request from '@/utils/request'

let _authPromise = null

/**
 * 初始化模块认证（自动调用 inner_login 获取 cookie）
 * - 集成到主项目后，主项目已有 Admin-Token，此函数仍可调用以获取 inner_token
 * - 多次调用只执行一次
 */
export function initChatDataAuth(options) {
  if (_authPromise) return _authPromise

  const userId = (options && options.userId ? String(options.userId) : '').trim()
  const userName = (options && options.userName ? String(options.userName) : '').trim()
  if (!userId || !userName) {
    console.error('[ChatData] 未获取到当前登录用户，无法初始化 inner_token')
    return Promise.resolve(false)
  }

  _authPromise = request({
    url: `/agents/api/inner_login`,
    method: 'get',
    params: {
      userid: userId,
      username: userName,
    },
    headers: {
      isToken: true,
    },
  })
    .then(() => {
      console.log('[ChatData] 自动登录成功')
      return true
    })
    .catch(err => {
      console.error('[ChatData] 自动登录异常:', err)
      _authPromise = null
      return false
    })

  return _authPromise
}

/**
 * 重置登录状态（允许重新登录）
 */
export function resetChatDataAuth() {
  _authPromise = null
}
