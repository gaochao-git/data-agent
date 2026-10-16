<script setup lang="ts">
/**
 * 本体绑定编辑器：语义模型绑定行内编辑（统一单轨，不触碰物理表）。
 * 每条绑定 = 语义配置（semantic_config_id）+ 表别名（table_alias）+ 列映射；
 * 口径权威在语义层，问数链路为「本体目录 → 语义模型 YAML → NL2SQL → sql_guard」。
 * 绑定随对象表单整体提交。
 */
import { onMounted, ref } from 'vue'
import type { PropType } from 'vue'
import { ElMessage } from 'element-plus'
import { getAdminDatasources, getAdminSemanticConfigs } from '../../api'
import type { OntologyBindingItem, OntologyPropertyItem, SemanticConfigItem } from '../../types'
import { bindingIdentityText } from './ontologyMeta'

const props = defineProps({
  // 绑定行（父组件持有，就地编辑）
  rows: { type: Array as PropType<OntologyBindingItem[]>, required: true },
  // 对象属性集：列映射的键候选
  properties: { type: Array as PropType<OntologyPropertyItem[]>, default: () => [] }
})

// 全量语义配置：跨数据源组扁平清单（公司级量级有限，编辑回显与别名候选一次就绪）
const allConfigs = ref<SemanticConfigItem[]>([])
const configsLoading = ref(false)
const configsLoaded = ref(false)

onMounted(() => {
  void loadAllConfigs()
})

const loadAllConfigs = async () => {
  if (configsLoading.value) return
  configsLoading.value = true
  try {
    const dsRes = await getAdminDatasources()
    if (dsRes.status !== 'success') {
      ElMessage.error(dsRes.msg || '获取数据源组清单失败')
      return
    }
    const groups = dsRes.data?.datasources || []
    const results = await Promise.all(
      groups.map(async ds => {
        try {
          const res = await getAdminSemanticConfigs(ds.id)
          return res.status === 'success' ? res.data?.configs || [] : []
        } catch (err) {
          // 单个数据源组失败不阻断整体清单，拦截器已弹出具体错误
          console.error(`获取数据源组 ${ds.id} 的语义配置失败:`, err)
          return []
        }
      })
    )
    allConfigs.value = results.flat()
    configsLoaded.value = true
  } catch (err) {
    console.error('获取语义配置清单失败:', err)
  } finally {
    configsLoading.value = false
  }
}

const configOf = (configId: string): SemanticConfigItem | undefined => allConfigs.value.find(c => c.id === configId)

// 表别名候选：从配置的指标/维度中提取唯一别名，同时支持手输
const aliasOptionsOf = (config: SemanticConfigItem | undefined): string[] => {
  if (!config) return []
  const aliases = new Set<string>()
  ;(config.metrics || []).forEach(m => m.table_alias && aliases.add(m.table_alias))
  ;(config.dimensions || []).forEach(d => d.table_alias && aliases.add(d.table_alias))
  return Array.from(aliases)
}

const onConfigChange = (row: OntologyBindingItem) => {
  row.table_alias = ''
}

// ---------- 列映射 ----------

// 已被其他映射行占用的属性键从下拉中排除，避免同键覆盖
const availablePropertyNames = (binding: OntologyBindingItem, currentKey: string) =>
  props.properties
    .map(p => p.property_name)
    .filter(name => name && (name === currentKey || !(name in (binding.column_mappings || {}))))

// 改键保留原值：先取出旧值，删旧键再写新键
const renameMappingKey = (binding: OntologyBindingItem, oldKey: string, newKey: string) => {
  if (!newKey || newKey === oldKey) return
  const value = binding.column_mappings[oldKey] ?? ''
  delete binding.column_mappings[oldKey]
  binding.column_mappings[newKey] = value
}

const addMapping = (binding: OntologyBindingItem) => {
  const used = new Set(Object.keys(binding.column_mappings || {}))
  const next = props.properties.map(p => p.property_name).find(name => name && !used.has(name))
  if (!next) {
    ElMessage.warning('没有可映射的属性，请先在属性集中添加')
    return
  }
  binding.column_mappings[next] = ''
}

const removeMapping = (binding: OntologyBindingItem, key: string) => {
  delete binding.column_mappings[key]
}

// ---------- 行管理 ----------

const addRow = () => {
  props.rows.push({
    semantic_config_id: '',
    table_alias: '',
    column_mappings: {}
  })
}

const removeRow = (index: number) => {
  props.rows.splice(index, 1)
}

defineExpose({ addRow })
</script>

<template>
  <div class="flex flex-col gap-2">
    <div
      v-for="(row, index) in rows"
      :key="index"
      class="rounded-lg border border-slate-200 bg-slate-50/60 p-2.5"
    >
      <div class="mb-2 flex items-center gap-2">
        <span class="text-xs font-medium text-slate-600">语义模型绑定</span>
        <span v-if="bindingIdentityText(row) !== '? / ?'" class="truncate text-xs text-slate-500">
          {{ bindingIdentityText(row) }}
        </span>
        <div class="flex-1"></div>
        <el-button link size="small" type="danger" @click="removeRow(index)">
          <i class="fa-solid fa-trash text-[11px]"></i> 移除
        </el-button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <el-select
          v-model="row.semantic_config_id"
          v-loading="configsLoading && !configsLoaded"
          size="small"
          filterable
          :empty-values="[null, undefined]"
          placeholder="语义配置"
          class="!w-72"
          @change="onConfigChange(row)"
        >
          <el-option
            v-for="config in allConfigs"
            :key="config.id"
            :label="`${config.name} (${config.id})`"
            :value="config.id"
          />
        </el-select>
        <el-select
          v-model="row.table_alias"
          size="small"
          filterable
          allow-create
          default-first-option
          :empty-values="[null, undefined]"
          placeholder="表别名"
          class="!w-44"
        >
          <el-option
            v-for="alias in aliasOptionsOf(configOf(row.semantic_config_id))"
            :key="alias"
            :label="alias"
            :value="alias"
          />
        </el-select>
      </div>

      <!-- 列映射 -->
      <div class="mt-2">
        <div class="mb-1 flex items-center justify-between">
          <span class="text-[11px] text-slate-400">列映射（属性 API 名 → 物理列名；口径以语义层为准）</span>
          <el-button link size="small" type="primary" @click="addMapping(row)">
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
              @change="newKey => renameMappingKey(row, key, newKey)"
            >
              <el-option
                v-for="name in availablePropertyNames(row, key)"
                :key="name"
                :label="name"
                :value="name"
              />
            </el-select>
            <i class="fa-solid fa-arrow-right text-[10px] text-slate-300"></i>
            <el-input
              v-model="row.column_mappings[key]"
              size="small"
              placeholder="物理列名，如 order_no"
              class="!w-56 font-mono"
            />
            <el-button link size="small" type="danger" @click="removeMapping(row, key)">
              <i class="fa-solid fa-times text-[11px]"></i>
            </el-button>
          </div>
        </div>
        <div v-else class="text-[11px] text-slate-300">暂无列映射（属性可后补映射）</div>
      </div>
    </div>

    <div v-if="rows.length === 0" class="rounded border border-dashed border-slate-200 py-3 text-center text-xs text-slate-400">
      暂无绑定；认证前对象至少需要一个语义模型绑定
    </div>
  </div>
</template>
