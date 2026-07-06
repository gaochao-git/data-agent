<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import {
  copySkill,
  createSkill,
  deleteSkill,
  deleteSkillVersionFile,
  favoriteSkill,
  getSkill,
  getSkillVersionFile,
  getSkillVersionFiles,
  getSkills,
  getSkillVersion,
  getSkillVersions,
  publishSkill,
  rollbackSkillVersion,
  saveSkillVersionFile,
  unfavoriteSkill,
  updateSkill
} from '../api'
import type { SkillInfo, SkillVersionInfo } from '../types'

type SkillFileDir = 'references' | 'assets' | 'scripts'
type SkillScope = 'query' | 'analyze' | 'all'
type SkillAgent = 'query' | 'analyze'
type SkillFileForm = {
  dir_name: SkillFileDir
  file_path: string
  content: string
}
type SkillFileTreeNode = {
  key: string
  label: string
  path: string
  type: 'root' | 'dir' | 'skill' | 'file'
  dirName?: SkillFileDir
  children: SkillFileTreeNode[]
}

const props = defineProps<{
  initialSkillId?: string | null
  defaultSkillScope?: SkillScope
  agent?: SkillAgent
}>()

const md = new MarkdownIt({ breaks: true, html: false, linkify: true })
const skills = ref<SkillInfo[]>([])
const selectedSkill = ref<(SkillInfo & { content?: string }) | null>(null)
const skillVersions = ref<SkillVersionInfo[]>([])
const selectedVersion = ref<SkillVersionInfo | null>(null)
const skillFiles = ref<Record<SkillFileDir, string[]>>({ references: [], assets: [], scripts: [] })
const loading = ref(false)
const versionLoading = ref(false)
const fileLoading = ref(false)
const fileSaving = ref(false)
const saving = ref(false)
const activeScope = ref<'all' | 'favorites' | 'mine' | 'system'>('all')
const keyword = ref('')
const showCreatePanel = ref(false)
const contentViewMode = ref<'markdown' | 'text'>('markdown')
const editingSkillId = ref<string | null>(null)
const selectedFileNode = ref<{ type: 'skill' | 'file'; dir_name?: SkillFileDir; file_path: string } | null>(null)
const selectedFileEditing = ref(false)
const fileForm = reactive<SkillFileForm>({
  dir_name: 'references',
  file_path: '',
  content: '',
})

const form = reactive({
  skill_name: '',
  skill_title: '',
  skill_desc: '',
  skill_category: 'custom',
  skill_scope: 'analyze' as SkillScope,
  visibility_type: 'private' as 'private' | 'global',
  content: '',
})

const scopeOptions = [
  { key: 'all', label: '全部' },
  { key: 'favorites', label: '收藏' },
  { key: 'mine', label: '我的' },
  { key: 'system', label: '系统' },
] as const

const skillScopeOptions: Array<{ value: SkillScope; label: string }> = [
  { value: 'analyze', label: '智能分析' },
  { value: 'query', label: '智能问数' },
  { value: 'all', label: '两者' },
]

const displayedSkills = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return skills.value
  return skills.value.filter((skill) => {
    const content = `${skill.name} ${skill.title || ''} ${skill.description || ''}`.toLowerCase()
    return content.includes(text)
  })
})

