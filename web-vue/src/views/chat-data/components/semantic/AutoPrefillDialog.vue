<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { parseSemanticPrefillContextFile } from '../../api'

defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [businessContext: string]
}>()

const visible = defineModel<boolean>('visible', { default: false })

const autoPrefillBusinessContext = ref('')
const autoPrefillContextFileInputRef = ref<HTMLInputElement | null>(null)
const parsingPrefillContextFile = ref(false)

watch(visible, (value) => {
  if (value) autoPrefillBusinessContext.value = ''
})

const appendAutoPrefillBusinessContext = (text: string, filename?: string) => {
  const normalized = (text || '').trim()
  if (!normalized) return
  const prefix = filename ? `【${filename}】\n` : ''
  const current = autoPrefillBusinessContext.value.trim()
  autoPrefillBusinessContext.value = (current ? `${current}\n\n${prefix}${normalized}` : `${prefix}${normalized}`).slice(0, 20000)
}

const selectAutoPrefillContextFiles = () => {
  autoPrefillContextFileInputRef.value?.click()
}

const handleAutoPrefillContextFilesChange = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (files.length === 0) return
  const oversized = files.find(file => file.size > 10 * 1024 * 1024)
  if (oversized) {
    ElMessage.warning(`文件不能超过 10MB：${oversized.name}`)
    return
  }
  parsingPrefillContextFile.value = true
  try {
    let successCount = 0
    for (const file of files) {
      const res = await parseSemanticPrefillContextFile(file)
      if (res.status !== 'success') {
        ElMessage.warning(res.msg || `解析失败：${file.name}`)
        continue
      }
      appendAutoPrefillBusinessContext(res.data?.text || '', res.data?.filename || file.name)
      successCount += 1
    }
    if (successCount > 0) {
      ElMessage.success(`已上传并解析 ${successCount} 个文件`)
    }
  } catch (err) {
    ElMessage.error('解析文件失败')
  } finally {
    parsingPrefillContextFile.value = false
  }
}

const handleSubmit = () => {
  emit('submit', autoPrefillBusinessContext.value.trim())
}
</script>

<template>
  <el-dialog v-model="visible" width="820px" title="自动预填">
    <div class="space-y-4">
      <div class="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-sm leading-6 text-blue-700">
        将所选表的物理字段预填为维度，并结合字段说明和业务补充推荐最多 10 个常用原子指标。
      </div>
      <div>
        <div class="mb-2 flex items-center justify-between">
          <span class="text-sm font-medium text-gray-700">业务补充</span>
          <div class="flex items-center gap-2">
            <input
              ref="autoPrefillContextFileInputRef"
              class="hidden"
              type="file"
              multiple
              accept=".txt,.docx,.pdf"
              @change="handleAutoPrefillContextFilesChange"
            />
            <el-button size="small" :loading="parsingPrefillContextFile" @click="selectAutoPrefillContextFiles">
              <i class="fa-solid fa-paperclip mr-1"></i>
              上传文件
            </el-button>
          </div>
        </div>
        <el-input
          v-model="autoPrefillBusinessContext"
          type="textarea"
          :rows="8"
          maxlength="20000"
          show-word-limit
          placeholder="可补充业务口径、常分析的字段、金额/数量等指标习惯。支持上传 txt、docx、pdf 文件后追加到这里。"
        />
        <div class="mt-1 text-xs text-gray-400">文件仅用于本次解析，不会保存；单个文件不能超过 10MB。</div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="handleSubmit">开始预填</el-button>
      </div>
    </template>
  </el-dialog>
</template>
