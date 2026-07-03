<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createQueryFavorite } from '../api'

type FavoriteMode = 'sql' | 'builder'

interface Props {
  visible: boolean
  mode: FavoriteMode
  datasourceId: string
  semanticConfigId?: string | null
  initialTitle?: string
  initialContent?: string
  initialBuilderConfig?: Record<string, unknown> | null
}

const props = withDefaults(defineProps<Props>(), {
  semanticConfigId: null,
  initialTitle: '',
  initialContent: '',
  initialBuilderConfig: null
})

const emit = defineEmits<{
  'update:visible': [value: boolean]
  saved: []
}>()

const formTitle = ref('')
const formContent = ref('')
const saving = ref(false)

const isBuilder = computed(() => props.mode === 'builder')
const dialogTitle = computed(() => {
  if (props.mode === 'builder') return '收藏可视化查询'
  return '收藏 SQL 查询'
})

watch(
  () => props.visible,
  (visible) => {
    if (!visible) return
    formTitle.value = (props.initialTitle || '').trim()
    formContent.value = props.initialContent || ''
  },
  { immediate: true }
)

const closeDialog = () => {
  emit('update:visible', false)
}

const handleSubmit = async () => {
  const favoriteTitle = formTitle.value.trim()
  if (!favoriteTitle) {
    ElMessage.warning('请输入收藏标题')
    return
  }
  if (!props.datasourceId) {
    ElMessage.warning('请先选择数据源')
    return
  }
  if (!isBuilder.value && !formContent.value.trim()) {
    ElMessage.warning('原始内容不能为空')
    return
  }
  if (isBuilder.value && !props.initialBuilderConfig) {
    ElMessage.warning('当前可视化查询配置为空，无法收藏')
    return
  }

  saving.value = true
  try {
    await createQueryFavorite({
      datasource_id: props.datasourceId,
      query_type: props.mode,
      favorite_title: favoriteTitle,
      query_text: isBuilder.value ? (props.initialContent || '可视化查询配置') : formContent.value,
      builder_config: isBuilder.value ? (props.initialBuilderConfig || undefined) : undefined,
      semantic_config_id: props.semanticConfigId || undefined,
    })
    ElMessage.success('收藏成功')
    emit('saved')
    closeDialog()
  } catch (err: any) {
    const apiMsg = err?.response?.data?.msg
    ElMessage.error(apiMsg || '收藏失败，请稍后重试')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="dialogTitle"
    width="640px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:visible', $event)"
  >
    <div class="flex flex-col gap-3">
      <div>
        <div class="text-[13px] text-[#4b5563] mb-1.5">收藏标题</div>
        <el-input v-model="formTitle" maxlength="255" placeholder="请输入收藏标题" />
      </div>
      <div>
        <div class="text-[13px] text-[#4b5563] mb-1.5">原始内容</div>
        <el-input
          v-model="formContent"
          type="textarea"
          :rows="8"
          resize="vertical"
          :readonly="isBuilder"
          :placeholder="isBuilder ? '可视化查询原始配置不可编辑' : '请输入原始内容'"
        />
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="closeDialog">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSubmit">收藏</el-button>
      </div>
    </template>
  </el-dialog>
</template>
