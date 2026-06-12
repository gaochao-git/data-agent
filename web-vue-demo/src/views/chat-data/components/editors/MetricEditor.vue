<script setup lang="ts">
/**
 * 指标编辑器弹窗
 */
import { ref, computed, watch } from 'vue'
import type { SemanticMetric, SemanticRelationship, TableInfo } from '../../types'

interface Props {
  visible: boolean
  metric: SemanticMetric
  tables: TableInfo[]
  relationships: SemanticRelationship[]
  isNew: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:visible': [value: boolean]
  save: [metric: SemanticMetric]
  cancel: []
}>()

// 本地编辑副本
const localMetric = ref<SemanticMetric>({ ...props.metric })

// 监听 metric 变化
watch(() => props.metric, (val) => {
  localMetric.value = { ...val }
}, { deep: true })

// 表单验证
const isFormValid = computed(() => {
  const m = localMetric.value
  return !!(m.name?.trim() && m.table && m.expression?.trim())
})

const handleSave = () => {
  if (!isFormValid.value) return
  emit('save', { ...localMetric.value })
}

const handleCancel = () => {
  emit('cancel')
  emit('update:visible', false)
}
</script>

<template>
  <el-dialog :model-value="visible" :title="isNew ? '添加指标' : '编辑指标'" width="520px" append-to-body :close-on-click-modal="true" @close="handleCancel">
    <div class="flex flex-col gap-3">
      <!-- 第一行：名称 + 所属表 -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">指标名称 <span class="text-red-500">*</span></label>
          <input v-model="localMetric.name" type="text" placeholder="如: 销售额、订单数" class="px-2.5 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-colors focus:border-blue-500" />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">所属表 <span class="text-red-500">*</span></label>
          <el-select v-model="localMetric.table" filterable placeholder="选择表">
            <el-option v-for="t in tables" :key="t.table_name" :value="t.table_name" :label="t.description && t.description !== t.table_name ? `${t.table_name} (${t.description})` : t.table_name" />
          </el-select>
        </div>
      </div>

      <!-- 第二行：聚合表达式 + 过滤条件 -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">聚合表达式 <span class="text-red-500">*</span></label>
          <input v-model="localMetric.expression" type="text" placeholder="如: COUNT(*), SUM(amount)" class="px-2.5 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-colors focus:border-blue-500 font-mono" />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">默认过滤条件</label>
          <input v-model="localMetric.filter" type="text" placeholder="如: is_active = 1" class="px-2.5 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-colors focus:border-blue-500 font-mono" />
        </div>
      </div>

      <!-- 第三行：描述 -->
      <div class="flex flex-col gap-1">
        <label class="text-xs font-medium text-gray-700">描述</label>
        <input v-model="localMetric.description" type="text" placeholder="指标说明（帮助 AI 理解这个指标的含义）" class="px-2.5 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-colors focus:border-blue-500" />
      </div>

    </div>

    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
      <el-button type="primary" :disabled="!isFormValid" @click="handleSave">确定</el-button>
    </template>
  </el-dialog>
</template>
