<script setup lang="ts">
/**
 * 后台管理 · 本体对象列表面板（左树右详情）。
 * 对象/属性/关系为本体三等实体；本面板负责对象资产治理：
 * 左侧「业务域 → 对象 → 属性/关系/映射」完全树形导航（对象子节点懒加载），
 * 右侧按选中节点类型渲染详情卡（对象卡含编辑/状态迁移/删除操作）。
 * 层级对标 Palantir object type groups（扁平分类原语，展示层分组，非对象父子建模）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { TreeInstance } from 'element-plus'
import {
  deleteAdminOntologyObject,
  getAdminOntologyObject,
  getAdminOntologyObjects,
  getAdminOntologyRelations
} from '../../api'
import type {
  OntologyMappingItem,
  OntologyObjectItem,
  OntologyObjectStatus,
  OntologyPropertyItem,
  OntologyRelationItem
} from '../../types'
import OntologyObjectForm from './OntologyObjectForm.vue'
import OntologyStatusDialog from './OntologyStatusDialog.vue'
import { mappingIdentityText, ONTOLOGY_MULTIPLICITY_META, ONTOLOGY_RENDER_HINTS, ONTOLOGY_STATUS_META } from './ontologyMeta'
import { useSemanticConfigOptions } from './useSemanticConfigOptions'

const emit = defineEmits<{ changed: [] }>()

// 语义模型选项（映射行回显「模型名（组名）」）
const { load: loadSemanticOptions, optionOf } = useSemanticConfigOptions()
onMounted(() => {
  void loadSemanticOptions()
})

const mappingLabel = (mapping: OntologyMappingItem): string =>
  optionOf(mapping.semantic_config_id)?.label || mappingIdentityText(mapping)

const loading = ref(false)
const items = ref<OntologyObjectItem[]>([])
const keyword = ref('')
const filterStatus = ref<OntologyObjectStatus | undefined>(undefined)
const filterDomain = ref<string | undefined>(undefined)
const hasLoaded = ref(false)

// 客户端分页：本体对象为公司级治理资产，量级有限
const page = ref(1)
const pageSize = ref(20)
const pagedItems = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return items.value.slice(start, start + pageSize.value)
})

// 域筛选项从已加载对象中提取（后端无独立域清单接口）
const knownDomains = computed(() => Array.from(new Set(items.value.map(item => item.domain).filter(Boolean))).sort())

// ---------- 树节点类型：域 → 对象 → 属性/关系/绑定 ----------

const UNGROUPED_DOMAIN = '未归域'

interface DomainNode {
  row_key: string
  kind: 'domain'
  domain_label: string
  children: ObjectNodeData[]
}
interface ObjectNodeData {
  row_key: string
  kind: 'object'
  object_name: string
  display_name: string
  status: OntologyObjectStatus
  domain: string
  description: string
  mappings_count: number
  update_time: string
  update_by_name: string
  is_leaf: false
}
interface PropertyNode {
  row_key: string
  kind: 'property'
  property: OntologyPropertyItem
  is_leaf: true
}
interface RelationNode {
  row_key: string
  kind: 'relation'
  relation: OntologyRelationItem
  relation_direction: 'from' | 'to'
  is_leaf: true
}
interface MappingNode {
  row_key: string
  kind: 'mapping'
  mapping: OntologyMappingItem
  is_leaf: true
}
interface EmptyNode {
  row_key: string
  kind: 'empty'
  label: string
  is_leaf: true
}
type TreeRow = DomainNode | ObjectNodeData | PropertyNode | RelationNode | MappingNode | EmptyNode

// 数据版本号：编入对象节点 key，列表刷新/保存后懒加载缓存整体失效，防止展示脏数据
const dataVersion = ref(0)

const treeData = computed<DomainNode[]>(() => {
  const groups = new Map<string, OntologyObjectItem[]>()
  for (const item of pagedItems.value) {
    const domain = item.domain || UNGROUPED_DOMAIN
    if (!groups.has(domain)) groups.set(domain, [])
    groups.get(domain)!.push(item)
  }
  const domains = Array.from(groups.keys()).sort((a, b) => {
    if (a === UNGROUPED_DOMAIN) return 1
    if (b === UNGROUPED_DOMAIN) return -1
    return a.localeCompare(b, 'zh-Hans-CN')
  })
  return domains.map(domain => ({
    row_key: `domain:${domain}`,
    kind: 'domain',
    domain_label: domain,
    children: (groups.get(domain) || []).map((item): ObjectNodeData => ({
      row_key: `object:${item.object_name}:v${dataVersion.value}`,
      kind: 'object',
      object_name: item.object_name,
      display_name: item.display_name,
      status: item.status,
      domain: item.domain,
      description: item.description,
      mappings_count: item.mappings_count ?? (item.mappings || []).length,
      update_time: item.update_time || '',
      update_by_name: item.update_by_name || '',
      is_leaf: false
    }))
  }))
})

// 域节点默认展开（key 稳定；对象节点 key 带版本号，保存/刷新后重新折叠）
const defaultExpandedKeys = computed(() => treeData.value.map(row => row.row_key))

// ---------- 对象子节点懒加载 + 详情缓存（树子行与右侧详情共用一次取数） ----------

interface ObjectDetailEntry {
  detail: OntologyObjectItem
  fromRelations: OntologyRelationItem[]
  toRelations: OntologyRelationItem[]
}

const treeRef = ref<TreeInstance>()
// key = 对象节点 row_key（含版本号）
const detailStore = ref<Record<string, ObjectDetailEntry>>({})
const objectLoadState = ref<Record<string, 'loading' | 'error' | 'ready'>>({})

const buildChildRows = (objectNode: ObjectNodeData, entry: ObjectDetailEntry): TreeRow[] => {
  const rows: TreeRow[] = []
  for (const prop of entry.detail.properties || []) {
    rows.push({ row_key: `${objectNode.row_key}:prop:${prop.property_name}`, kind: 'property', property: prop, is_leaf: true })
  }
  for (const rel of entry.fromRelations) {
    rows.push({ row_key: `${objectNode.row_key}:rel:${rel.relation_name}:from`, kind: 'relation', relation: rel, relation_direction: 'from', is_leaf: true })
  }
  for (const rel of entry.toRelations) {
    rows.push({ row_key: `${objectNode.row_key}:rel:${rel.relation_name}:to`, kind: 'relation', relation: rel, relation_direction: 'to', is_leaf: true })
  }
  for (const mapping of entry.detail.mappings || []) {
    rows.push({ row_key: `${objectNode.row_key}:mapping:${mappingIdentityText(mapping)}`, kind: 'mapping', mapping, is_leaf: true })
  }
  if (rows.length === 0) {
    rows.push({ row_key: `${objectNode.row_key}:empty`, kind: 'empty', label: '暂无属性、关系与映射', is_leaf: true })
  }
  return rows
}

const fetchObjectEntry = async (objectNode: ObjectNodeData) => {
  const key = objectNode.row_key
  objectLoadState.value = { ...objectLoadState.value, [key]: 'loading' }
  try {
    const [detailRes, fromRes, toRes] = await Promise.all([
      getAdminOntologyObject(objectNode.object_name),
      getAdminOntologyRelations({ from_object: objectNode.object_name }),
      getAdminOntologyRelations({ to_object: objectNode.object_name })
    ])
    if (detailRes.status !== 'success' || !detailRes.data || fromRes.status !== 'success' || toRes.status !== 'success') {
      const msg = detailRes.msg || fromRes.msg || toRes.msg || '获取对象详情失败'
      ElMessage.error(msg)
      objectLoadState.value = { ...objectLoadState.value, [key]: 'error' }
      return
    }
    const entry: ObjectDetailEntry = {
      detail: detailRes.data,
      fromRelations: fromRes.data?.relations || [],
      toRelations: toRes.data?.relations || []
    }
    detailStore.value = { ...detailStore.value, [key]: entry }
    objectLoadState.value = { ...objectLoadState.value, [key]: 'ready' }
    treeRef.value?.updateKeyChildren?.(key, buildChildRows(objectNode, entry))
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('加载本体对象详情失败:', err)
    objectLoadState.value = { ...objectLoadState.value, [key]: 'error' }
  }
}

const ensureObjectData = (objectNode: ObjectNodeData) => {
  const state = objectLoadState.value[objectNode.row_key]
  if (state === 'ready' || state === 'loading') return
  void fetchObjectEntry(objectNode)
}

const retrySelectedObject = () => {
  if (selectedNode.value?.kind === 'object') void fetchObjectEntry(selectedNode.value)
}

// ---------- 节点选中与树事件 ----------

const treeProps = { label: 'row_key', isLeaf: 'is_leaf' }
const currentNodeKey = ref('')

const selectedNode = ref<TreeRow | null>(null)

const onNodeClick = (data: TreeRow) => {
  selectedNode.value = data
  currentNodeKey.value = data.row_key
  if (data.kind === 'object') ensureObjectData(data)
}

const onNodeExpand = (data: TreeRow) => {
  if (data.kind === 'object') ensureObjectData(data)
}

// ---------- 右侧详情派生 ----------

const selectedObject = computed(() => {
  if (selectedNode.value?.kind !== 'object') return null
  const node = selectedNode.value
  return {
    node,
    entry: detailStore.value[node.row_key],
    state: objectLoadState.value[node.row_key]
  }
})

const selectedObjectStatusMeta = computed(() => {
  const node = selectedNode.value
  if (!node || node.kind !== 'object') return null
  return ONTOLOGY_STATUS_META[node.status as keyof typeof ONTOLOGY_STATUS_META] || null
})

const renderHintLabels = (hints: Record<string, boolean>) =>
  ONTOLOGY_RENDER_HINTS.filter(hint => hints[hint.key]).map(hint => hint.label)

const mappingMappingText = (mapping: OntologyMappingItem) => {
  const entries = Object.entries(mapping.column_mappings || {})
  if (entries.length === 0) return '暂无字段映射'
  return entries.map(([prop, dim]) => `${prop} → ${dim}`).join('，')
}

// ---------- 列表加载与过滤 ----------

const loadObjects = async () => {
  loading.value = true
  try {
    const res = await getAdminOntologyObjects({
      keyword: keyword.value.trim() || undefined,
      status: filterStatus.value || undefined,
      domain: filterDomain.value || undefined
    })
    if (res.status === 'success') {
      items.value = res.data?.objects || []
      hasLoaded.value = true
      dataVersion.value += 1
      detailStore.value = {}
      objectLoadState.value = {}
      const maxPage = Math.max(1, Math.ceil(items.value.length / pageSize.value))
      if (page.value > maxPage) page.value = maxPage
    } else {
      ElMessage.error(res.msg || '获取本体对象列表失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取本体对象列表失败:', err)
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadObjects()
}

const reload = async () => {
  await loadObjects()
}

const searchObjects = () => {
  page.value = 1
  void loadObjects()
}

const onFilterChange = () => {
  page.value = 1
  void loadObjects()
}

// ---------- 编辑与状态（对象详情卡操作） ----------

const formVisible = ref(false)
const formObjectName = ref('')

const statusDialogVisible = ref(false)
const statusTarget = ref<OntologyObjectItem | null>(null)

const openCreate = () => {
  formObjectName.value = ''
  formVisible.value = true
}

const openEdit = (objectName: string) => {
  formObjectName.value = objectName
  formVisible.value = true
}

const openStatus = (objectName: string) => {
  const key = Object.keys(detailStore.value).find(candidate => candidate.startsWith(`object:${objectName}:`))
  statusTarget.value = (key && detailStore.value[key]?.detail) || items.value.find(item => item.object_name === objectName) || null
  statusDialogVisible.value = true
}

const onSaved = async () => {
  await loadObjects()
  emit('changed')
}

const removeObject = async (objectName: string, status: OntologyObjectStatus, displayName: string) => {
  const statusHint =
    status === 'certified'
      ? 'certified 对象禁止删除，请先降级或标记废弃。'
      : '删除后对象、其全部属性与参与的关系将一并移除（软删）。'
  try {
    await ElMessageBox.confirm(
      `确定删除本体对象「${displayName || objectName}」？${statusHint}`,
      '删除本体对象',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntologyObject(objectName)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      selectedNode.value = null
      currentNodeKey.value = ''
      await onSaved()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如 certified 禁删），不再重复提示
    console.error('删除本体对象失败:', err)
  }
}

defineExpose({ ensureLoaded, reload })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 说明条 -->
    <div class="shrink-0 border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
      本体对象是语义层的外圈导航索引：认证态进入 agent 上下文；口径计算始终由语义层管理。
      树形导航：业务域 → 对象 → 属性/关系/映射（对象子节点按需加载）。
    </div>

    <!-- 工具栏 -->
    <div class="flex shrink-0 items-center gap-2 px-4 py-2">
      <el-select
        v-model="filterStatus"
        size="small"
        placeholder="全部状态"
        clearable
        :empty-values="[null, undefined]"
        class="!w-36"
        @change="onFilterChange"
      >
        <el-option v-for="(meta, key) in ONTOLOGY_STATUS_META" :key="key" :label="meta.label" :value="key" />
      </el-select>
      <el-select
        v-model="filterDomain"
        size="small"
        placeholder="全部业务域"
        clearable
        filterable
        :empty-values="[null, undefined]"
        class="!w-40"
        @change="onFilterChange"
      >
        <el-option v-for="domain in knownDomains" :key="domain" :label="domain" :value="domain" />
      </el-select>
      <el-input
        v-model="keyword"
        size="small"
        placeholder="搜索对象名/展示名/描述"
        clearable
        class="!w-56"
        @keyup.enter="searchObjects"
        @clear="searchObjects"
      />
      <el-button size="small" @click="searchObjects">搜索</el-button>
      <el-button size="small" :loading="loading" @click="loadObjects">
        <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
      </el-button>
      <div class="flex-1"></div>
      <el-button size="small" type="primary" round @click="openCreate">
        <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
        新建对象
      </el-button>
    </div>

    <!-- 左树右详情 -->
    <div class="flex min-h-0 flex-1 overflow-hidden">
      <!-- 左：完全树形导航 -->
      <div class="flex w-[340px] shrink-0 flex-col border-r border-slate-100 px-2 pb-2">
        <div class="min-h-0 flex-1 overflow-y-auto pt-1">
          <el-tree
            ref="treeRef"
            :data="treeData"
            node-key="row_key"
            :props="treeProps"
            :expand-on-click-node="false"
            :default-expanded-keys="defaultExpandedKeys"
            :current-node-key="currentNodeKey"
            highlight-current
            empty-text="暂无本体对象"
            @node-click="onNodeClick"
            @node-expand="onNodeExpand"
          >
            <template #default="{ data }">
              <!-- 域节点 -->
              <span v-if="data.kind === 'domain'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-folder text-[11px] text-slate-400"></i>
                <span class="text-xs font-medium text-slate-700">{{ data.domain_label }}</span>
                <span class="text-[11px] text-slate-400">{{ data.children.length }} 个对象</span>
              </span>
              <!-- 对象节点 -->
              <span v-else-if="data.kind === 'object'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-cube text-[11px] text-blue-500"></i>
                <span class="font-mono text-xs text-slate-800">{{ data.object_name }}</span>
                <span v-if="data.display_name" class="text-[11px] text-slate-500">{{ data.display_name }}</span>
                <el-tag
                  size="small"
                  :type="ONTOLOGY_STATUS_META[data.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'"
                  effect="plain"
                >
                  {{ ONTOLOGY_STATUS_META[data.status as keyof typeof ONTOLOGY_STATUS_META]?.label || data.status }}
                </el-tag>
              </span>
              <!-- 属性节点 -->
              <span v-else-if="data.kind === 'property'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-tag text-[10px] text-sky-500"></i>
                <span class="font-mono text-xs text-slate-700">{{ data.property.property_name }}</span>
                <span class="text-[11px] text-slate-400">{{ data.property.value_type }}</span>
                <el-tag v-if="data.property.is_identifier" size="small" type="warning" effect="plain">标识</el-tag>
                <el-tag v-if="data.property.is_title" size="small" type="success" effect="plain">标题</el-tag>
              </span>
              <!-- 关系节点 -->
              <span v-else-if="data.kind === 'relation'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-arrow-right-arrow-left text-[10px] text-violet-500"></i>
                <span class="font-mono text-xs text-slate-700">{{ data.relation.relation_name }}</span>
                <span class="text-[11px] text-slate-400">
                  {{ data.relation_direction === 'from' ? '发起' : '参与' }}
                  {{ data.relation_direction === 'from'
                    ? `→ ${data.relation.to_display_name || data.relation.to_object_name || '-'}`
                    : `← ${data.relation.from_display_name || data.relation.from_object_name || '-'}` }}
                </span>
              </span>
              <!-- 映射节点 -->
              <span v-else-if="data.kind === 'mapping'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-link text-[10px] text-teal-500"></i>
                <span class="text-xs text-slate-700">{{ mappingLabel(data.mapping) }}</span>
              </span>
              <!-- 占位节点 -->
              <span v-else class="text-xs text-slate-400">{{ data.label }}</span>
            </template>
          </el-tree>
        </div>
        <div v-if="items.length > 0" class="flex shrink-0 justify-end pt-1">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            :total="items.length"
            :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next"
            size="small"
            background
          />
        </div>
      </div>

      <!-- 右：选中节点详情 -->
      <div class="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-3">
        <!-- 空态 -->
        <div v-if="!selectedNode" class="flex h-full items-center justify-center">
          <div class="text-center text-xs text-slate-400">
            <i class="fa-solid fa-sitemap mb-2 block text-2xl text-slate-300"></i>
            在左侧选择对象、属性、关系或映射查看详情
          </div>
        </div>

        <!-- 对象详情卡 -->
        <template v-else-if="selectedNode.kind === 'object' && selectedObject">
          <div v-if="selectedObject.state === 'loading'" class="py-8 text-center text-xs text-slate-400">
            <i class="fa-solid fa-spinner fa-spin mr-1.5"></i>加载对象详情中…
          </div>
          <div v-else-if="selectedObject.state === 'error'" class="py-8 text-center">
            <div class="mb-2 text-xs text-slate-500">对象详情加载失败</div>
            <el-button size="small" type="primary" @click="retrySelectedObject">重试</el-button>
          </div>
          <template v-else-if="selectedObject.entry">
            <div class="mb-3 flex items-center gap-2">
              <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.object_name }}</span>
              <span v-if="selectedNode.display_name" class="text-xs text-slate-500">{{ selectedNode.display_name }}</span>
              <el-tooltip v-if="selectedObjectStatusMeta" :content="selectedObjectStatusMeta.hint" placement="top">
                <el-tag size="small" :type="selectedObjectStatusMeta.tagType" effect="plain">{{ selectedObjectStatusMeta.label }}</el-tag>
              </el-tooltip>
              <div class="flex-1"></div>
              <el-button size="small" @click="openEdit(selectedNode.object_name)">
                <i class="fa-solid fa-pen text-[11px]"></i> 编辑
              </el-button>
              <el-button size="small" @click="openStatus(selectedNode.object_name)">
                <i class="fa-solid fa-arrow-right-arrow-left text-[11px]"></i> 状态
              </el-button>
              <el-button
                size="small"
                type="danger"
                @click="removeObject(selectedNode.object_name, selectedNode.status, selectedNode.display_name)"
              >
                <i class="fa-solid fa-trash text-[11px]"></i> 删除
              </el-button>
            </div>
            <div class="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
              <div>业务域：<span class="text-slate-700">{{ selectedNode.domain || '-' }}</span></div>
              <div>更新时间：<span class="text-slate-700">{{ selectedNode.update_time || '-' }}</span></div>
              <div>修改人员：<span class="text-slate-700">{{ selectedNode.update_by_name || '-' }}</span></div>
              <div>映射数：<span class="text-slate-700">{{ selectedNode.mappings_count }}</span></div>
              <div class="col-span-2">描述：<span class="text-slate-700">{{ selectedNode.description || '-' }}</span></div>
            </div>

            <!-- 属性清单 -->
            <div class="mb-1 mt-4 flex items-center gap-2">
              <i class="fa-solid fa-tag text-[10px] text-sky-500"></i>
              <span class="text-xs font-medium text-slate-600">属性清单</span>
              <span class="text-xs text-slate-400">{{ (selectedObject.entry.detail.properties || []).length }} 个</span>
            </div>
            <el-table
              v-if="(selectedObject.entry.detail.properties || []).length > 0"
              :data="selectedObject.entry.detail.properties"
              size="small"
              max-height="240"
              border
            >
              <el-table-column label="属性" min-width="170" show-overflow-tooltip>
                <template #default="{ row: prop }">
                  <span class="font-mono text-xs text-slate-800">{{ prop.property_name }}</span>
                  <span v-if="prop.display_name" class="ml-1.5 text-xs text-slate-500">{{ prop.display_name }}</span>
                  <el-tag v-if="prop.is_identifier" size="small" type="warning" effect="plain" class="ml-1">标识</el-tag>
                  <el-tag v-if="prop.is_title" size="small" type="success" effect="plain" class="ml-1">标题</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="类型" width="90" align="center">
                <template #default="{ row: prop }">
                  <span class="text-xs text-slate-500">{{ prop.value_type }}</span>
                </template>
              </el-table-column>
              <el-table-column label="描述" min-width="200" show-overflow-tooltip>
                <template #default="{ row: prop }">
                  <span class="text-xs text-slate-500">{{ prop.description || '-' }}</span>
                </template>
              </el-table-column>
            </el-table>
            <div v-else class="py-1 text-xs text-slate-400">暂无属性</div>

            <!-- 参与关系 -->
            <div class="mb-1 mt-4 flex items-center gap-2">
              <i class="fa-solid fa-arrow-right-arrow-left text-[10px] text-violet-500"></i>
              <span class="text-xs font-medium text-slate-600">参与关系</span>
              <span class="text-xs text-slate-400">
                {{ selectedObject.entry.fromRelations.length + selectedObject.entry.toRelations.length }} 条
              </span>
            </div>
            <div
              v-if="selectedObject.entry.fromRelations.length + selectedObject.entry.toRelations.length > 0"
              class="flex flex-col gap-1.5"
            >
              <div
                v-for="rel in [...selectedObject.entry.fromRelations.map(relation => ({ relation, direction: 'from' })), ...selectedObject.entry.toRelations.map(relation => ({ relation, direction: 'to' }))]"
                :key="`${rel.direction}:${rel.relation.relation_name}`"
                class="rounded border border-slate-100 bg-slate-50/60 px-2 py-1.5"
              >
                <div class="flex items-center gap-2">
                  <el-tag size="small" :type="rel.direction === 'from' ? 'primary' : 'info'" effect="plain">
                    {{ rel.direction === 'from' ? '发起' : '参与' }}
                  </el-tag>
                  <span class="font-mono text-xs text-slate-700">{{ rel.relation.relation_name }}</span>
                  <span class="text-xs text-slate-500">
                    {{ rel.direction === 'from'
                      ? `→ ${rel.relation.to_display_name || rel.relation.to_object_name || '-'}`
                      : `← ${rel.relation.from_display_name || rel.relation.from_object_name || '-'}` }}
                  </span>
                  <span class="text-[11px] text-slate-400">{{ ONTOLOGY_MULTIPLICITY_META[rel.relation.multiplicity] || rel.relation.multiplicity }}</span>
                </div>
                <div v-if="rel.relation.description" class="mt-0.5 text-[11px] text-slate-500">{{ rel.relation.description }}</div>
              </div>
            </div>
            <div v-else class="py-1 text-xs text-slate-400">未参与任何关系</div>

            <!-- 映射摘要 -->
            <div class="mb-1 mt-4 flex items-center gap-2">
              <i class="fa-solid fa-link text-[10px] text-teal-500"></i>
              <span class="text-xs font-medium text-slate-600">映射摘要</span>
              <span class="text-xs text-slate-400">{{ (selectedObject.entry.detail.mappings || []).length }} 条</span>
            </div>
            <div v-if="(selectedObject.entry.detail.mappings || []).length > 0" class="flex flex-col gap-1.5 pb-2">
              <div
                v-for="mapping in selectedObject.entry.detail.mappings"
                :key="mappingIdentityText(mapping)"
                class="rounded border border-slate-100 bg-slate-50/60 px-2 py-1.5"
              >
                <div class="flex items-center gap-2">
                  <span class="text-xs text-slate-700">{{ mappingLabel(mapping) }}</span>
                  <span class="text-[11px] text-slate-400">绑定语义模型</span>
                </div>
                <div class="mt-0.5 font-mono text-[11px] text-slate-500">{{ mappingMappingText(mapping) }}</div>
              </div>
            </div>
            <div v-else class="py-1 text-xs text-slate-400">未绑定语义模型</div>
          </template>
        </template>

        <!-- 属性详情卡 -->
        <div v-else-if="selectedNode.kind === 'property'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i class="fa-solid fa-tag text-[11px] text-sky-500"></i>
            <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.property.property_name }}</span>
            <span v-if="selectedNode.property.display_name" class="text-xs text-slate-500">{{ selectedNode.property.display_name }}</span>
            <el-tag v-if="selectedNode.property.is_identifier" size="small" type="warning" effect="plain">标识属性</el-tag>
            <el-tag v-if="selectedNode.property.is_title" size="small" type="success" effect="plain">标题属性</el-tag>
          </div>
          <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
            <div>值类型：<span class="text-slate-700">{{ selectedNode.property.value_type }}</span></div>
            <div v-if="selectedNode.property.enum_ref">枚举引用：<span class="font-mono text-slate-700">{{ selectedNode.property.enum_ref }}</span></div>
            <div v-if="renderHintLabels(selectedNode.property.render_hints).length > 0" class="col-span-2">
              渲染提示：<span class="text-slate-700">{{ renderHintLabels(selectedNode.property.render_hints).join('、') }}</span>
            </div>
            <div class="col-span-2">描述：<span class="text-slate-700">{{ selectedNode.property.description || '-' }}</span></div>
          </div>
        </div>

        <!-- 关系详情卡 -->
        <div v-else-if="selectedNode.kind === 'relation'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i class="fa-solid fa-arrow-right-arrow-left text-[11px] text-violet-500"></i>
            <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.relation.relation_name }}</span>
            <el-tag size="small" :type="selectedNode.relation_direction === 'from' ? 'primary' : 'info'" effect="plain">
              {{ selectedNode.relation_direction === 'from' ? '当前对象发起' : '当前对象参与' }}
            </el-tag>
            <el-tag
              size="small"
              :type="ONTOLOGY_STATUS_META[selectedNode.relation.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'"
              effect="plain"
            >
              {{ ONTOLOGY_STATUS_META[selectedNode.relation.status as keyof typeof ONTOLOGY_STATUS_META]?.label || selectedNode.relation.status }}
            </el-tag>
          </div>
          <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
            <div>发起方：<span class="text-slate-700">{{ selectedNode.relation.from_display_name || selectedNode.relation.from_object_name || '-' }}</span></div>
            <div>目标方：<span class="text-slate-700">{{ selectedNode.relation.to_display_name || selectedNode.relation.to_object_name || '-' }}</span></div>
            <div>基数：<span class="text-slate-700">{{ ONTOLOGY_MULTIPLICITY_META[selectedNode.relation.multiplicity] || selectedNode.relation.multiplicity }}</span></div>
            <div>状态：<span class="text-slate-700">{{ selectedNode.relation.status }}</span></div>
            <div class="col-span-2">发起列：<span class="font-mono text-slate-700">{{ selectedNode.relation.from_columns.join('、') || '-' }}</span></div>
            <div class="col-span-2">目标列：<span class="font-mono text-slate-700">{{ selectedNode.relation.to_columns.join('、') || '-' }}</span></div>
            <div class="col-span-2">描述：<span class="text-slate-700">{{ selectedNode.relation.description || '-' }}</span></div>
          </div>
        </div>

        <!-- 映射详情卡 -->
        <div v-else-if="selectedNode.kind === 'mapping'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i class="fa-solid fa-link text-[11px] text-teal-500"></i>
            <span class="text-sm font-medium text-slate-800">{{ mappingLabel(selectedNode.mapping) }}</span>
            <span class="text-xs text-slate-400">绑定语义模型</span>
          </div>
          <div class="mb-1 text-xs font-medium text-slate-600">字段映射（属性 API 名 → 模型维度名）</div>
          <div v-if="Object.keys(selectedNode.mapping.column_mappings || {}).length > 0" class="flex flex-col gap-1">
            <div
              v-for="(dim, propName) in selectedNode.mapping.column_mappings"
              :key="propName"
              class="flex items-center gap-2 font-mono text-xs text-slate-600"
            >
              <span>{{ propName }}</span>
              <i class="fa-solid fa-arrow-right text-[10px] text-slate-300"></i>
              <span>{{ dim }}</span>
            </div>
          </div>
          <div v-else class="text-xs text-slate-400">暂无字段映射</div>
        </div>
      </div>
    </div>

    <!-- 新建/编辑弹窗 -->
    <OntologyObjectForm v-model:visible="formVisible" :object-name="formObjectName" @saved="onSaved" />

    <!-- 状态迁移弹窗 -->
    <OntologyStatusDialog v-model:visible="statusDialogVisible" :object="statusTarget" :objects="items" @saved="onSaved" />
  </div>
</template>

<style scoped>
/* 树节点内容横向排布，超出省略 */
:deep(.el-tree-node__content) {
  height: 28px;
}
</style>
