<script setup lang="ts">
/**
 * 后台管理 · 技能总览（只读，自包含）。
 *
 * 左侧是「来源 / 插件 → 技能 → 固定目录 → 文件」树状目录层级，右侧显示选中节点内容。
 * 刻意不复用对话内 SkillManager：用户端技能管理组件保持零改动，管理端只用
 * /admin/skills* 三个只读接口。启停、收藏、编辑、导入等写操作不在此面板出现。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getAdminSkill,
  getAdminSkillFile,
  getAdminSkills,
  getAdminSkillOfficeHtmlPreview,
  getAdminSkillVersionRawFile
} from '../api'
import type { SkillFileDirName } from '../api'
import type { SkillInfo } from '../types'
import {
  countSkillsBySource,
  filterAdminSkills,
  skillOwnerText,
  skillSource,
  skillVersionText,
  skillVisibilityText
} from '../utils/skillAdmin'
import type { AdminSkillInfo } from '../utils/skillAdmin'
import { renderMarkdown } from '../utils/markdown'

type SkillDetail = SkillInfo & { content?: string; references?: string[]; assets?: string[] }
type NodeType = 'group' | 'plugin' | 'skill' | 'dir' | 'file'

interface TreeNode {
  key: string
  label: string
  /** 面包屑/目录清单用的纯路径名，不带 (N) 计数。 */
  pathLabel: string
  type: NodeType
  depth: number
  expandable: boolean
  skillKey?: string
  dirName?: SkillFileDirName
  filePath?: string
  isSkillMd?: boolean
}

const GROUP_LABELS: Record<string, string> = {
  system: '系统技能',
  user: '用户技能',
  plugin: '插件技能'
}
const SOURCE_ORDER = ['system', 'user', 'plugin'] as const
const FILE_DIRS: SkillFileDirName[] = ['references', 'assets']

const loading = ref(false)
const hasLoaded = ref(false)
const skills = ref<AdminSkillInfo[]>([])
const keyword = ref('')
const sourceFilter = ref('all')

