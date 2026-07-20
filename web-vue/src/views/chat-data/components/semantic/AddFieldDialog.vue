<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AddFieldForm, AddFieldTableOption } from '../../types'

const props = defineProps<{
  tables: AddFieldTableOption[]
}>()

const emit = defineEmits<{
  tableChange: []
  columnChange: []
  confirm: []
}>()

const visible = defineModel<boolean>('visible', { default: false })
const form = defineModel<AddFieldForm>('form', { required: true })

const addFieldLabelMode = ref<'name' | 'description'>('name')
const addFieldTableKeyword = ref('')
const addFieldColumnKeyword = ref('')

watch(visible, (value) => {
  if (!value) return
  addFieldTableKeyword.value = ''
  addFieldColumnKeyword.value = ''
})

const addFieldColumns = computed(() => {
  const entry = props.tables.find(t => t.alias === form.value.tableAlias)
  return entry?.columns || []
})

const filteredAddFieldTables = computed(() => {
  const keyword = addFieldTableKeyword.value.trim().toLowerCase()
  if (!keyword) return props.tables
  return props.tables.filter(entry =>
    entry.alias.toLowerCase().includes(keyword) ||
    entry.table.toLowerCase().includes(keyword) ||
    (entry.description || '').toLowerCase().includes(keyword)
  )
})

const filteredAddFieldColumns = computed(() => {
  const keyword = addFieldColumnKeyword.value.trim().toLowerCase()
  if (!keyword) return addFieldColumns.value
  return addFieldColumns.value.filter(c =>
    c.name.toLowerCase().includes(keyword) ||
    (c.description || '').toLowerCase().includes(keyword)
  )
})

const getAddFieldTableLabel = (entry: AddFieldTableOption) => {
  const base = addFieldLabelMode.value === 'description' ? (entry.description || entry.table) : entry.alias
  return entry.alias === entry.table ? base : `${base}（${entry.table}）`
}

const getAddFieldColumnLabel = (col: { name: string; description?: string }) => {
  if (addFieldLabelMode.value === 'description') return col.description || col.name
  return col.name
}

const handleAddFieldTableFilter = (keyword: string) => {
  addFieldTableKeyword.value = keyword
}

const handleAddFieldColumnFilter = (keyword: string) => {
  addFieldColumnKeyword.value = keyword
}

const handleTableChange = () => {
  addFieldColumnKeyword.value = ''
  emit('tableChange')
}
</script>

<template>
  <el-dialog v-model="visible" width="560px" title="新增字段">
    <div class="space-y-3">
      <div class="flex items-center justify-end">
        <el-radio-group v-model="addFieldLabelMode" size="small">
          <el-radio-button value="description">中文</el-radio-button>
          <el-radio-button value="name">英文</el-radio-button>
        </el-radio-group>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="text-xs text-gray-600 mb-1">表</div>
          <el-select
            v-model="form.tableAlias"
            filterable
            placeholder="请选择表"
            :filter-method="handleAddFieldTableFilter"
            @change="handleTableChange"
          >
            <el-option
              v-for="entry in filteredAddFieldTables"
              :key="entry.alias"
              :label="getAddFieldTableLabel(entry)"
              :value="entry.alias"
            />
          </el-select>
        </div>
        <div>
          <div class="text-xs text-gray-600 mb-1">字段</div>
          <el-select
            v-model="form.column"
            filterable
            placeholder="请选择字段"
            :filter-method="handleAddFieldColumnFilter"
            @change="emit('columnChange')"
          >
            <el-option
              v-for="c in filteredAddFieldColumns"
              :key="c.name"
              :label="getAddFieldColumnLabel(c)"
              :value="c.name"
            />
          </el-select>
        </div>
      </div>
      <div>
        <div class="text-xs text-gray-600 mb-1">字段名</div>
        <el-input v-model="form.fieldName" placeholder="请输入逻辑字段名（同表内唯一）" />
      </div>
      <div>
        <div class="text-xs text-gray-600 mb-1">显示名称</div>
        <el-input v-model="form.displayName" placeholder="请输入显示名称（同指标集内唯一）" />
      </div>
      <div>
        <div class="text-xs text-gray-600 mb-1">新增类型</div>
        <el-radio-group v-model="form.mode">
          <el-radio-button value="metric">指标</el-radio-button>
          <el-radio-button value="dimension">维度</el-radio-button>
        </el-radio-group>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="emit('confirm')">确定</el-button>
      </div>
    </template>
  </el-dialog>
</template>
