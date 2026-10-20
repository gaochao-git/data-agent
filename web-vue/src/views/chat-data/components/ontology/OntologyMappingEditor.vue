<script setup lang="ts">
/**
 * 本体映射编辑器：语义模型映射行内编辑（层级 = 组 → 模型 → 维度映射，不触碰物理表）。
 * 每条映射 = 语义配置（semantic_config_id）+ 字段映射 {属性API名: 模型维度名}；
 * 模型选择用 el-cascader 二级导航（第一级=数据源组、第二级=语义模型，行值只存 config id，
 * 级联值 [groupId, configId] 由 optionOf 反推），实体锚点（映射维度必须同表）由后端校验。
 * 映射随对象表单整体提交。
 */
import { computed, onMounted } from 'vue'
import type { PropType } from 'vue'
import { ElMessage } from 'element-plus'
import type { OntologyMappingItem, OntologyPropertyItem, SemanticConfigItem } from '../../types'
import { mappingIdentityText } from './ontologyMeta'
import { useSemanticConfigOptions } from './useSemanticConfigOptions'

const props = defineProps({
  // 映射行（父组件持有；子组件只改行内字段，增删行通过事件交给父组件）
  rows: { type: Array as PropType<OntologyMappingItem[]>, required: true },
  // 对象属性集：字段映射的键候选
  properties: { type: Array as PropType<OntologyPropertyItem[]>, default: () => [] },
  // 可选：限定候选数据源组（本体绑定的组）；不传则展示全部组
  groupIds: { type: Array as PropType<string[]>, default: () => [] }
})

const emit = defineEmits<{ removeRow: [index: number] }>()

const { loading: configsLoading, loaded: configsLoaded, load, optionOf, groupedOptions } =
  useSemanticConfigOptions()

onMounted(() => {
  void load()
})

const configOf = (configId: string): SemanticConfigItem | undefined => optionOf(configId)?.config

// ---------- 模型级联选择（第一级=数据源组，第二级=语义模型） ----------

// 本体锁定：仅展示限定组（对象类型挂本体后映射只能落在本体绑定的组内）
const cascaderOptions = computed(() =>
  groupedOptions()
    .filter(group => props.groupIds.length === 0 || props.groupIds.includes(group.groupId))
    .map(group => ({
      value: group.groupId,
      label: group.groupName,
      children: group.configs.map(opt => ({
        value: opt.config.id,
        label: `${opt.config.name} (${opt.config.id})`
      }))
    }))
)

// 行的级联值 = [groupId, configId]；行数据只存 config id，路径由 optionOf 反推（编辑回显）
const configPath = (configId: string): string[] => {
  const option = configId ? optionOf(configId) : undefined
  return option ? [option.groupId, configId] : []
}

const onCascaderChange = (row: OntologyMappingItem, value: unknown) => {
  const path = Array.isArray(value) ? (value as string[]) : []
  const configId = path[path.length - 1] || ''
  if (configId === row.semantic_config_id) return
  row.semantic_config_id = configId
  onConfigChange(row)
}

const onConfigChange = (row: OntologyMappingItem) => {
  row.column_mappings = {}
  prefillMappings(row)
}

// 同列名预填：属性 API 名与模型维度的物理列同名时自动生成等价映射（右值=维度名），
// 不同名的留给用户手选。映射概念不可省（业务词≠数据词是常态，如 order_no→订单编号维度）。
const prefillMappings = (row: OntologyMappingItem) => {
  if (!row.semantic_config_id || props.properties.length === 0) return
  const config = configOf(row.semantic_config_id)
  if (!config) return
  const columnToDim = new Map<string, string>()
  ;(config.dimensions || []).forEach(d => d.column && d.name && columnToDim.set(d.column, d.name))
  const prefilled = { ...row.column_mappings }
  let added = 0
  props.properties.forEach(p => {
    if (!p.property_name || p.property_name in prefilled) return
    const dimName = columnToDim.get(p.property_name)
    if (dimName) {
      prefilled[p.property_name] = dimName
      added += 1
    }
  })
  if (added > 0) row.column_mappings = prefilled
}

// ---------- 字段映射 ----------

// 已在其他映射行使用的属性：非标识属性只能单源映射（标识属性可作跨源主键，允许多映射）
const usedByOtherMappings = (propertyName: string, mapping: OntologyMappingItem) =>
  props.rows.some(
    other =>
      other !== mapping &&
      Object.prototype.hasOwnProperty.call(other.column_mappings || {}, propertyName) &&
      !props.properties.some(prop => prop.property_name === propertyName && prop.is_identifier)
  )