/** 左栏宽度与拖拽分隔：默认 360，可左右拖动（280–640），右栏自适应占满剩余空间。 */
const LEFT_MIN = 280
const LEFT_MAX = 640
const leftWidth = ref(360)
const resizing = ref(false)
const startResize = (event: PointerEvent) => {
  event.preventDefault()
  const startX = event.clientX
  const startWidth = leftWidth.value
  resizing.value = true
  const onMove = (e: PointerEvent) => {
    leftWidth.value = Math.min(LEFT_MAX, Math.max(LEFT_MIN, startWidth + (e.clientX - startX)))
  }
  const onUp = () => {
    resizing.value = false
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
const expandedKeys = ref<string[]>([])
const skillDetails = ref<Record<string, SkillDetail>>({})
const selectedKey = ref('')
const detailLoading = ref(false)
const fileContent = ref('')
const fileLoading = ref(false)
const fileKind = ref<'markdown' | 'image' | 'pdf' | 'office' | 'text'>('text')
const fileObjectUrl = ref('')
const officeHtml = ref('')
// Markdown 预览模式：rendered-渲染 HTML，source-源码原文
const markdownViewMode = ref<'rendered' | 'source'>('rendered')

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'])

const fileKindOf = (filePath: string): 'markdown' | 'image' | 'pdf' | 'office' | 'text' => {
  const ext = filePath.split('.').pop()?.toLowerCase() || ''
  if (ext === 'md' || ext === 'markdown') return 'markdown'
  if (IMAGE_EXTENSIONS.has(ext)) return 'image'
  if (ext === 'pdf') return 'pdf'
  if (ext === 'docx') return 'office'
  return 'text'
}

const releaseFileObjectUrl = () => {
  if (fileObjectUrl.value) {
    URL.revokeObjectURL(fileObjectUrl.value)
    fileObjectUrl.value = ''
  }
}
const fileHint = ref('')

const counts = computed(() => countSkillsBySource(skills.value))
const visibleSkills = computed(() =>
  filterAdminSkills(skills.value, { keyword: keyword.value, source: sourceFilter.value })
)
const expandedSet = computed(() => new Set(expandedKeys.value))

/** 技能节点下挂 SKILL.md 与 references / assets 两个固定目录，文件来自详情接口。 */
const appendSkillNodes = (
  out: TreeNode[],
  item: AdminSkillInfo,
  depth: number,
  expanded: Set<string>
): void => {
  const skillKey = item.skill_id || item.name
  const key = `skill:${skillKey}`
  const skillLabel = item.title || item.name
  out.push({ key, label: skillLabel, pathLabel: skillLabel, type: 'skill', depth, expandable: true, skillKey })
  if (!expanded.has(key)) return
  const detail = skillDetails.value[skillKey]
  out.push({
    key: `${key}:SKILL.md`,
    label: 'SKILL.md',
    pathLabel: 'SKILL.md',
    type: 'file',
    depth: depth + 1,
    expandable: false,
    skillKey,
    isSkillMd: true
  })
  for (const dirName of FILE_DIRS) {
    const files = detail?.[dirName] || []
    const dirKey = `${key}:dir:${dirName}`
    out.push({
      key: dirKey,
      label: `${dirName} (${files.length})`,
      pathLabel: dirName,
      type: 'dir',
      depth: depth + 1,
      expandable: true,
      skillKey,
      dirName
    })
    if (!expanded.has(dirKey)) continue
    for (const file of files) {
      out.push({
        key: `${dirKey}:${file}`,
        label: file,
        pathLabel: file,
        type: 'file',
        depth: depth + 2,
        expandable: false,
        skillKey,
        dirName,
        filePath: file
      })
    }
  }
}

/** 插件技能按所属插件再分一层：插件 → 技能 → 文件。 */
const appendPluginGroups = (
  out: TreeNode[],
  items: AdminSkillInfo[],
  expanded: Set<string>,
  forceExpand: boolean
): void => {
  const grouped = new Map<string, AdminSkillInfo[]>()
  for (const item of items) {
    const name = item.plugin_name || '未归属插件'
    grouped.set(name, [...(grouped.get(name) || []), item])
  }
  for (const [pluginName, list] of grouped) {
    const pluginKey = `plugin:group:${pluginName}`
    out.push({
      key: pluginKey,
      label: `${pluginName} (${list.length})`,
      pathLabel: pluginName,
      type: 'plugin',
      depth: 1,
      expandable: true
    })
    if (!expanded.has(pluginKey) && !forceExpand) continue
    for (const item of list) appendSkillNodes(out, item, 2, expanded)
  }
}

/** 扁平渲染的可见节点列表：depth 控制缩进，避免引入递归组件与 el-tree 依赖。 */
const treeNodes = computed<TreeNode[]>(() => {
  const out: TreeNode[] = []
  const expanded = expandedSet.value
  // 搜索时强制展开来源/插件层，让命中技能直接可见，无需逐层点开目录。
  const forceExpand = keyword.value.trim().length > 0
  for (const source of SOURCE_ORDER) {
    if (sourceFilter.value !== 'all' && sourceFilter.value !== source) continue
    const items = visibleSkills.value.filter(item => skillSource(item) === source)
    const groupKey = `group:${source}`
    out.push({
      key: groupKey,
      label: `${GROUP_LABELS[source] ?? source} (${items.length})`,
      pathLabel: GROUP_LABELS[source] ?? source,
      type: 'group',
      depth: 0,
      expandable: true
    })
    if (!expanded.has(groupKey) && !forceExpand) continue
    if (source === 'plugin') {
      appendPluginGroups(out, items, expanded, forceExpand)
    } else {
      for (const item of items) appendSkillNodes(out, item, 1, expanded)
    }
  }
  return out
})

const selectedNode = computed(() => treeNodes.value.find(node => node.key === selectedKey.value) || null)
const selectedDetail = computed(() => {
  const key = selectedNode.value?.skillKey
  return key ? skillDetails.value[key] || null : null
})
const isPluginSkill = computed(() => selectedDetail.value?.source === 'plugin')

/** 一行定位：从树里回溯父级，避免右侧重复铺开技能标题/来源等大块信息。 */
const breadcrumb = computed(() => {
  const node = selectedNode.value
  if (!node) return ''
  const start = treeNodes.value.findIndex(item => item.key === node.key)
  const parts = [node.pathLabel]
  let depth = node.depth
  for (const parent of treeNodes.value.slice(0, start).reverse()) {
    if (parent.depth < depth) {
      parts.unshift(parent.pathLabel)
      depth = parent.depth
    }
    if (depth === 0) break
  }
  return parts.join(' / ')
})

/** 左侧树没有的少量元信息压成一行，不占正文空间。 */
const metaInline = computed(() => {
  const item = selectedDetail.value
  if (!item) return ''
  return [skillOwnerText(item), skillVisibilityText(item), skillVersionText(item)].filter(Boolean).join(' · ')
})

const contentText = computed(() => {
  const node = selectedNode.value
  if (!node) return ''
  if (node.type === 'file' && !node.isSkillMd) return fileContent.value
  if (node.type === 'skill' || node.type === 'file') return selectedDetail.value?.content || ''
  // 目录节点：列出直接子项，保持「左侧层级 / 右侧内容」的一致手感
  const start = treeNodes.value.findIndex(item => item.key === node.key)
  const children: string[] = []
  for (const next of treeNodes.value.slice(start + 1)) {
    if (next.depth <= node.depth) break
    if (next.depth === node.depth + 1) children.push(next.pathLabel)
  }
  return children.length ? children.join('\n') : '（空目录）'
})

const chevronClass = (node: TreeNode): string => {
  if (!node.expandable) return 'fa-solid fa-chevron-right opacity-0'
  return expandedSet.value.has(node.key) ? 'fa-solid fa-chevron-down' : 'fa-solid fa-chevron-right'
}

const nodeIcon = (node: TreeNode): string => {
  if (node.type === 'group' || node.type === 'plugin') return 'fa-solid fa-layer-group text-indigo-400'
  if (node.type === 'skill') return 'fa-solid fa-cube text-slate-400'
  if (node.type === 'dir') return 'fa-regular fa-folder text-amber-500'
  return 'fa-regular fa-file-lines text-slate-400'
}

const resetView = (): void => {
  expandedKeys.value = SOURCE_ORDER.map(source => `group:${source}`)
  skillDetails.value = {}
  selectedKey.value = ''
  fileContent.value = ''
  fileHint.value = ''
}

const loadSkills = async (): Promise<void> => {
  loading.value = true
  try {
    const res = await getAdminSkills()
    if (res.status === 'success') {
      skills.value = res.data?.skills || []
      hasLoaded.value = true
      resetView()
    } else {
      ElMessage.error(res.msg || '获取技能总览失败')
    }
  } catch (err) {
    console.error('获取技能总览失败:', err)
    ElMessage.error('获取技能总览失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

/** 左侧导航切入时调用：每次进入重新拉取，保证目录树与内容都是最新的。 */
const ensureLoaded = (): void => {
  void loadSkills()
}

const loadDetail = async (skillKey: string): Promise<void> => {
  if (skillDetails.value[skillKey]) return
  detailLoading.value = true
  try {
    const res = await getAdminSkill(skillKey)
    if (res.status === 'success') {
      skillDetails.value = { ...skillDetails.value, [skillKey]: res.data }
    } else {
      ElMessage.error(res.msg || '获取技能详情失败')
    }
  } catch (err) {
    console.error('获取技能详情失败:', err)
    ElMessage.error('获取技能详情失败，请稍后重试')
  } finally {
    detailLoading.value = false
  }
}

const showFile = async (node: TreeNode): Promise<void> => {
  fileContent.value = ''
  fileHint.value = ''
  releaseFileObjectUrl()
  officeHtml.value = ''
  markdownViewMode.value = 'rendered'
  fileKind.value = fileKindOf(node.filePath || '')
  if (!node.skillKey || !node.dirName || !node.filePath) return
  if (isPluginSkill.value) {
    fileHint.value = '插件技能的附属文件不支持在此预览。'
    return
  }
  const detail = skillDetails.value[node.skillKey]
  const versionNo = Number(detail?.current_version_no || detail?.draft_version_no || 1)
  fileLoading.value = true
  try {
    const kind = fileKind.value
    if (kind === 'image' || kind === 'pdf') {
      const blob = await getAdminSkillVersionRawFile(node.skillKey, {
        dir_name: node.dirName,
        file_path: node.filePath,
        version_no: versionNo
      })
      fileObjectUrl.value = URL.createObjectURL(blob)
      return
    }
    if (kind === 'office') {
      const res = await getAdminSkillOfficeHtmlPreview(node.skillKey, {
        dir_name: node.dirName,
        file_path: node.filePath,
        version_no: versionNo
      })
      if (res.status === 'success' && res.data?.available && res.data.html) {
        officeHtml.value = res.data.html
      } else {
        ElMessage.warning(res.data?.reason === 'preview_failed' ? 'Office 预览暂不可用' : '该文件类型不支持在线预览')
      }
      return
    }
    const res = await getAdminSkillFile(node.skillKey, {
      dir_name: node.dirName,
      file_path: node.filePath,
      version_no: versionNo
    })
    if (res.status === 'success') {
      fileContent.value = res.data?.content || ''
    } else {
      ElMessage.error(res.msg || '读取技能文件失败')
    }
  } catch (err) {
    console.error('读取技能文件失败:', err)
    ElMessage.error('读取技能文件失败，请稍后重试')
  } finally {
    fileLoading.value = false
  }
}

const onNodeClick = async (node: TreeNode): Promise<void> => {
  selectedKey.value = node.key
  if (node.expandable) {
    expandedKeys.value = expandedKeys.value.includes(node.key)
      ? expandedKeys.value.filter(key => key !== node.key)
      : [...expandedKeys.value, node.key]
  }
  if ((node.type === 'skill' || node.isSkillMd) && node.skillKey) {
    // SKILL.md（技能节点与 SKILL.md 文件）固定按 markdown 预览，保证渲染/源码切换可用
    fileKind.value = 'markdown'
    markdownViewMode.value = 'rendered'
    await loadDetail(node.skillKey)
  }
  if (node.type === 'file' && !node.isSkillMd) await showFile(node)
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden" :class="{ 'select-none': resizing }">
    <div class="flex min-h-0 flex-1">
      <!-- 左：筛选控件 + 树状目录层级（宽度可拖动） -->
      <div
        class="flex min-h-0 shrink-0 flex-col overflow-hidden border-r border-slate-100"
        :style="{ width: `${leftWidth}px` }"
        data-qa="admin-skill-tree"
      >
        <div class="flex shrink-0 flex-col gap-2 border-b border-slate-100 px-3 py-2">
          <input
            v-model="keyword"
            type="search"
            class="h-8 w-full rounded-md border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-300"
            placeholder="搜索名称 / 标题 / 描述 / 创建人"
            aria-label="搜索技能"
          />
          <div class="flex items-center gap-2">
            <select
              v-model="sourceFilter"
              class="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-700"
              aria-label="按来源筛选"
            >
              <option value="all">全部来源 ({{ counts.all }})</option>
              <option value="system">系统 ({{ counts.system }})</option>
              <option value="user">用户 ({{ counts.user }})</option>
              <option value="plugin">插件 ({{ counts.plugin }})</option>
            </select>
            <button
              type="button"
              class="h-8 shrink-0 rounded-md border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              :disabled="loading"
              @click="loadSkills"
            >
              <i class="fa-solid fa-rotate-right mr-1" style="font-size: 10px"></i>刷新
            </button>
          </div>
        </div>
        <div v-if="loading && !hasLoaded" class="min-h-0 flex-1 overflow-y-auto px-4 py-6 text-center text-xs text-slate-400">加载中…</div>
        <div v-else-if="!visibleSkills.length" class="min-h-0 flex-1 overflow-y-auto px-4 py-6 text-center text-xs text-slate-400">
          没有匹配的技能
        </div>
        <div v-else class="min-h-0 flex-1 overflow-y-auto">
          <button
            v-for="node in treeNodes"
            :key="node.key"
            type="button"
            class="flex w-full items-center gap-1.5 border-b border-slate-50 py-1.5 pr-3 text-left text-xs hover:bg-slate-50"
          :class="[
            selectedKey === node.key ? 'bg-indigo-50/70 text-indigo-700' : 'text-slate-700',
            node.type === 'group' ? 'font-semibold' : ''
          ]"
          :style="{ paddingLeft: `${8 + node.depth * 16}px` }"
          :data-tree="node.key"
          :data-type="node.type"
          @click="onNodeClick(node)"
        >
          <i :class="chevronClass(node)" class="w-2 shrink-0 text-[9px] text-slate-400"></i>
          <i :class="nodeIcon(node)" class="w-3 shrink-0 text-[11px]"></i>
          <span class="truncate">{{ node.label }}</span>
        </button>
        </div>
      </div>

      <!-- 拖拽分隔条：按住左右拖动调整左栏宽度 -->
      <div
        class="w-1 shrink-0 cursor-col-resize bg-slate-100 transition-colors hover:bg-indigo-200"
        :class="{ 'bg-indigo-300': resizing }"
        data-qa="admin-skill-splitter"
        @pointerdown="startResize"
      ></div>

      <!-- 右：顶部只留一行定位信息，其余空间全部留给正文 -->
      <div class="flex min-w-0 flex-1 flex-col overflow-hidden px-4 py-3" data-qa="admin-skill-content">
        <div class="mb-2 flex shrink-0 items-center gap-3 text-[11px] text-slate-500">
          <span class="min-w-0 flex-1 truncate" :title="breadcrumb">{{ breadcrumb || '技能总览' }}</span>
          <span v-if="metaInline" class="shrink-0 text-slate-400">{{ metaInline }}</span>
          <span
            v-if="fileKind === 'markdown' && contentText"
            class="flex shrink-0 overflow-hidden rounded border border-slate-200 leading-none"
            data-qa="admin-skill-md-toggle"
          >
            <button
              type="button"
              class="px-1.5 py-0.5 transition-colors"
              :class="markdownViewMode === 'rendered' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-500 hover:bg-slate-50'"
              data-qa="admin-skill-md-rendered"
              @click="markdownViewMode = 'rendered'"
            >渲染</button>
            <button
              type="button"
              class="px-1.5 py-0.5 transition-colors"
              :class="markdownViewMode === 'source' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-500 hover:bg-slate-50'"
              data-qa="admin-skill-md-source"
              @click="markdownViewMode = 'source'"
            >源码</button>
          </span>
          <span
            class="shrink-0 text-slate-400"
            title="只读总览：跨用户展示系统技能与全部用户技能（含停用、草稿）；启用停用、收藏与内容编辑请到对话内「技能管理」操作。"
          >只读总览 · 共 {{ counts.all }} 个技能</span>
        </div>
        <div v-if="detailLoading || fileLoading" class="flex min-h-0 flex-1 items-center justify-center text-xs text-slate-400">加载中…</div>
        <template v-else>
          <!-- 图片：raw 字节直出 -->
          <div v-if="fileKind === 'image' && fileObjectUrl" class="min-h-0 flex-1 overflow-auto rounded-md border border-slate-100 bg-slate-50 p-3">
            <img :src="fileObjectUrl" class="max-w-full" alt="技能附件图片" />
          </div>
          <!-- PDF：iframe 内嵌预览 -->
          <iframe
            v-else-if="fileKind === 'pdf' && fileObjectUrl"
            :src="fileObjectUrl"
            class="min-h-0 flex-1 rounded-md border border-slate-100 bg-white"
            title="PDF 预览"
          ></iframe>
          <!-- Word：OfficeCLI 服务端 HTML 预览 -->
          <div
            v-else-if="fileKind === 'office' && officeHtml"
            class="min-h-0 flex-1 overflow-auto rounded-md border border-slate-100 bg-white p-4 text-sm text-slate-700"
            v-html="officeHtml"
          ></div>
          <!-- Markdown：渲染 / 源码切换（覆盖 SKILL.md 与附属 md 文件） -->
          <div
            v-else-if="fileKind === 'markdown' && contentText"
            class="min-h-0 flex-1 overflow-auto rounded-md border border-slate-100 bg-white p-4 text-sm leading-relaxed text-slate-700"
          >
            <pre v-if="markdownViewMode === 'source'" class="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-slate-600">{{ contentText }}</pre>
            <div v-else v-html="renderMarkdown(contentText)"></div>
          </div>
          <!-- Word/图片/PDF 加载失败或普通文本：原样文本展示 -->
          <pre v-else class="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-100 bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">{{ fileKind === 'office' ? 'Office 预览不可用，请下载后查看。' : fileKind === 'image' || fileKind === 'pdf' ? '文件加载失败，请稍后重试。' : (contentText || fileHint || '从左侧目录树选择技能、SKILL.md 或附属文件查看内容') }}</pre>
        </template>
      </div>
    </div>
  </div>
</template>
