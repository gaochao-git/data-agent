/**
 * localStorage 读写助手
 * 嵌入宿主可能禁用 storage（隐私模式 / 沙箱 iframe），任何异常都降级为默认值，
 * 保证偏好记忆失效时不阻断页面功能。
 */

export const readLocalStorage = (key: string): string | null => {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export const writeLocalStorage = (key: string, value: string): void => {
  try {
    localStorage.setItem(key, value)
  } catch {
    // 写入失败只丢失偏好记忆，不影响功能
  }
}
