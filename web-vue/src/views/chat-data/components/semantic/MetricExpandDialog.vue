<script setup lang="ts">
import type { MetricExpandOption } from '../../types'

const emit = defineEmits<{
  confirm: []
}>()

const visible = defineModel<boolean>('visible', { default: false })
const options = defineModel<MetricExpandOption[]>('options', { default: () => [] })

const handleConfirm = () => {
  emit('confirm')
}
</script>

<template>
  <el-dialog v-model="visible" width="640px" title="扩展聚合指标">
    <div class="space-y-3">
      <div class="text-xs leading-5 text-gray-500">
        基于当前物理字段生成其他聚合指标，已存在的同字段同聚合不会重复生成。
      </div>
      <div class="overflow-hidden rounded-md border border-gray-200">
        <table class="w-full border-collapse text-[12px]">
          <thead class="bg-gray-50 text-gray-600">
            <tr>
              <th class="w-[52px] border border-gray-200 px-3 py-2 text-center">选择</th>
              <th class="border border-gray-200 px-3 py-2 text-left">表达式</th>
              <th class="border border-gray-200 px-3 py-2 text-left">字段名</th>
              <th class="border border-gray-200 px-3 py-2 text-left">显示名称</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="option in options" :key="option.aggFunc" :class="option.exists ? 'bg-gray-50 text-gray-400' : ''">
              <td class="border border-gray-200 px-3 py-2 text-center">
                <input v-model="option.selected" type="checkbox" :disabled="option.exists" />
              </td>
              <td class="border border-gray-200 px-3 py-2 font-mono">{{ option.label }}</td>
              <td class="border border-gray-200 px-3 py-2">
                <el-input v-model="option.fieldName" size="small" :disabled="option.exists" />
              </td>
              <td class="border border-gray-200 px-3 py-2">
                <div class="flex items-center gap-2">
                  <el-input v-model="option.displayName" size="small" :disabled="option.exists" />
                  <span v-if="option.exists" class="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">已存在</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="handleConfirm">确定</el-button>
      </div>
    </template>
  </el-dialog>
</template>
