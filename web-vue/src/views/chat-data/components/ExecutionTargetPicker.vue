<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { ExecutionTargetOption } from '../types'

interface Props {
  modelValue: string[]
  targets: ExecutionTargetOption[]
  multiple?: boolean
  compact?: boolean
  fullWidth?: boolean
  hideIcon?: boolean
  inline?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  multiple: true,
  compact: false,
  fullWidth: false,
  hideIcon: false,
  inline: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string[]]
}>()

const visible = ref(false)
const searchText = ref('')
const pendingSelectedIds = ref<string[]>([])
const selectedIdSet = computed(() => new Set(pendingSelectedIds.value))
const allSelected = computed(() => props.targets.length > 0 && pendingSelectedIds.value.length === props.targets.length)
const filteredTargets = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  if (!keyword) return props.targets
  return props.targets.filter((item) => {
    const label = String(item.org_name || item.label || '').toLowerCase()
    return label.includes(keyword)
  })
})

const selectedSummary = computed(() => {
  const selectedTargets = props.targets.filter(item => props.modelValue.includes(item.member_datasource_id))
  if (selectedTargets.length === 0) return '未选择'
  if (selectedTargets.length === 1) return selectedTargets[0]?.label || '已选择 1 个'
  return `${selectedTargets[0]?.label || '已选择'} 等 ${selectedTargets.length} 个`
})

const syncPendingSelection = () => {
  const matchedIds = props.modelValue.filter(id =>
    props.targets.some(item => item.member_datasource_id === id)
  )
  pendingSelectedIds.value = props.multiple ? matchedIds : matchedIds.slice(0, 1)
}

watch(
  () => [props.modelValue.join(','), props.targets.map(item => item.member_datasource_id).join(',')],
  () => {
    syncPendingSelection()
  },
  { immediate: true }
)

const openDialog = () => {
  syncPendingSelection()
  searchText.value = ''
  visible.value = true
}

const handleToggleSelect = (datasourceId: string, checked: boolean) => {
  let nextValue: string[] = []
  if (checked && !props.multiple) {
    nextValue = [datasourceId]
  } else if (checked) {
    nextValue = Array.from(new Set([...pendingSelectedIds.value, datasourceId]))
  } else {
    nextValue = pendingSelectedIds.value.filter(id => id !== datasourceId)
  }
  pendingSelectedIds.value = nextValue
  if (props.inline) {
    emit('update:modelValue', nextValue)
  }
}

const handleSelectAll = () => {
  if (!props.multiple) return
  if (props.targets.length === 0) return
  const nextValue = props.targets.map(item => item.member_datasource_id)
  pendingSelectedIds.value = nextValue
  if (props.inline) {
    emit('update:modelValue', nextValue)
  }
}

const handleClearAll = () => {
  pendingSelectedIds.value = []
  if (props.inline) {
    emit('update:modelValue', [])
  }
}

const toggleRowSelection = (datasourceId: string) => {
  if (!props.multiple) {
    handleToggleSelect(datasourceId, true)
    return
  }
  handleToggleSelect(datasourceId, !selectedIdSet.value.has(datasourceId))
}

const handleConfirm = () => {
  if (props.targets.length > 0 && pendingSelectedIds.value.length === 0) {
    ElMessage.warning('请选择执行对象')
    return
  }
  emit('update:modelValue', pendingSelectedIds.value)
  visible.value = false
}
</script>