const displayedContent = computed(() => selectedVersion.value?.content ?? selectedSkill.value?.content ?? '')
const currentVersionNo = computed(() => selectedVersion.value?.skill_version_no || selectedSkill.value?.draft_version_no || selectedSkill.value?.current_version_no || 0)
const editableVersionNo = computed(() => selectedSkill.value?.draft_version_no || selectedSkill.value?.current_version_no || currentVersionNo.value)
const canManageFiles = computed(() => Boolean(
  selectedSkill.value?.source === 'user'
  && currentVersionNo.value
  && (!selectedVersion.value || selectedVersion.value.version_publish_status === 'draft')
))
const canShowFileTree = computed(() => Boolean(selectedSkill.value))
const selectedFileTitle = computed(() => selectedFileNode.value?.file_path || 'SKILL.md')
const selectedFileContent = computed(() => selectedFileNode.value ? fileForm.content : displayedContent.value)
const displayedContentHtml = computed(() => md.render(selectedFileContent.value || '暂无内容'))
const canEditSelectedFile = computed(() => Boolean(canManageFiles.value && selectedFileNode.value && selectedFileEditing.value))
const canDeleteSkill = (skill?: SkillInfo | null) => Boolean(
  skill?.source === 'user'
)
const canCopySkill = (skill?: SkillInfo | null) => Boolean(
  skill?.source && skill.source !== 'system'
)
const getSkillCreatorLabel = (skill: SkillInfo) => {
  if (skill.source === 'system') return '系统'
  return skill.creator_name || skill.creator_id || skill.owner_user_id || '未知'
}
const getSkillDirectoryName = computed(() => selectedSkill.value?.name || selectedSkill.value?.skill_id || 'skill')
const skillDirDescriptions: Record<SkillFileDir, string> = {
  references: '参考文档、说明和知识材料',
  assets: '图片、示例数据、配置等素材',
  scripts: '可复用脚本和工具代码',
}
const getSkillDirDescription = (dirName?: SkillFileDir) => (dirName ? skillDirDescriptions[dirName] || '' : '')
const canCreateFileInDir = (dirName?: SkillFileDir) => {
  if (!dirName) return false
  return !(props.agent === 'query' && dirName === 'scripts')
}
const canAccessFileDir = (dirName?: SkillFileDir) => {
  if (!dirName) return false
  return !(props.agent === 'query' && dirName === 'scripts')
}
const availableFileDirs = computed<SkillFileDir[]>(() => (
  props.agent === 'query' ? ['references', 'assets'] : ['references', 'assets', 'scripts']
))
const buildFixedDirNode = (dirName: SkillFileDir, files: string[]): SkillFileTreeNode => {
  const root: SkillFileTreeNode = {
    key: dirName,
    label: dirName,
    path: '',
    type: 'dir',
    dirName,
    children: [],
  }
  const findOrCreateDir = (parent: SkillFileTreeNode, label: string, path: string) => {
    let node = parent.children.find(item => item.type === 'dir' && item.label === label)
    if (!node) {
      node = { key: `${dirName}:${path}`, label, path, type: 'dir', dirName, children: [] }
      parent.children.push(node)
    }
    return node
  }
  files.forEach((filePath) => {
    const parts = String(filePath || '').split('/').filter(Boolean)
    if (parts.length === 0) return
    let parent = root
    let currentPath = ''
    parts.forEach((part, index) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part
      if (index === parts.length - 1) {
        parent.children.push({ key: `${dirName}:${currentPath}`, label: part, path: currentPath, type: 'file', dirName, children: [] })
      } else {
        parent = findOrCreateDir(parent, part, currentPath)
      }
    })
  })
  const sortNodes = (nodes: SkillFileTreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
      return a.label.localeCompare(b.label)
    })
    nodes.forEach(node => sortNodes(node.children))
  }
  sortNodes(root.children)
  return root
}
const fileTree = computed<SkillFileTreeNode[]>(() => [{
  key: 'skill-root',
  label: getSkillDirectoryName.value,
  path: '',
  type: 'root',
  children: [
    { key: 'skill-root:SKILL.md', label: 'SKILL.md', path: 'SKILL.md', type: 'skill', children: [] },
    buildFixedDirNode('references', skillFiles.value.references || selectedSkill.value?.references || []),
    buildFixedDirNode('assets', skillFiles.value.assets || selectedSkill.value?.assets || []),
    ...(props.agent === 'query' ? [] : [buildFixedDirNode('scripts', skillFiles.value.scripts || selectedSkill.value?.scripts || [])]),
  ],
}])
const getSkillStatusLabel = (skill?: SkillInfo | null) => {
  if (!skill) return '-'
  if (skill.source === 'system') return '已发布'
  if (skill.publish_status === 'published') return '已发布'
  if (skill.publish_status === 'disabled') return '已停用'
  return '草稿'
}

const getSkillDraftLabel = (skill?: SkillInfo | null) => {
  if (!skill?.has_draft) return ''
  return `草稿 v${skill.draft_version_no || ''}`
}

const getVisibilityLabel = (visibility?: string) => {
  return visibility === 'global' ? '全局' : '私有'
}

const getSkillScopeLabel = (scope?: string) => {
  return skillScopeOptions.find(item => item.value === scope)?.label || '智能分析'
}

const canActivateVersion = (version: SkillVersionInfo) => {
  return Boolean(selectedSkill.value?.source === 'user' && !version.current && version.version_publish_status === 'published')
}

const getVersionStateLabel = (version: SkillVersionInfo) => {
  if (version.version_publish_status === 'draft') return '草稿'
  if (version.version_publish_status === 'disabled') return '停用'
  return ''
}

const formatVersionMeta = (version: SkillVersionInfo) => {
  const operator = version.update_by_name || version.create_by_name || '-'
  const time = version.update_time || version.create_time || '-'
  return `${operator} · ${time}`
}

const selectDraftVersion = async () => {
  const draftNo = selectedSkill.value?.draft_version_no || 0
  if (!draftNo) return
  const draft = skillVersions.value.find(item => item.skill_version_no === draftNo)
  if (draft) {
    await selectVersion(draft)
  } else {
    selectedVersion.value = null
  }
}

const resetForm = () => {
  form.skill_name = ''
  form.skill_title = ''
  form.skill_desc = ''
  form.skill_category = 'custom'
  form.skill_scope = props.defaultSkillScope || 'analyze'
  form.visibility_type = 'private'
  form.content = ''
}

const openCreatePanel = () => {
  editingSkillId.value = null
  resetForm()
  showCreatePanel.value = true
}

const openEditPanel = () => {
  const skill = selectedSkill.value
  if (!skill || skill.source !== 'user') return
  editingSkillId.value = skill.skill_id || skill.name
  form.skill_name = skill.name || ''
  form.skill_title = skill.title || skill.name || ''
  form.skill_desc = skill.description || ''
  form.skill_category = skill.category || 'custom'
  form.skill_scope = (skill.skill_scope || 'analyze') as SkillScope
  form.visibility_type = skill.visibility_type === 'global' ? 'global' : 'private'
  form.content = ''
  showCreatePanel.value = true
}

