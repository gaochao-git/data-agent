<script setup lang="ts">
/**
 * 连接方式编辑弹窗
 * 编辑两张表之间的 join 类型与关联字段条件
 */
import {
  getRelationshipConditions,
  syncRelationshipColumnsFromConditions,
} from '../../utils'
import type { SemanticRelationship } from '../../types'

interface RelationshipScopeEntryOption {
  alias: string
  table: string
  label: string
}

const props = defineProps<{
  scopeEntries: RelationshipScopeEntryOption[]
  columnsByAlias: Record<string, string[]>
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

const visible = defineModel<boolean>('visible', { default: false })
const relationship = defineModel<SemanticRelationship | null>('relationship', { default: null })

const handleLeftAliasChange = () => {
  const rel = relationship.value
  if (!rel) return
  const entry = props.scopeEntries.find(item => item.alias === rel.left_alias)
  rel.left_table = entry?.table || ''
  ;(rel.join_conditions || []).forEach(c => { c.left_column = '' })
}

const handleRightAliasChange = () => {
  const rel = relationship.value
  if (!rel) return
  const entry = props.scopeEntries.find(item => item.alias === rel.right_alias)
  rel.right_table = entry?.table || ''
  ;(rel.join_conditions || []).forEach(c => { c.right_column = '' })
}

const addJoinCondition = () => {
  const rel = relationship.value
  if (!rel) return
  const next = getRelationshipConditions(rel).map(c => ({ ...c }))
  next.push({ left_column: '', right_column: '' })
  rel.join_conditions = next
  syncRelationshipColumnsFromConditions(rel)
}

const removeJoinCondition = (index: number) => {
  const rel = relationship.value
  if (!rel) return
  const current = getRelationshipConditions(rel).map(c => ({ ...c }))
  if (current.length <= 1) return
  current.splice(index, 1)
  rel.join_conditions = current
  syncRelationshipColumnsFromConditions(rel)
}
</script>

<template>
  <el-dialog v-model="visible" width="820px" title="连接方式" @close="emit('cancel')">
    <div v-if="relationship" class="overflow-x-auto">
      <div class="min-w-[680px] space-y-4">
      <div class="flex items-center gap-2">
        <button
          :class="[
            'px-3 py-1.5 rounded border text-xs flex items-center gap-1.5',
            relationship.join_type === 'inner' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 text-gray-600'
          ]"
          @click="relationship.join_type = 'inner'"
        >
          <span>内连接</span>
          <span class="text-[10px] opacity-70">INNER</span>
        </button>
        <button
          :class="[
            'px-3 py-1.5 rounded border text-xs flex items-center gap-1.5',
            relationship.join_type === 'left' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 text-gray-600'
          ]"
          @click="relationship.join_type = 'left'"
        >
          <span>左连接</span>
          <span class="text-[10px] opacity-70">LEFT</span>
        </button>
        <button
          :class="[
            'px-3 py-1.5 rounded border text-xs flex items-center gap-1.5',
            relationship.join_type === 'right' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 text-gray-600'
          ]"
          @click="relationship.join_type = 'right'"
        >
          <span>右连接</span>
          <span class="text-[10px] opacity-70">RIGHT</span>
        </button>
      </div>
      <div class="grid grid-cols-[1fr_auto_1fr] gap-2 items-center">
        <select
          v-model="relationship.left_alias"
          class="px-2.5 py-2 border border-gray-200 rounded text-sm"
          @change="handleLeftAliasChange"
        >
          <option v-for="entry in scopeEntries" :key="entry.alias" :value="entry.alias">{{ entry.label }}</option>
        </select>
        <div class="text-sm text-gray-400">→</div>
        <select
          v-model="relationship.right_alias"
          class="px-2.5 py-2 border border-gray-200 rounded text-sm"
          @change="handleRightAliasChange"
        >
          <option v-for="entry in scopeEntries" :key="entry.alias" :value="entry.alias">{{ entry.label }}</option>
        </select>
      </div>
      <div class="rounded-md border border-gray-200 bg-gray-50/50 p-3">
        <div class="mb-2 flex items-center justify-between">
          <div class="text-xs font-medium text-gray-600">关联字段</div>
          <button
            class="px-2 py-1 border border-gray-200 rounded bg-white text-gray-600 text-xs hover:border-blue-200 hover:text-blue-600"
            @click="addJoinCondition"
          >
            <i class="fa-solid fa-plus mr-1"></i>新增条件
          </button>
        </div>
        <div
          v-for="(cond, condIndex) in (relationship.join_conditions || [])"
          :key="`join-cond-${condIndex}`"
          class="space-y-1.5"
        >
          <div v-if="condIndex > 0" class="text-[11px] font-semibold text-blue-500">AND</div>
          <div class="grid grid-cols-[1fr_auto_1fr_auto] gap-2 items-center">
            <select v-model="cond.left_column" class="px-2.5 py-2 border border-gray-200 rounded text-sm bg-white">
              <option value="">请选择左表字段</option>
              <option v-for="col in (columnsByAlias[relationship.left_alias || ''] || [])" :key="col" :value="col">{{ col }}</option>
            </select>
            <div class="text-sm font-semibold text-gray-500">=</div>
            <select v-model="cond.right_column" class="px-2.5 py-2 border border-gray-200 rounded text-sm bg-white">
              <option value="">请选择右表字段</option>
              <option v-for="col in (columnsByAlias[relationship.right_alias || ''] || [])" :key="col" :value="col">{{ col }}</option>
            </select>
            <button
              class="w-8 h-8 border border-gray-200 rounded bg-white text-gray-400 hover:text-red-500 hover:border-red-200 disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="(relationship.join_conditions || []).length <= 1"
              @click="removeJoinCondition(condIndex)"
            >
              <i class="fa-solid fa-minus text-[11px]"></i>
            </button>
          </div>
        </div>
      </div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <button class="px-3 py-1.5 rounded border border-gray-200 bg-white text-gray-600" @click="visible = false">取消</button>
        <button class="px-3 py-1.5 rounded border-none bg-blue-500 text-white" @click="emit('confirm')">确定</button>
      </div>
    </template>
  </el-dialog>
</template>