// 已被其他映射行占用的非标识属性键从下拉中排除，避免提交时被后端单源规则拒绝
const availablePropertyNames = (mapping: OntologyMappingItem, currentKey: string) =>
  props.properties
    .map(p => p.property_name)
    .filter(
      name =>
        name &&
        (name === currentKey ||
          (!(name in (mapping.column_mappings || {})) && !usedByOtherMappings(name, mapping)))
    )

// 改键保留原值：先取出旧值，删旧键再写新键
const renameMappingKey = (mapping: OntologyMappingItem, oldKey: string, newKey: string) => {
  if (!newKey || newKey === oldKey) return
  const value = mapping.column_mappings[oldKey] ?? ''
  delete mapping.column_mappings[oldKey]
  mapping.column_mappings[newKey] = value
}

const addMapping = (mapping: OntologyMappingItem) => {
  const used = new Set(Object.keys(mapping.column_mappings || {}))
  const next = props.properties.map(p => p.property_name).find(name => name && !used.has(name))
  if (!next) {
    ElMessage.warning('没有可映射的属性，请先在属性集中添加')
    return
  }
  mapping.column_mappings[next] = ''
}

const removeMapping = (mapping: OntologyMappingItem, key: string) => {
  delete mapping.column_mappings[key]
}

// ---------- 行管理 ----------

// 增删由父组件持有数组完成（子组件不直接改 props）：
// 添加按钮在父组件，删除通过事件回传索引。
const removeRow = (index: number) => {
  emit('removeRow', index)
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <div
      v-for="(row, index) in rows"
      :key="index"
      class="rounded-lg border border-slate-200 bg-slate-50/60 p-2.5"
    >
      <div class="mb-2 flex items-center gap-2">
        <span class="text-xs font-medium text-slate-600">语义模型映射</span>
        <span v-if="row.semantic_config_id" class="truncate text-xs text-slate-500">
          {{ optionOf(row.semantic_config_id)?.label || mappingIdentityText(row) }}
        </span>
        <div class="flex-1"></div>
        <el-button link size="small" type="danger" @click="removeRow(index)">
          <i class="fa-solid fa-trash text-[11px]"></i> 移除
        </el-button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <el-cascader
          :model-value="configPath(row.semantic_config_id)"
          :options="cascaderOptions"
          v-loading="configsLoading && !configsLoaded"
          size="small"
          filterable
          clearable
          :empty-values="[null, undefined]"
          placeholder="先选数据源组，再选语义模型"
          class="!w-[464px] max-w-full"
          @change="(value: unknown) => onCascaderChange(row, value)"
        />
      </div>

      <!-- 字段映射 -->
      <div class="mt-2">
        <div class="mb-1 flex items-center justify-between">
          <span class="text-[11px] text-slate-400">字段映射（属性 API 名 → 模型维度名；口径以语义层为准）</span>
          <el-button link size="small" type="primary" :disabled="!row.semantic_config_id" @click="addMapping(row)">
            <i class="fa-solid fa-plus text-[11px]"></i> 添加映射
          </el-button>
        </div>
        <div v-if="Object.keys(row.column_mappings || {}).length > 0" class="flex flex-col gap-1">
          <div v-for="(_, key) in row.column_mappings" :key="key" class="flex items-center gap-2">
            <el-select
              :model-value="key"
              size="small"
              filterable
              class="!w-44 shrink-0 font-mono"
              @change="(newKey: string) => renameMappingKey(row, key, newKey)"
            >
              <el-option
                v-for="name in availablePropertyNames(row, key)"
                :key="name"
                :label="name"
                :value="name"
              />
            </el-select>
            <i class="fa-solid fa-arrow-right text-[10px] text-slate-300"></i>
            <el-select
              v-model="row.column_mappings[key]"
              size="small"
              filterable
              :empty-values="[null, undefined]"
              placeholder="模型维度"
              class="!w-64"
            >
              <el-option
                v-for="dim in configOf(row.semantic_config_id)?.dimensions || []"
                :key="`${dim.table}.${dim.name}`"
                :label="`${dim.name}（${dim.table}.${dim.column}）`"
                :value="dim.name"
              />
            </el-select>
            <el-button link size="small" type="danger" @click="removeMapping(row, key)">
              <i class="fa-solid fa-times text-[11px]"></i>
            </el-button>
          </div>
        </div>
        <div v-else class="text-[11px] text-slate-300">
          {{ row.semantic_config_id ? '暂无字段映射（属性可后补映射）' : '请先选择语义模型' }}
        </div>
      </div>
    </div>

    <div v-if="rows.length === 0" class="rounded border border-dashed border-slate-200 py-3 text-center text-xs text-slate-400">
      暂无映射；认证前对象至少需要一个语义模型映射
    </div>
  </div>
</template>
