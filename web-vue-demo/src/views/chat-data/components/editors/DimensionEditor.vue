<script setup lang="ts">
/**
 * 维度编辑器弹窗
 */
import { ref, computed, watch } from 'vue'
import type { SemanticDimension, TableInfo } from '../../types'

interface Props {
  visible: boolean
  dimension: SemanticDimension
  tables: TableInfo[]
  isNew: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:visible': [value: boolean]
  save: [dimension: SemanticDimension]
  cancel: []
}>()

// 本地编辑副本
const localDimension = ref<SemanticDimension>({ ...props.dimension })

// 监听 dimension 变化
watch(() => props.dimension, (val) => {
  localDimension.value = { ...val }
}, { deep: true })

// 当前选中表的字段列表
const tableColumns = computed(() => {
  const table = props.tables.find(t => t.table_name === localDimension.value.table)
  return table?.columns || []
})

// 可选值
const allowedValuesInput = ref('')
const addAllowedValue = () => {
  const val = allowedValuesInput.value.trim()
  if (!val) return
  // 支持逗号分隔批量添加
  const items = val.split(',').map(v => v.trim()).filter(Boolean)
  const existing = localDimension.value.allowed_values || []
  const newValues = items.filter(v => !existing.includes(v))
  if (newValues.length > 0) {
    localDimension.value.allowed_values = [...existing, ...newValues]
  }
  allowedValuesInput.value = ''
}
const removeAllowedValue = (index: number) => {
  const arr = [...(localDimension.value.allowed_values || [])]
  arr.splice(index, 1)
  localDimension.value.allowed_values = arr.length > 0 ? arr : undefined
}

const handleTableChange = () => {
  localDimension.value.column = ''
}

// 表单验证
const isFormValid = computed(() => {
  const d = localDimension.value
  return !!(d.name?.trim() && d.table && d.column)
})

const handleSave = () => {
  if (!isFormValid.value) return
  emit('save', { ...localDimension.value })
}

const handleCancel = () => {
  emit('cancel')
  emit('update:visible', false)
}
</script>

<template>
  <el-dialog :model-value="visible" :title="isNew ? '添加维度' : '编辑维度'" width="480px" append-to-body :close-on-click-modal="true" @close="handleCancel">
    <div class="flex flex-col gap-3">
      <!-- 第一行：名称 + 类型 -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">维度名称 <span class="text-red-500">*</span></label>
          <input v-model="localDimension.name" type="text" placeholder="如: 地区、日期" class="px-2.5 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-colors focus:border-blue-500" />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">类型</label>
          <el-select v-model="localDimension.type" placeholder="选择类型">
            <el-option value="categorical" label="分类" />
            <el-option value="time" label="时间" />
            <el-option value="number" label="数值" />
          </el-select>
        </div>
      </div>

      <!-- 第二行：所属表 + 字段 -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">所属表 <span class="text-red-500">*</span></label>
          <el-select v-model="localDimension.table" filterable placeholder="选择表" @change="handleTableChange">
            <el-option v-for="t in tables" :key="t.table_name" :value="t.table_name" :label="t.description && t.description !== t.table_name ? `${t.table_name} (${t.description})` : t.table_name" />
          </el-select>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-700">字段 <span class="text-red-500">*</span></label>
          <el-select v-model="localDimension.column" filterable placeholder="选择字段">
            <el-option v-for="col in tableColumns" :key="col.name" :value="col.name" :label="col.description ? `${col.name} (${col.description})` : col.name" />
          </el-select>
        </div>
      </div>

      <!-- 第三行：描述 -->
      <div class="flex flex-col gap-1">
        <label class="text-xs font-medium text-gray-700">描述</label>
        <input v-model="localDimension.description" type="text" placeholder="维度说明（帮助 AI 理解这个维度的含义）" class="px-2.5 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-colors focus:border-blue-500" />
      </div>

      <!-- 第四行：可选值 -->
      <div class="flex flex-col gap-1">
        <label class="text-xs font-medium text-gray-700">可选值</label>
        <div class="flex flex-wrap items-center gap-1.5 px-2.5 py-1.5 border border-gray-300 rounded-md transition-colors focus-within:border-blue-500 min-h-[36px]">
          <span v-for="(val, idx) in localDimension.allowed_values" :key="idx" class="inline-flex items-center h-[22px] px-1.5 rounded bg-blue-50 text-blue-600 text-[12px]">
            {{ val }}
            <i class="fa-solid fa-xmark ml-1 text-[10px] text-blue-300 hover:text-blue-600 cursor-pointer" @click="removeAllowedValue(idx)"></i>
          </span>
          <input v-model="allowedValuesInput" type="text" placeholder="输入后回车添加" class="flex-1 min-w-[80px] h-[22px] border-none outline-none bg-transparent text-[13px]" @keydown.enter.prevent="addAllowedValue" />
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
      <el-button type="primary" :disabled="!isFormValid" @click="handleSave">确定</el-button>
    </template>
  </el-dialog>
</template>