const loadSkills = async () => {
  loading.value = true
  try {
    const response = await getSkills({ scope: activeScope.value, agent: props.agent })
    skills.value = response.data?.skills || []
    if (selectedSkill.value && !skills.value.some(item => item.name === selectedSkill.value?.name || item.skill_id === selectedSkill.value?.skill_id)) {
      selectedSkill.value = null
      skillVersions.value = []
      selectedVersion.value = null
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '加载技能失败')
    skills.value = []
  } finally {
    loading.value = false
  }
}

const loadSkillVersions = async (skill: SkillInfo) => {
  const skillId = skill.skill_id || skill.name
  if (!skillId || skill.source !== 'user') {
    skillVersions.value = []
    selectedVersion.value = null
    return
  }
  versionLoading.value = true
  try {
    const response = await getSkillVersions(skillId)
    skillVersions.value = response.data?.versions || []
    selectedVersion.value = null
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '加载版本失败')
    skillVersions.value = []
    selectedVersion.value = null
  } finally {
    versionLoading.value = false
  }
}

const loadSkillFiles = async () => {
  const skillId = selectedSkill.value?.skill_id || selectedSkill.value?.name
  const versionNo = currentVersionNo.value
  if (!skillId || selectedSkill.value?.source !== 'user' || !versionNo) {
    skillFiles.value = {
      references: selectedSkill.value?.references || [],
      assets: selectedSkill.value?.assets || [],
      scripts: selectedSkill.value?.scripts || [],
    }
    return
  }
  fileLoading.value = true
  try {
    const response = await getSkillVersionFiles(skillId, versionNo)
    skillFiles.value = {
      references: response.data?.references || [],
      assets: response.data?.assets || [],
      scripts: response.data?.scripts || [],
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '加载技能文件失败')
    skillFiles.value = { references: [], assets: [], scripts: [] }
  } finally {
    fileLoading.value = false
  }
}

const selectSkill = async (skill: SkillInfo) => {
  try {
    const response = await getSkill(skill.skill_id || skill.name)
    selectedSkill.value = response.data ? { ...skill, ...response.data } : skill
    await loadSkillVersions(selectedSkill.value)
    await loadSkillFiles()
    selectedFileNode.value = null
    selectedFileEditing.value = false
    resetFileForm()
    contentViewMode.value = 'markdown'
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '加载技能详情失败')
  }
}

const selectSkillById = async (skillId?: string | null) => {
  const targetId = String(skillId || '').trim()
  if (!targetId) return
  if (!skills.value.length) {
    await loadSkills()
  }
  const matched = skills.value.find(skill => skill.skill_id === targetId || skill.name === targetId)
  if (matched) {
    await selectSkill(matched)
  }
}

const selectVersion = async (version: SkillVersionInfo) => {
  const skillId = selectedSkill.value?.skill_id || selectedSkill.value?.name
  if (!skillId) return
  try {
    const response = await getSkillVersion(skillId, version.skill_version_no)
    selectedVersion.value = response.data || version
    await loadSkillFiles()
    selectedFileNode.value = null
    selectedFileEditing.value = false
    resetFileForm()
    contentViewMode.value = 'markdown'
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '加载版本内容失败')
  }
}

const resetFileForm = () => {
  fileForm.dir_name = 'references'
  fileForm.file_path = ''
  fileForm.content = ''
}

const getDefaultFileName = (dirName: SkillFileDir) => {
  if (dirName === 'scripts') return 'new_script.py'
  if (dirName === 'assets') return 'new_file.txt'
  return 'new_doc.md'
}

const openCreateFilePanel = (dirName: SkillFileDir, parentPath = '') => {
  if (!canCreateFileInDir(dirName)) return
  resetFileForm()
  fileForm.dir_name = dirName
  fileForm.file_path = parentPath ? `${parentPath}/${getDefaultFileName(dirName)}` : getDefaultFileName(dirName)
  fileForm.content = ''
  selectedFileNode.value = { type: 'file', dir_name: dirName, file_path: fileForm.file_path }
  selectedFileEditing.value = true
  contentViewMode.value = 'text'
}

const selectSkillContentFile = (editing = false) => {
  selectedFileNode.value = { type: 'skill', file_path: 'SKILL.md' }
  selectedFileEditing.value = editing
  fileForm.dir_name = 'references'
  fileForm.file_path = 'SKILL.md'
  fileForm.content = editing ? (selectedSkill.value?.content || '') : (displayedContent.value || '')
  contentViewMode.value = editing ? 'text' : 'markdown'
}

const openFileContent = async (dirName: SkillFileDir, filePath: string, editing = false) => {
  if (!canAccessFileDir(dirName)) return
  const skillId = selectedSkill.value?.skill_id || selectedSkill.value?.name
  const versionNo = editing ? editableVersionNo.value : currentVersionNo.value
  if (!skillId || !versionNo) return
  try {
    const response = await getSkillVersionFile(skillId, versionNo, { dir_name: dirName, file_path: filePath })
    fileForm.dir_name = dirName
    fileForm.file_path = filePath
    fileForm.content = response.data?.content || ''
    selectedFileNode.value = { type: 'file', dir_name: dirName, file_path: filePath }
    selectedFileEditing.value = editing
    contentViewMode.value = editing ? 'text' : 'markdown'
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '读取文件失败')
  }
}

