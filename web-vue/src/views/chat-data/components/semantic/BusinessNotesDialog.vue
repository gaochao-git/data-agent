<script setup lang="ts">
import { ref, watch } from 'vue'
import type { SemanticConfigItem } from '../../types'

const props = defineProps<{
  config: SemanticConfigItem | null
  canManage: boolean
}>()

const emit = defineEmits<{
  save: [businessNotes: string]
}>()

const visible = defineModel<boolean>('visible', { default: false })
const editingBusinessFlowText = ref('')

const splitBusinessNotes = (text: string) => {
  const content = (text || '').trim()
  if (!content) return { businessFlow: '' }
  const businessMatch = content.match(/【业务运转说明】\s*([\s\S]*?)(?=【元数据补充】|$)/)
  if (businessMatch) {
    return {
      businessFlow: (businessMatch?.[1] || '').trim(),
    }
  }
  if (content.includes('【元数据补充】')) {
    return { businessFlow: '' }
  }
  return { businessFlow: content }
}

const buildBusinessNotesText = () => {
  return editingBusinessFlowText.value.trim()
}

watch(visible, (value) => {
  if (!value) return
  const parsed = splitBusinessNotes(props.config?.business_notes || '')
  editingBusinessFlowText.value = parsed.businessFlow
})

const save = () => {
  if (!props.config) return
  emit('save', buildBusinessNotesText())
  visible.value = false
}
</script>

<template>
  <el-dialog v-model="visible" width="760px" title="业务说明">
    <div class="space-y-2">
      <div class="text-[13px] font-medium text-gray-700">业务运转说明</div>
      <div class="min-h-[40px] text-[12px] leading-5 text-gray-500">描述这个业务是怎么工作的，例如订单、支付、发货、退款等业务过程和对象关系。</div>
      <textarea
        v-model="editingBusinessFlowText"
        wrap="off"
        class="h-[320px] w-full overflow-auto whitespace-pre px-3 py-2 border border-gray-200 rounded-md text-[13px] leading-relaxed text-gray-700 outline-none resize-none focus:border-blue-500"
        placeholder="例如：用户下单后生成 orders 记录，订单可包含多个 order_items；支付成功后记录支付时间，退款订单不计入有效成交。"
      ></textarea>
    </div>
    <template #footer>
      <button
        class="px-3 py-1.5 border border-gray-200 rounded bg-white text-xs text-gray-600 hover:bg-gray-50"
        @click="visible = false"
      >
        取消
      </button>
      <button
        class="ml-2 px-3 py-1.5 border-none rounded bg-[#1677FF] text-xs text-white hover:bg-[#0958D9] disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="!canManage"
        title="保存业务说明"
        @click="save"
      >
        保存
      </button>
    </template>
  </el-dialog>
</template>
