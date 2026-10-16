<script setup lang="ts">
/**
 * 本体属性集编辑器：属性一等实体的行内编辑（名称/展示名/值类型/枚举引用/
 * 主键与标题标记/渲染提示）。属性随对象表单整体提交，这里不做独立保存。
 */
import { onMounted, ref } from 'vue'
import type { PropType } from 'vue'
import { getAdminMetaEnums } from '../../api'
import type { OntologyPropertyItem } from '../../types'
import { ONTOLOGY_NAME_PATTERN, ONTOLOGY_RENDER_HINTS, ONTOLOGY_VALUE_TYPES } from './ontologyMeta'

const props = defineProps({
  // 属性行（父组件持有，就地编辑）
  rows: { type: Array as PropType<OntologyPropertyItem[]>, required: true },
  // certified 冻结名单：名单内的既有属性禁止删除/改名/改主键标记（对齐后端门禁）
  frozenNames: { type: Array as PropType<string[]>, default: () => [] }
})

const isFrozen = (name: string) => props.frozenNames.includes(name)

// 全局枚举候选：value_type=enum 时供引用选择（"enum_name" 或 "domain:enum_name"）
const enumOptions = ref<Array<{ value: string; label: string }>>([])

onMounted(async () => {
  try {
    const res = await getAdminMetaEnums()
    if (res.status === 'success') {
      enumOptions.value = (res.data || []).map(item => ({
        value: item.business_domain ? `${item.business_domain}:${item.enum_name}` : item.enum_name,
        label: item.business_domain ? `${item.business_domain} / ${item.enum_name}` : item.enum_name
      }))
    }
  } catch (err) {
    // 枚举清单加载失败不阻断编辑，仍可手输引用
    console.error('获取全局枚举清单失败:', err)
  }
})

const addRow = () => {
  props.rows.push({
    property_name: '',
    display_name: '',
    description: '',
    value_type: 'String',
    enum_ref: '',
    render_hints: {},
    is_identifier: props.rows.length === 0,
    is_title: props.rows.length === 0
  })
}

const removeRow = (index: number) => {
  props.rows.splice(index, 1)
}

// 标题键至多一个：勾选某行时取消其他行的标题标记
const onTitleChange = (row: OntologyPropertyItem, checked: boolean | string | number) => {
  if (!checked) return
  props.rows.forEach(item => {
    if (item !== row) item.is_title = false
  })
}

const toggleHint = (row: OntologyPropertyItem, key: string, checked: boolean | string | number) => {
  if (checked) {
    row.render_hints[key] = true
  } else {
    delete row.render_hints[key]
  }
}

const hintCount = (row: OntologyPropertyItem) => Object.keys(row.render_hints || {}).length

defineExpose({ addRow })
</script>

<template>
  <div class="flex flex-col gap-1.5">
    <!-- 表头 -->
    <div class="flex items-center gap-2 text-[11px] font-medium text-slate-400">
      <span class="w-36 shrink-0">属性名</span>
      <span class="w-28 shrink-0">展示名</span>
      <span class="w-28 shrink-0">值类型</span>
      <span class="w-40 shrink-0">枚举引用</span>
      <span class="w-14 shrink-0 text-center">主键</span>
      <span class="w-14 shrink-0 text-center">标题</span>
      <span class="w-16 shrink-0 text-center">渲染</span>
      <span class="min-w-0 flex-1">描述</span>
      <span class="w-8 shrink-0"></span>
    </div>
    <!-- 属性行 -->
    <div v-for="(row, index) in rows" :key="index" class="flex items-center gap-2">
      <el-input
        v-model="row.property_name"
        size="small"
        :disabled="isFrozen(row.property_name)"
        placeholder="如 order_no"
        class="!w-36 shrink-0 font-mono"
      />
      <el-input v-model="row.display_name" size="small" placeholder="展示名" class="!w-28 shrink-0" />
      <el-select v-model="row.value_type" size="small" class="!w-28 shrink-0" :empty-values="[null, undefined]">
        <el-option v-for="t in ONTOLOGY_VALUE_TYPES" :key="t" :label="t" :value="t" />
      </el-select>
      <el-select
        v-model="row.enum_ref"
        size="small"
        class="!w-40 shrink-0"
        :disabled="row.value_type !== 'enum'"
        filterable
        allow-create
        default-first-option
        :empty-values="[null, undefined]"
        :placeholder="row.value_type === 'enum' ? '选择全局枚举' : '-'"
      >
        <el-option v-for="opt in enumOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
      </el-select>
      <div class="flex w-14 shrink-0 justify-center">
        <el-checkbox v-model="row.is_identifier" :disabled="isFrozen(row.property_name)" />
      </div>
      <div class="flex w-14 shrink-0 justify-center">
        <el-checkbox v-model="row.is_title" :disabled="isFrozen(row.property_name)" @change="(checked: boolean | string | number) => onTitleChange(row, checked)" />
      </div>
      <div class="flex w-16 shrink-0 justify-center">
        <el-popover placement="bottom" :width="180" trigger="click">
          <template #reference>
            <el-button link size="small" type="primary">
              <i class="fa-solid fa-sliders text-[11px]"></i>
              <span v-if="hintCount(row)" class="ml-0.5 text-[11px]">{{ hintCount(row) }}</span>
            </el-button>
          </template>
          <div class="flex flex-col gap-2">
            <el-checkbox
              v-for="hint in ONTOLOGY_RENDER_HINTS"
              :key="hint.key"
              :model-value="!!row.render_hints[hint.key]"
              @change="(checked: boolean | string | number) => toggleHint(row, hint.key, checked)"
            >
              {{ hint.label }}
            </el-checkbox>
          </div>
        </el-popover>
      </div>
      <el-input v-model="row.description" size="small" placeholder="属性描述（可选）" class="min-w-0 flex-1" />
      <div class="w-8 shrink-0 text-right">
        <el-button
          link
          size="small"
          type="danger"
          :disabled="isFrozen(row.property_name)"
          @click="removeRow(index)"
        >
          <i class="fa-solid fa-trash text-[11px]"></i>
        </el-button>
      </div>
    </div>
    <div v-if="rows.length === 0" class="rounded border border-dashed border-slate-200 py-4 text-center text-xs text-slate-400">
      暂无属性，请至少添加一个属性（含标识属性作为主键）
    </div>
  </div>
</template>