const handleSaveFile = async () => {
  if (!canEditSelectedFile.value) {
    ElMessage.warning('系统技能文件仅支持查看')
    return
  }
  if (selectedFileNode.value?.type === 'file' && !canAccessFileDir(fileForm.dir_name)) {
    ElMessage.warning('智能问数技能不支持脚本文件')
    return
  }
  const skillId = selectedSkill.value?.skill_id || selectedSkill.value?.name
  const versionNo = currentVersionNo.value
  if (!skillId || !versionNo || !fileForm.file_path.trim()) {
    ElMessage.warning('请填写文件路径')
    return
  }
  fileSaving.value = true
  try {
    if (selectedFileNode.value?.type === 'skill') {
      await updateSkill(skillId, {
        skill_title: selectedSkill.value?.title || selectedSkill.value?.name || '',
        skill_desc: selectedSkill.value?.description || '',
        skill_category: selectedSkill.value?.category || 'custom',
        skill_scope: selectedSkill.value?.skill_scope || 'analyze',
        visibility_type: selectedSkill.value?.visibility_type === 'global' ? 'global' : 'private',
        content: fileForm.content,
      })
    } else {
      await saveSkillVersionFile(skillId, versionNo, {
        dir_name: fileForm.dir_name,
        file_path: fileForm.file_path.trim(),
        content: fileForm.content,
      })
    }
    ElMessage.success('文件已保存')
    const previousNode = selectedFileNode.value ? { ...selectedFileNode.value } : null
    const previousEditing = selectedFileEditing.value
    const previousMode = contentViewMode.value
    const response = await getSkill(skillId)
    if (response.data) {
      selectedSkill.value = { ...selectedSkill.value, ...response.data }
    }
    if (selectedSkill.value) {
      await loadSkillVersions(selectedSkill.value)
    }
    await selectDraftVersion()
    await loadSkillFiles()
    if (previousNode?.type === 'skill') {
      selectSkillContentFile(previousEditing)
    } else if (previousNode?.type === 'file' && previousNode.dir_name) {
      await openFileContent(previousNode.dir_name, previousNode.file_path, previousEditing)
    }
    contentViewMode.value = previousMode
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '保存文件失败')
  } finally {
    fileSaving.value = false
  }
}

const handleDeleteFile = async (dirName: SkillFileDir, filePath: string) => {
  if (!canAccessFileDir(dirName)) return
  const skillId = selectedSkill.value?.skill_id || selectedSkill.value?.name
  const versionNo = currentVersionNo.value
  if (!skillId || !versionNo) return
  try {
    await deleteSkillVersionFile(skillId, versionNo, { dir_name: dirName, file_path: filePath })
    ElMessage.success('文件已删除')
    const response = await getSkill(skillId)
    if (response.data) {
      selectedSkill.value = { ...selectedSkill.value, ...response.data }
      await loadSkillVersions(selectedSkill.value)
      await selectDraftVersion()
    }
    if (selectedFileNode.value?.type === 'file' && selectedFileNode.value.dir_name === dirName && selectedFileNode.value.file_path === filePath) {
      selectedFileNode.value = null
      selectedFileEditing.value = false
      resetFileForm()
    }
    await loadSkillFiles()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '删除文件失败')
  }
}

const handleCreate = async () => {
  if (!editingSkillId.value && !form.skill_name.trim()) {
    ElMessage.warning('请填写技能名')
    return
  }
  if (!form.skill_title.trim()) {
    ElMessage.warning('请填写标题')
    return
  }
  if (!editingSkillId.value && !/^[a-z][a-z-]{1,62}[a-z]$/.test(form.skill_name.trim())) {
    ElMessage.warning('技能名称仅支持小写字母和中划线，需以字母开头和结尾')
    return
  }
  saving.value = true
  try {
    const response = editingSkillId.value
      ? await updateSkill(editingSkillId.value, {
        skill_title: form.skill_title,
        skill_desc: form.skill_desc,
        skill_category: form.skill_category,
        skill_scope: form.skill_scope,
        visibility_type: form.visibility_type,
      })
      : await createSkill({
        skill_name: form.skill_name.trim(),
        skill_title: form.skill_title,
        skill_desc: form.skill_desc,
        skill_category: form.skill_category,
        skill_scope: form.skill_scope,
        visibility_type: form.visibility_type,
        content: form.content,
      })
    ElMessage.success(editingSkillId.value ? '已保存' : '已创建草稿')
    showCreatePanel.value = false
    editingSkillId.value = null
    resetForm()
    await loadSkills()
    if (response.data) {
      selectedSkill.value = response.data
      await loadSkillVersions(selectedSkill.value)
      await loadSkillFiles()
      contentViewMode.value = 'markdown'
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || (editingSkillId.value ? '保存技能失败' : '创建技能失败'))
  } finally {
    saving.value = false
  }
}

