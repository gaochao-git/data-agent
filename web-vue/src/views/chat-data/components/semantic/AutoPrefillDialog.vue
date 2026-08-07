<script setup lang="ts">
import { ref, watch } from 'vue'

defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [businessContext: string]
}>()

const visible = defineModel<boolean>('visible', { default: false })

const autoPrefillBusinessContext = ref('')

watch(visible, (value) => {
  if (value) autoPrefillBusinessContext.value = ''
})

const handleSubmit = () => {
  emit('submit', autoPrefillBusinessContext.value.trim())
}
</script>

<template>
  <el-dialog v-model="visible" width="820px" title="自动预填">
    <div class="space-y-4">
      <div class="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-sm leading-6 text-blue-700">
        根据所选表的字段说明和业务补充优化显示名称，并推荐最多 10 个常用原子指标。
      </div>
      <div>
        <div class="mb-2 flex items-center justify-between">
          <span class="text-sm font-medium text-gray-700">业务补充</span>
        </div>
        <el-input
          v-model="autoPrefillBusinessContext"
          type="textarea"
          :rows="8"
          maxlength="20000"
          show-word-limit
          placeholder="可补充业务口径、常分析的字段、金额/数量等指标习惯。"
        />
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
