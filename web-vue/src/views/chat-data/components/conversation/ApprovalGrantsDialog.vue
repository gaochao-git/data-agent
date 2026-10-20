<script setup lang="ts">
/**
 * 本会话审批许可管理：逐项恢复审批 / 全部重置。
 * 许可存储在会话 checkpoint state 中，这里是唯一的前端管理入口。
 */
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getConversationApprovalGrants,
  resetConversationApprovalGrants,
  revokeConversationApprovalGrants,
} from '../../api'
import { getToolDisplayName } from '../../utils/toolDisplay'

const props = defineProps<{
  visible: boolean
  conversationId?: string | null
}>()
const emit = defineEmits<{ 'update:visible': [value: boolean] }>()

const grants = ref<string[]>([])
const loading = ref(false)
const loadError = ref(false)

const dialogVisible = ref(props.visible)
watch(() => props.visible, (value) => { dialogVisible.value = value })
watch(dialogVisible, (value) => { emit('update:visible', value) })

const grantLabel = (grant: string) => {
  const toolName = grant.startsWith('tool:') ? grant.slice(5) : grant
  const display = getToolDisplayName(toolName)
  return display === '工具调用' ? toolName : display
}

const loadGrants = async () => {
  if (!props.conversationId) return
  loading.value = true
  loadError.value = false
  try {
    const res = await getConversationApprovalGrants(props.conversationId)
    if (res.status === 'success') {
      grants.value = res.data.grants || []
    } else {
      loadError.value = true
    }
  } catch (err) {
    console.error('读取审批许可失败:', err)
    loadError.value = true
  } finally {
    loading.value = false
  }
}

watch(() => props.visible, async (value) => {
  if (value) await loadGrants()
})

const revokeGrant = async (grant: string) => {
  if (!props.conversationId) return
  try {
    // 传完整 grant key：后端同时兼容 tool:<name> 与旧版 mcp_server:<id>
    const res = await revokeConversationApprovalGrants(props.conversationId, [grant])
    if (res.status === 'success') {
      grants.value = res.data.grants || []
      ElMessage.success('已恢复该工具的审批')
    } else ElMessage.warning(res.msg || '撤销失败，请稍后重试')
  } catch (err) {
    console.error('撤销审批许可失败:', err)
    ElMessage.error('撤销审批许可失败，请稍后重试')
  }
}

const resetAll = async () => {
  if (!props.conversationId) return
  try {
    await ElMessageBox.confirm(
      '全部重置后，本会话内已授权的工具都会恢复为每次询问。',
      '确认重置审批许可',
      { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res = await resetConversationApprovalGrants(props.conversationId)
    if (res.status === 'success') {
      grants.value = []
      dialogVisible.value = false
      ElMessage.success(res.msg || '已重置本会话的审批许可')
    } else ElMessage.warning(res.msg || '重置失败，请稍后重试')
  } catch (err) {
    console.error('重置审批许可失败:', err)
    ElMessage.error('重置审批许可失败，请稍后重试')
  }
}
</script>

<template>
  <el-dialog v-model="dialogVisible" width="440px" title="本会话审批许可" append-to-body>
    <div v-loading="loading" class="min-h-[48px]">
      <p v-if="loading" class="text-sm text-slate-400">加载中…</p>
      <div v-else-if="loadError" class="flex items-center gap-2 text-sm text-red-500">
        许可加载失败，请稍后重试
        <button type="button" class="text-blue-600 hover:underline" @click="loadGrants">重试</button>
      </div>
      <p v-else-if="!grants.length" class="text-sm text-slate-400">当前没有已授权的工具</p>
      <div
        v-for="grant in grants"
        :key="grant"
        class="mb-2 flex items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-2"
      >
        <span class="text-sm text-slate-700">{{ grantLabel(grant) }}</span>
        <el-button link type="primary" size="small" @click="revokeGrant(grant)">恢复审批</el-button>
      </div>
    </div>
    <template #footer>
      <el-button @click="dialogVisible = false">关闭</el-button>
      <el-button type="danger" plain :disabled="!grants.length" @click="resetAll">全部重置</el-button>
    </template>
  </el-dialog>
</template>