const handlePublish = async (skill: SkillInfo) => {
  const skillId = skill.skill_id || skill.name
  if (!skillId) return
  try {
    const response = await publishSkill(skillId)
    ElMessage.success('已发布')
    await loadSkills()
    if (response.data) {
      selectedSkill.value = response.data
      await loadSkillVersions(selectedSkill.value)
      await loadSkillFiles()
      contentViewMode.value = 'markdown'
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '发布失败')
  }
}

const handleActivateVersion = async (version: SkillVersionInfo) => {
  const skillId = selectedSkill.value?.skill_id || selectedSkill.value?.name
  if (!skillId || !canActivateVersion(version)) return
  try {
    const response = await rollbackSkillVersion(skillId, version.skill_version_no)
    ElMessage.success(`已选择 v${version.skill_version_no} 为当前版本`)
    await loadSkills()
    if (response.data) {
      selectedSkill.value = response.data
      await loadSkillVersions(selectedSkill.value)
      selectedVersion.value = null
      await loadSkillFiles()
      contentViewMode.value = 'markdown'
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '选择版本失败')
  }
}

const toggleFavorite = async (skill: SkillInfo, event?: MouseEvent) => {
  event?.stopPropagation()
  const skillId = skill.skill_id || skill.name
  if (!skillId) return
  try {
    if (skill.favorite) {
      await unfavoriteSkill(skillId)
      skill.favorite = false
    } else {
      await favoriteSkill(skillId)
      skill.favorite = true
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '操作失败')
  }
}

