/**
 * Token 管理
 *
 * 与主项目 auth.js 保持一致，使用 js-cookie 存储 Admin-Token。
 * 集成到主项目后，直接使用主项目已有的 auth.js，无需复制此文件。
 */

import Cookies from 'js-cookie'

const TokenKey = 'Admin-Token'

export function getToken() {
  return Cookies.get(TokenKey)
}

export function setToken(token) {
  return Cookies.set(TokenKey, token)
}

export function removeToken() {
  Cookies.remove(TokenKey)
}