<template>
  <div v-if="inline">
    <div class="flex items-center justify-between mb-3">
      <div v-if="multiple" class="flex items-center gap-2">
        <button
          class="h-8 px-3 border rounded text-[12px] cursor-pointer transition-all duration-150 border-[#e5e7eb] bg-white text-[#374151] disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="targets.length === 0 || allSelected"
          @click="handleSelectAll"
        >
          全选
        </button>
        <button
          class="h-8 px-3 border rounded text-[12px] cursor-pointer transition-all duration-150 border-[#e5e7eb] bg-white text-[#374151] disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="pendingSelectedIds.length === 0"
          @click="handleClearAll"
        >
          清空
        </button>
      </div>
      <span class="text-[12px] text-[#6b7280]">
        {{ multiple ? `已选 ${pendingSelectedIds.length} 个` : (selectedSummary === '未选择' ? '未选择执行对象' : `当前：${selectedSummary}`) }}
      </span>
    </div>

    <div class="mb-3 flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 h-9">
      <i class="fa-solid fa-magnifying-glass text-[12px] text-slate-400"></i>
      <input
        v-model="searchText"
        type="text"
        class="flex-1 border-none bg-transparent text-[13px] text-slate-700 outline-none"
        placeholder="搜索执行对象"
      />
    </div>

    <el-table :data="filteredTargets" border max-height="420" @row-click="(row: ExecutionTargetOption) => toggleRowSelection(row.member_datasource_id)">
      <el-table-column label="选择" width="72" align="center">
        <template #default="{ row }">
          <input
            :type="multiple ? 'checkbox' : 'radio'"
            :checked="selectedIdSet.has(row.member_datasource_id)"
            name="execution-target-inline"
            @click.stop
            @change="handleToggleSelect(row.member_datasource_id, ($event.target as HTMLInputElement).checked)"
          />
        </template>
      </el-table-column>
      <el-table-column prop="org_name" label="名称" min-width="320">
        <template #default="{ row }">
          {{ row.org_name || row.label }}
        </template>
      </el-table-column>
    </el-table>
  </div>

  <template v-else>
    <button
      :class="[
        'flex items-center gap-2 border border-[#e3e5eb] rounded bg-white text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]',
        compact ? 'h-[28px] px-2.5 text-[12px]' : 'h-[24px] px-3 text-[12px] max-w-[320px]',
        fullWidth ? 'w-full min-w-0' : ''
      ]"
      @click="openDialog"
    >
      <i v-if="!hideIcon" class="fa-solid fa-database text-[11px] text-[#6b7280]"></i>
      <span class="truncate">{{ selectedSummary }}</span>
      <i class="fa-solid fa-angle-down text-[10px] text-[#9ca3af]"></i>
    </button>

    <el-dialog v-model="visible" title="选择执行对象" width="520px" append-to-body>
      <div class="flex items-center justify-between mb-3">
        <div v-if="multiple" class="flex items-center gap-2">
          <button
            class="h-8 px-3 border rounded text-[12px] cursor-pointer transition-all duration-150 border-[#e5e7eb] bg-white text-[#374151] disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="targets.length === 0 || allSelected"
            @click="handleSelectAll"
          >
            全选
          </button>
          <button
            class="h-8 px-3 border rounded text-[12px] cursor-pointer transition-all duration-150 border-[#e5e7eb] bg-white text-[#374151] disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="pendingSelectedIds.length === 0"
            @click="handleClearAll"
          >
            清空
          </button>
        </div>
        <span class="text-[12px] text-[#6b7280]">
          {{ multiple ? `已选 ${pendingSelectedIds.length} 个` : (selectedSummary === '未选择' ? '未选择执行对象' : `当前：${selectedSummary}`) }}
        </span>
      </div>

      <div class="mb-3 flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 h-9">
        <i class="fa-solid fa-magnifying-glass text-[12px] text-slate-400"></i>
        <input
          v-model="searchText"
          type="text"
          class="flex-1 border-none bg-transparent text-[13px] text-slate-700 outline-none"
          placeholder="搜索执行对象"
        />
      </div>

      <el-table :data="filteredTargets" border max-height="420" @row-click="(row: ExecutionTargetOption) => toggleRowSelection(row.member_datasource_id)">
        <el-table-column label="选择" width="72" align="center">
          <template #default="{ row }">
            <input
              :type="multiple ? 'checkbox' : 'radio'"
              :checked="selectedIdSet.has(row.member_datasource_id)"
              name="execution-target-dialog"
              @click.stop
              @change="handleToggleSelect(row.member_datasource_id, ($event.target as HTMLInputElement).checked)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="org_name" label="名称" min-width="320">
          <template #default="{ row }">
            {{ row.org_name || row.label }}
          </template>
        </el-table-column>
      </el-table>

      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="handleConfirm">确定</el-button>
      </template>
    </el-dialog>
  </template>
</template>