const handleDeleteSkill = async (skill?: SkillInfo | null, event?: MouseEvent) => {
  event?.stopPropagation()
  const targetSkill = skill || selectedSkill.value
  const skillId = targetSkill?.skill_id || targetSkill?.name
  if (!skillId || !canDeleteSkill(targetSkill)) return
  try {
    await ElMessageBox.confirm(
      `确定删除技能「${targetSkill?.title || targetSkill?.name || skillId}」吗？`,
      '删除技能',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  try {
    await deleteSkill(skillId)
    ElMessage.success('技能已删除')
    if (selectedSkill.value?.skill_id === targetSkill?.skill_id || selectedSkill.value?.name === targetSkill?.name) {
      selectedSkill.value = null
      selectedVersion.value = null
      skillVersions.value = []
      skillFiles.value = { references: [], assets: [], scripts: [] }
      selectedFileNode.value = null
      selectedFileEditing.value = false
      resetFileForm()
    }
    await loadSkills()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '删除技能失败')
  }
}

const handleCopySkill = async (skill: SkillInfo, event?: MouseEvent) => {
  event?.stopPropagation()
  const skillId = skill.skill_id || skill.name
  if (!skillId || !canCopySkill(skill)) return
  try {
    await ElMessageBox.confirm(
      `确定复制技能「${skill.title || skill.name || skillId}」吗？复制后会生成一个私有草稿副本。`,
      '复制技能',
      {
        confirmButtonText: '复制',
        cancelButtonText: '取消',
        type: 'info',
      },
    )
  } catch {
    return
  }
  try {
    const response = await copySkill(skillId)
    ElMessage.success('技能已复制')
    await loadSkills()
    if (response.data) {
      selectedSkill.value = response.data
      await loadSkillVersions(selectedSkill.value)
      await loadSkillFiles()
      selectedFileNode.value = null
      selectedFileEditing.value = false
      resetFileForm()
      contentViewMode.value = 'markdown'
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '复制技能失败')
  }
}

onMounted(() => {
  loadSkills().then(() => selectSkillById(props.initialSkillId))
})

watch(() => props.initialSkillId, (skillId) => {
  selectSkillById(skillId)
})
</script>

<template>
  <div class="skill-manager h-full min-h-0 flex bg-[#F7F8FA]">
    <aside class="w-[420px] shrink-0 border-r border-slate-200 bg-white flex flex-col min-h-0">
      <div class="px-4 py-3 border-b border-slate-100">
        <div class="flex items-center justify-between gap-2">
          <div>
            <h3 class="m-0 text-sm font-semibold text-slate-800">技能管理</h3>
            <p class="m-0 mt-1 text-xs text-slate-400">系统技能、用户技能和收藏</p>
          </div>
          <button type="button" class="h-8 px-3 rounded-lg border-none bg-[#1677FF] text-xs text-white" @click="openCreatePanel">新建</button>
        </div>
        <div class="mt-3 flex items-center gap-1 rounded-lg bg-slate-100 p-1">
          <button
            v-for="item in scopeOptions"
            :key="item.key"
            :class="[
              'h-7 flex-1 rounded-md border-none text-xs transition-all',
              activeScope === item.key ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
            ]"
            @click="activeScope = item.key; loadSkills()"
          >
            {{ item.label }}
          </button>
        </div>
        <input
          v-model="keyword"
          class="mt-3 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-blue-300"
          placeholder="搜索技能"
        />
      </div>

      <div class="flex-1 min-h-0 overflow-y-auto p-3">
        <div v-if="loading" class="py-8 text-center text-xs text-slate-400">加载中...</div>
        <div v-else-if="displayedSkills.length === 0" class="py-8 text-center text-xs text-slate-400">暂无技能</div>
        <button
          v-for="skill in displayedSkills"
          v-else
          :key="skill.skill_id || skill.name"
          :class="[
            'mb-2 w-full rounded-lg border px-3 py-3 text-left transition-all',
            selectedSkill?.name === skill.name ? 'border-blue-200 bg-blue-50' : 'border-slate-100 bg-white hover:border-slate-200 hover:bg-slate-50'
          ]"
          @click="selectSkill(skill)"
        >
          <div class="flex items-start gap-2">
            <div class="min-w-0 flex-1">
              <div class="flex min-w-0 items-center gap-2">
                <span class="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">{{ skill.title || skill.name }}</span>
                <span class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{{ getSkillCreatorLabel(skill) }}</span>
                <span class="shrink-0 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600">{{ getSkillScopeLabel(skill.skill_scope) }}</span>
              </div>
              <div class="mt-1 truncate text-xs text-slate-400">{{ skill.description || skill.name }}</div>
            </div>
            <div class="flex h-6 shrink-0 items-center justify-end gap-1">
              <button
                v-if="canCopySkill(skill)"
                type="button"
                class="h-6 w-6 rounded border-none bg-transparent text-slate-300 hover:bg-blue-50 hover:text-blue-500"
                title="复制技能"
                @click="handleCopySkill(skill, $event)"
              >
                <i class="fa-regular fa-copy"></i>
              </button>
              <button
                v-if="canDeleteSkill(skill)"
                type="button"
                class="h-6 w-6 rounded border-none bg-transparent text-slate-300 hover:bg-red-50 hover:text-red-500"
                title="删除技能"
                @click="handleDeleteSkill(skill, $event)"
              >
                <i class="fa-regular fa-trash-can"></i>
              </button>
              <button type="button" class="h-6 w-6 rounded border-none bg-transparent text-slate-300 hover:bg-amber-50 hover:text-amber-400" title="收藏" @click="toggleFavorite(skill, $event)">
                <i :class="skill.favorite ? 'fa-solid fa-star text-amber-400' : 'fa-regular fa-star'"></i>
              </button>
            </div>
          </div>
        </button>
      </div>

    </aside>

    <main class="flex-1 min-w-0 min-h-0 overflow-hidden">
      <div v-if="showCreatePanel" class="h-full overflow-y-auto bg-white p-5">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="m-0 text-base font-semibold text-slate-800">{{ editingSkillId ? '编辑用户技能' : '新建用户技能' }}</h3>
          <button type="button" class="border-none bg-transparent text-sm text-slate-400 hover:text-slate-600" @click="showCreatePanel = false; editingSkillId = null">关闭</button>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <label class="text-xs text-slate-500">
            技能名称
            <input
              v-model="form.skill_name"
              :disabled="Boolean(editingSkillId)"
              class="mt-1 h-9 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-800 outline-none placeholder:text-slate-300 disabled:bg-slate-50 disabled:text-slate-400"
              placeholder="小写字母和中划线，需以字母开头和结尾"
            />
          </label>
          <label class="text-xs text-slate-500">标题<input v-model="form.skill_title" class="mt-1 h-9 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-800 outline-none" /></label>
          <label class="text-xs text-slate-500">
            适用范围
            <select v-model="form.skill_scope" class="mt-1 h-9 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-800 outline-none">
              <option v-for="item in skillScopeOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
          </label>
          <label class="text-xs text-slate-500">可见范围<select v-model="form.visibility_type" class="mt-1 h-9 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-800 outline-none"><option value="private">私有</option><option value="global">全局</option></select></label>
        </div>
        <label class="mt-4 block text-xs text-slate-500">描述<input v-model="form.skill_desc" class="mt-1 h-9 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-800 outline-none" /></label>
        <label v-if="!editingSkillId" class="mt-4 block text-xs text-slate-500">技能内容<textarea v-model="form.content" class="mt-1 h-[360px] w-full resize-none rounded-lg border border-slate-200 p-3 font-mono text-sm text-slate-800 outline-none" placeholder="# 技能说明"></textarea></label>
        <div v-else class="mt-4 rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-xs text-slate-500">
          文件内容请在下方文件目录中选择文件后，通过预览/源码区域修改。
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="h-9 px-4 rounded-lg border border-slate-200 bg-white text-sm text-slate-600" @click="showCreatePanel = false; editingSkillId = null">取消</button>
          <button type="button" class="h-9 px-4 rounded-lg border-none bg-[#1677FF] text-sm text-white disabled:opacity-50" :disabled="saving" @click="handleCreate">{{ editingSkillId ? '保存' : '保存草稿' }}</button>
        </div>
      </div>

      <div v-else-if="selectedSkill" class="h-full overflow-y-auto bg-white p-5">
        <div class="mb-4 flex items-start justify-between gap-4">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <h3 class="m-0 truncate text-base font-semibold text-slate-800">{{ selectedSkill.title || selectedSkill.name }}</h3>
              <span class="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{{ selectedSkill.source === 'system' ? '系统技能' : '用户技能' }}</span>
            </div>
            <p class="m-0 mt-1 text-sm text-slate-500">{{ selectedSkill.description || '暂无描述' }}</p>
          </div>
          <button
            v-if="selectedSkill.source === 'user'"
            type="button"
            class="h-8 shrink-0 rounded border border-slate-200 bg-white px-3 text-xs text-slate-600 hover:bg-slate-50"
            @click="openEditPanel"
          >
            编辑元数据
          </button>
        </div>
        <div class="mb-4 grid grid-cols-2 gap-3 text-xs">
          <div class="rounded-lg bg-slate-50 p-3"><div class="text-slate-400">适用范围</div><div class="mt-1 truncate text-slate-700">{{ getSkillScopeLabel(selectedSkill.skill_scope) }}</div></div>
          <div class="rounded-lg bg-slate-50 p-3"><div class="text-slate-400">可见范围</div><div class="mt-1 truncate text-slate-700">{{ getVisibilityLabel(selectedSkill.visibility_type) }}</div></div>
        </div>
        <div v-if="selectedSkill.source === 'user'" class="mb-4 rounded-lg border border-slate-200 bg-white p-3">
          <div class="mb-2 flex items-center justify-between">
            <div class="flex items-center gap-2 text-xs font-medium text-slate-700">
              <span>版本</span>
            </div>
            <div v-if="versionLoading" class="text-xs text-slate-400">加载中...</div>
          </div>
          <div v-if="!versionLoading && skillVersions.length === 0" class="text-xs text-slate-400">暂无版本</div>
          <div v-else class="skill-version-list space-y-2 pr-1">
            <div
              v-for="version in skillVersions"
              :key="version.skill_version_no"
              :class="[
                'flex items-center gap-3 rounded-lg border px-3 py-2 text-xs',
                selectedVersion?.skill_version_no === version.skill_version_no ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-slate-200 bg-slate-50 text-slate-600'
              ]"
            >
              <label v-if="version.version_publish_status === 'published'" class="flex shrink-0 cursor-pointer items-center gap-2 text-xs text-slate-700">
                <input
                  type="radio"
                  class="h-4 w-4 cursor-pointer accent-blue-600"
                  name="skill-active-version"
                  :checked="version.current"
                  :disabled="version.current"
                  @change="handleActivateVersion(version)"
                />
                <span>选择</span>
              </label>
              <div v-else class="w-[74px] shrink-0 text-xs text-amber-600">{{ getVersionStateLabel(version) }}</div>
              <div class="min-w-0 flex-1 truncate">
                <span class="font-medium">v{{ version.skill_version_no }}</span>
                <span class="ml-4 text-slate-400">{{ formatVersionMeta(version) }}</span>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  class="h-7 rounded border border-slate-200 bg-white px-3 text-xs text-slate-600 hover:bg-slate-50"
                  @click="selectVersion(version)"
                >
                  查看
                </button>
                <button
                  v-if="version.version_publish_status === 'draft'"
                  type="button"
                  class="h-7 rounded border-none bg-[#1677FF] px-3 text-xs text-white"
                  @click="handlePublish(selectedSkill)"
                >
                  发布
                </button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="canShowFileTree" class="mb-4 rounded-lg border border-slate-200 bg-white p-3">
          <div class="mb-3 flex items-center justify-between">
            <div class="text-xs font-medium text-slate-700">文件目录</div>
            <div v-if="fileLoading" class="text-xs text-slate-400">加载中...</div>
          </div>
          <div class="rounded-lg bg-slate-50 p-3">
            <el-tree
              class="skill-file-tree"
              :data="fileTree"
              node-key="key"
              default-expand-all
              :expand-on-click-node="false"
            >
              <template #default="{ node, data }">
                <div class="flex min-w-0 flex-1 items-center gap-2 text-xs">
                  <i :class="(data.type === 'root' || data.type === 'dir') ? 'fa-regular fa-folder text-amber-500' : 'fa-regular fa-file-lines text-slate-400'"></i>
                  <button
                    type="button"
                    :class="[
                      'min-w-0 flex-1 truncate border-none bg-transparent text-left font-mono',
                      data.type === 'file' ? 'text-slate-700 hover:text-blue-600' : 'text-slate-500'
                    ]"
                    @click="data.type === 'skill' ? selectSkillContentFile(false) : (data.type === 'file' && data.dirName && openFileContent(data.dirName, data.path, false))"
                  >
                    {{ node.label }}
                  </button>
                  <span
                    v-if="data.type === 'dir' && data.path === '' && data.dirName"
                    class="shrink-0 text-xs text-slate-400"
                  >
                    {{ getSkillDirDescription(data.dirName) }}
                  </span>
                  <button
                    v-if="canManageFiles && data.type === 'dir' && data.dirName && canCreateFileInDir(data.dirName)"
                    type="button"
                    class="h-6 rounded border border-slate-200 bg-white px-2 text-xs text-slate-600"
                    @click="openCreateFilePanel(data.dirName, data.path)"
                  >
                    新增
                  </button>
                  <button
                    v-if="canManageFiles && data.type === 'skill'"
                    type="button"
                    class="shrink-0 border-none bg-transparent text-slate-400 hover:text-blue-600"
                    @click.stop="selectSkillContentFile(true)"
                  >
                    编辑
                  </button>
                  <button
                    v-if="canManageFiles && data.type === 'file' && data.dirName"
                    type="button"
                    class="shrink-0 border-none bg-transparent text-slate-400 hover:text-blue-600"
                    @click.stop="openFileContent(data.dirName, data.path, true)"
                  >
                    编辑
                  </button>
                  <button
                    v-if="canManageFiles && data.type === 'file' && data.dirName"
                    type="button"
                    class="shrink-0 border-none bg-transparent text-slate-300 hover:text-red-500"
                    @click.stop="handleDeleteFile(data.dirName, data.path)"
                  >
                    删除
                  </button>
                </div>
              </template>
            </el-tree>
          </div>
        </div>
        <div class="mb-2 flex items-center justify-between gap-2">
          <div class="min-w-0 truncate font-mono text-xs text-slate-500">{{ selectedFileNode ? selectedFileTitle : 'SKILL.md' }}</div>
          <div class="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1">
            <button
              type="button"
              :class="[
                'h-7 rounded-md border-none px-3 text-xs transition-all',
                contentViewMode === 'markdown' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              ]"
              @click="contentViewMode = 'markdown'"
            >
              预览
            </button>
            <button
              type="button"
              :class="[
                'h-7 rounded-md border-none px-3 text-xs transition-all',
                contentViewMode === 'text' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              ]"
              @click="contentViewMode = 'text'"
            >
              源码
            </button>
          </div>
        </div>
        <div
          v-if="contentViewMode === 'markdown'"
          class="skill-markdown min-h-[420px] rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm leading-relaxed text-slate-700"
          v-html="displayedContentHtml"
        ></div>
        <div v-else>
          <div v-if="canEditSelectedFile && selectedFileNode?.type === 'file'" class="mb-2 grid grid-cols-[160px_1fr] gap-3">
            <label class="text-xs text-slate-500">目录<select v-model="fileForm.dir_name" class="mt-1 h-8 w-full rounded border border-slate-200 bg-white px-2 text-xs outline-none"><option v-for="dirName in availableFileDirs" :key="dirName" :value="dirName">{{ dirName }}</option></select></label>
            <label class="text-xs text-slate-500">文件路径<input v-model="fileForm.file_path" class="mt-1 h-8 w-full rounded border border-slate-200 bg-white px-2 font-mono text-xs outline-none" placeholder="请输入文件名或相对路径，例如 guide.md" /></label>
          </div>
          <textarea
            v-if="canEditSelectedFile"
            v-model="fileForm.content"
            class="min-h-[420px] w-full resize-y rounded-lg border border-slate-200 bg-slate-50 p-4 font-mono text-sm leading-relaxed text-slate-700 outline-none focus:border-blue-300"
          ></textarea>
          <pre
            v-else
            class="m-0 min-h-[420px] overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-4 font-mono text-sm leading-relaxed text-slate-700"
          >{{ selectedFileContent || '暂无内容' }}</pre>
          <div v-if="canEditSelectedFile" class="mt-3 flex justify-end">
            <button type="button" class="h-8 rounded border-none bg-[#1677FF] px-3 text-xs text-white disabled:opacity-50" :disabled="fileSaving" @click="handleSaveFile">保存文件</button>
          </div>
        </div>
      </div>

      <div v-else class="flex h-full items-center justify-center text-sm text-slate-400">请选择一个技能</div>
    </main>
  </div>
