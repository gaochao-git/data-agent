/**
 * 自动登录参数提取和登录处理
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

/**
 * 从 URL 查询参数中提取用户信息
 */
export function extractUserParams(query: Record<string, string | string[] | undefined>) {
  const rawId = (query.id ?? query.userid ?? '') as string
  const rawName = (query.name ?? query.username ?? '') as string
  const redirect = (query.redirect ?? '') as string

  return {
    userId: rawId.trim(),
    userName: rawName.trim(),
    redirect: redirect.trim(),
  }
}

/**
 * 组合式函数：获取 URL 中的自动登录参数
 */
export function useAutoLoginParams() {
  const route = useRoute()

  return computed(() => extractUserParams(route.query as Record<string, string | undefined>))
}

/**
 * 执行自动登录
 */
export async function performAutoLogin(
  userId: string,
  userName: string,
  options?: { signal?: AbortSignal }
): Promise<{ success: boolean; error?: string }> {
  const target = `${API_BASE}/api/inner_login?userid=${encodeURIComponent(userId)}&username=${encodeURIComponent(userName)}`

  try {
    const response = await fetch(target, {
      method: 'GET',
      credentials: 'include',
      signal: options?.signal,
    })

    if (!response.ok) {
      const detail = await response.text()
      throw new Error(detail || `登录失败（${response.status}）`)
    }

    return { success: true }
  } catch (error) {
    if (options?.signal?.aborted) {
      return { success: false, error: '请求已取消' }
    }
    const message = error instanceof Error ? error.message : '登录失败，请稍后重试。'
    return { success: false, error: message }
  }
}

/**
 * 组合式函数：自动登录逻辑
 */
export function useAutoLogin() {
  const route = useRoute()
  const router = useRouter()
  const params = useAutoLoginParams()

  const needsLogin = computed(() => {
    const { userId, userName } = params.value
    return !!(userId && userName)
  })

  const clearLoginParams = () => {
    // 移除登录相关的查询参数
    const newQuery = { ...route.query }
    delete newQuery.id
    delete newQuery.userid
    delete newQuery.name
    delete newQuery.username
    delete newQuery.redirect

    router.replace({ query: newQuery })
  }

  const handleRedirect = () => {
    const { redirect } = params.value
    if (redirect && redirect.startsWith('/')) {
      router.replace(redirect)
    } else {
      clearLoginParams()
    }
  }

  return {
    params,
    needsLogin,
    performAutoLogin,
    clearLoginParams,
    handleRedirect,
  }
}
