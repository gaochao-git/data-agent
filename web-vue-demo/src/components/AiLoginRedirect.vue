<script setup lang="ts">
/**
 * 自动登录重定向组件
 * 从 URL 参数读取 id 和 name，调用 /api/inner_login 完成登录
 */
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useAutoLogin } from '@/composables/useAutoLoginParams'

type Status = 'idle' | 'loading' | 'success' | 'error'

const { params, performAutoLogin, handleRedirect } = useAutoLogin()

const status = ref<Status>('idle')
const errorMessage = ref<string | null>(null)

let abortController: AbortController | null = null

const runLogin = async () => {
  const { userId, userName } = params.value

  if (!userId || !userName) {
    status.value = 'error'
    errorMessage.value = '缺少 id 或 name 参数，无法完成登录。'
    return
  }

  abortController = new AbortController()
  status.value = 'loading'

  const result = await performAutoLogin(userId, userName, { signal: abortController.signal })

  if (result.success) {
    status.value = 'success'
    handleRedirect()
  } else {
    status.value = 'error'
    errorMessage.value = result.error || '登录失败'
  }
}

onMounted(() => {
  runLogin()
})

onUnmounted(() => {
  abortController?.abort()
})

// 监听参数变化，重新执行登录
watch(
  () => params.value,
  () => {
    if (status.value !== 'loading') {
      runLogin()
    }
  }
)
</script>

<template>
  <div class="login-redirect">
    <div class="login-card">
      <template v-if="status === 'loading'">
        <div class="loading-spinner"></div>
        <p>正在为 {{ params.userName || '用户' }} 初始化会话，请稍候…</p>
      </template>

      <template v-else-if="status === 'error'">
        <div class="error-icon">
          <i class="fa-solid fa-circle-exclamation"></i>
        </div>
        <p class="error-title">登录失败</p>
        <p class="error-message">{{ errorMessage }}</p>
      </template>

      <template v-else-if="status === 'idle'">
        <p>准备登录…</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.login-redirect {
  display: flex;
  height: 100vh;
  align-items: center;
  justify-content: center;
  background-color: #f9fafb;
  color: #374151;
}

.login-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 32px 48px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}

.loading-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e5e7eb;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.error-icon {
  font-size: 32px;
  color: #dc2626;
}

.error-title {
  font-size: 16px;
  font-weight: 600;
  color: #dc2626;
  margin: 0;
}

.error-message {
  font-size: 14px;
  color: #6b7280;
  margin: 0;
}
</style>