</template>

<style scoped>
.skill-manager button {
  appearance: none;
  font: inherit;
  cursor: pointer;
}

.skill-version-list {
  display: block;
  max-height: 160px !important;
  overflow-x: hidden;
  overflow-y: auto !important;
}

.skill-file-tree {
  background: transparent;
}

.skill-file-tree :deep(.el-tree-node__content) {
  min-height: 30px;
  border-radius: 6px;
}

.skill-file-tree :deep(.el-tree-node__content:hover) {
  background: #fff;
}

.skill-markdown :deep(h1) {
  margin: 0 0 18px;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.35;
  color: #1e293b;
}

.skill-markdown :deep(h2) {
  margin: 24px 0 12px;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.4;
  color: #1e293b;
}

.skill-markdown :deep(h3) {
  margin: 18px 0 10px;
  font-size: 15px;
  font-weight: 700;
  color: #334155;
}

.skill-markdown :deep(p) {
  margin: 0 0 12px;
}

.skill-markdown :deep(ul),
.skill-markdown :deep(ol) {
  margin: 0 0 12px 20px;
  padding: 0;
}

.skill-markdown :deep(li) {
  margin: 4px 0;
}

.skill-markdown :deep(code) {
  border-radius: 4px;
  background: #e2e8f0;
  padding: 1px 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 0.92em;
}

.skill-markdown :deep(pre) {
  margin: 12px 0;
  overflow: auto;
  border-radius: 8px;
  background: #0f172a;
  padding: 12px;
  color: #e2e8f0;
}

.skill-markdown :deep(pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
}

.skill-markdown :deep(blockquote) {
  margin: 12px 0;
  border-left: 3px solid #bfdbfe;
  padding-left: 12px;
  color: #64748b;
}
</style>
