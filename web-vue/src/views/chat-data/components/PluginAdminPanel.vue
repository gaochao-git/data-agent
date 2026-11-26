<script setup lang="ts">
/**
 * 后台管理 · 插件面板。
 * 已安装插件列表 + zip 安装 + 启停 + 卸载；安装/启停对新会话生效（接口语义见 plugins/README.md）。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import { downloadMcpServerTemplate, downloadPluginTemplate, getAdminPlugins, installPlugin, probeAdminPluginMcpServerTools, setAdminPluginEnabled, setAdminPluginPermissions, uninstallPlugin } from '../api'
import type { PluginInfo, PluginMcpServerInfo, PluginMcpToolInfo } from '../types'
import AdminPanelToolbar from './AdminPanelToolbar.vue'

// 与后端 installer 的 zip 上限一致（50MB）
const PLUGIN_UPLOAD_MAX_BYTES = 50 * 1024 * 1024

const loading = ref(false)
const plugins = ref<PluginInfo[]>([])
const hasLoaded = ref(false)
const uploadRef = ref<UploadInstance | null>(null)
const installing = ref(false)
const downloadingTemplate = ref(false)
const downloadingMcpTemplate = ref(false)
const probeLoading = ref(false)
const probeError = ref('')
const probePluginName = ref('')
const probeServerId = ref('')
const probeTools = ref<PluginMcpToolInfo[]>([])
const selectedProbeTool = ref<PluginMcpToolInfo | null>(null)
const mcpServersVisible = ref(false)
const mcpServersPlugin = ref<PluginInfo | null>(null)
const mcpServersPluginLabel = computed(() => mcpServersPlugin.value?.display_name || mcpServersPlugin.value?.name || '')
const mcpServers = computed(() => mcpServersPlugin.value?.mcp_servers || [])
const failedMcpServerCount = computed(() =>
  mcpServers.value.filter((server) => server.admission && !server.admission.ok).length
)
const probeBreadcrumb = computed(() => {
  const parts = [mcpServersPluginLabel.value || probePluginName.value, probeServerId.value]
  if (selectedProbeTool.value) {
    parts.push(selectedProbeTool.value.label || selectedProbeTool.value.name, '参数')
  } else {
    parts.push('工具列表')
  }
  return parts.filter(Boolean).join(' / ')
})
const permissionVisible = ref(false)
const permissionSaving = ref(false)
const permissionPlugin = ref<PluginInfo | null>(null)
const permissionScope = ref<PluginInfo['scope_type']>('all')
const permissionUserNames = ref<string[]>([])
const permissionPluginLabel = computed(() => permissionPlugin.value?.display_name || permissionPlugin.value?.name || '')

// 每工具配置摘要：approval/timeout 均按工具配置，未配置的工具走默认值
const mcpToolSummary = (server: PluginMcpServerInfo) => {
  const entries = Object.values(server.tools || {})
  if (!entries.length) return '全部默认（ask / 全局超时）'
  const counts = { ask: 0, allow: 0 } as Record<string, number>
  entries.forEach((value) => {
    const approval = value.approval ?? 'ask'
    counts[approval] = (counts[approval] ?? 0) + 1
  })
  const timeoutCount = entries.filter((value) => value.timeout_ms !== undefined).length
  const parts = [
    counts.ask ? `${counts.ask} 执行前审批` : '',
    counts.allow ? `${counts.allow} 免审批` : '',
    timeoutCount ? `${timeoutCount} 个自定义超时` : ''
  ].filter(Boolean)
  return parts.length ? `${parts.join(' / ')}，其余默认 ask` : '全部默认（ask / 全局超时）'
}
// 服务端分页（口径与定时任务一致）
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

const loadPlugins = async () => {
  loading.value = true
  try {
    const res = await getAdminPlugins({
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value
    })
    if (res.status === 'success') {
      plugins.value = res.data.plugins || []
      total.value = res.data.total || 0
      hasLoaded.value = true
    } else {
      ElMessage.error(res.msg || '获取插件列表失败')
    }
  } catch (err) {
    console.error('获取插件列表失败:', err)
    ElMessage.error('获取插件列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const handlePageChange = async () => {
  await loadPlugins()
}

const handleSizeChange = async () => {
  currentPage.value = 1
  await loadPlugins()
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadPlugins()
}

const handleDownloadTemplate = async () => {
  downloadingTemplate.value = true
  try {
    const blob = await downloadPluginTemplate()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'chatdata-plugin-template.zip'
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    console.error('下载插件模板失败:', err)
    ElMessage.error('下载插件模板失败，请稍后重试')
  } finally {
    downloadingTemplate.value = false
  }
}

const handleDownloadMcpTemplate = async () => {
  downloadingMcpTemplate.value = true
  try {
    const blob = await downloadMcpServerTemplate()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'chatdata-mcp-server-template.zip'
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    console.error('下载 MCP Server 模板失败:', err)
    ElMessage.error('下载 MCP Server 模板失败，请稍后重试')
  } finally {
    downloadingMcpTemplate.value = false
  }
}

const handleUploadChange = async (uploadFile: UploadFile) => {
  const file = uploadFile.raw
  if (!file) return
  uploadRef.value?.clearFiles()
  if (!file.name.toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请选择 .zip 插件包')
    return
  }
  if (file.size > PLUGIN_UPLOAD_MAX_BYTES) {
    ElMessage.warning(`插件包超过大小上限 ${PLUGIN_UPLOAD_MAX_BYTES / (1024 * 1024)}MB`)
    return
  }
  installing.value = true
  try {
    const res = await installPlugin(file)
    if (res.status === 'success') {
      const installed = res.data.plugin
      ElMessage.success(`插件 ${installed.display_name || installed.name} 安装成功，对新会话生效`)
      await loadPlugins()
    } else {
      ElMessage.error(res.msg || '安装插件失败')
    }
  } catch (err) {
    console.error('安装插件失败:', err)
    ElMessage.error('安装插件失败，请稍后重试')
  } finally {
    installing.value = false
  }
}

const togglePlugin = async (plugin: PluginInfo, enabled: boolean) => {
  try {
    const res = await setAdminPluginEnabled(plugin.name, enabled)
    if (res.status === 'success') {
      ElMessage.success(enabled ? '插件已启用，对新会话生效' : '插件已禁用')
    } else {
      ElMessage.error(res.msg || '操作失败')
    }
  } catch (err) {
    console.error('切换插件状态失败:', err)
    ElMessage.error('操作失败，请稍后重试')
  } finally {
    await loadPlugins()
  }
}

const removePlugin = async (plugin: PluginInfo) => {
  try {
    await ElMessageBox.confirm(
      `卸载后插件目录将被删除，其中的技能与 MCP 工具声明同时失效（对新会话）。确定卸载 ${plugin.display_name || plugin.name}？`,
      '卸载插件确认',
      { type: 'warning', confirmButtonText: '卸载', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await uninstallPlugin(plugin.name)
    if (res.status === 'success') {
      ElMessage.success('插件已卸载')
      await loadPlugins()
    } else {
      ElMessage.error(res.msg || '卸载插件失败')
    }
  } catch (err) {
    console.error('卸载插件失败:', err)
    ElMessage.error('卸载插件失败，请稍后重试')
  }
}

const openPermissionDialog = (plugin: PluginInfo) => {
  permissionPlugin.value = plugin
  permissionScope.value = plugin.scope_type || 'all'
  permissionUserNames.value = [...(plugin.user_names || [])]
  permissionVisible.value = true
}

const openMcpServersDialog = (plugin: PluginInfo) => {
  resetMcpProbe()
  mcpServersPlugin.value = plugin
  mcpServersVisible.value = true
}

const savePermissions = async () => {
  const plugin = permissionPlugin.value
  if (!plugin) return
  const userNames = Array.from(
    new Set(permissionUserNames.value.map((value) => String(value || '').trim()).filter(Boolean))
  )
  if (permissionScope.value === 'selected' && !userNames.length) {
    ElMessage.warning('请至少选择一个 username')
    return
  }
  permissionSaving.value = true
  try {
    const res = await setAdminPluginPermissions(
      plugin.name,
      permissionScope.value,
      permissionScope.value === 'selected' ? userNames : []
    )
    if (res.status === 'success') {
      ElMessage.success('插件授权已更新，对新会话生效')
      permissionVisible.value = false
      await loadPlugins()
    } else {
      ElMessage.error(res.msg || '更新插件授权失败')
    }
  } catch (err) {
    console.error('更新插件授权失败:', err)
    ElMessage.error('更新插件授权失败，请稍后重试')
  } finally {
    permissionSaving.value = false
  }
}

const probeMcpTools = async (plugin: PluginInfo, server: PluginMcpServerInfo) => {
  const pluginName = plugin.name
  const serverId = server.id
  probeLoading.value = true
  probeError.value = ''
  probePluginName.value = pluginName
  probeServerId.value = serverId
  probeTools.value = []
  selectedProbeTool.value = null
  try {
    const res = await probeAdminPluginMcpServerTools(pluginName, serverId)
    if (probePluginName.value !== pluginName || probeServerId.value !== serverId) return
    if (res.status === 'success') {
      probeTools.value = res.data.tools || []
    } else {
      probeError.value = res.msg || '探测失败'
    }
  } catch (err) {
    if (probePluginName.value !== pluginName || probeServerId.value !== serverId) return
    console.error('探测 MCP 工具失败:', err)
    probeError.value = '连接 MCP server 失败，请检查地址、认证和网络'
  } finally {
    if (probePluginName.value === pluginName && probeServerId.value === serverId) {
      probeLoading.value = false
    }
  }
}

const resetMcpProbe = () => {
  probeLoading.value = false
  probeError.value = ''
  probePluginName.value = ''
  probeServerId.value = ''
  probeTools.value = []
  selectedProbeTool.value = null
}

const probeMcpServerFromList = (server: PluginMcpServerInfo) => {
  const plugin = mcpServersPlugin.value
  if (!plugin) return
  void probeMcpTools(plugin, server)
}

const openProbeTool = (tool: PluginMcpToolInfo) => {
  selectedProbeTool.value = tool
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 工具栏 -->
    <AdminPanelToolbar>
      <template #actions>
        <el-button size="small" :loading="downloadingTemplate" @click="handleDownloadTemplate">
          <i class="fa-solid fa-download" style="margin-right: 4px; font-size: 10px;"></i>
          下载插件模板
        </el-button>
        <el-button size="small" :loading="downloadingMcpTemplate" @click="handleDownloadMcpTemplate">
          <i class="fa-solid fa-server" style="margin-right: 4px; font-size: 10px;"></i>
          下载 MCP 模板
        </el-button>
        <el-upload
          ref="uploadRef"
          action="#"
          accept=".zip"
          :auto-upload="false"
          :show-file-list="false"
          @change="handleUploadChange"
        >
          <el-button type="primary" size="small" round :loading="installing">
            <i class="fa-solid fa-plug" style="margin-right: 4px; font-size: 10px;"></i>
            安装插件
          </el-button>
        </el-upload>
        <el-button size="small" :loading="loading" @click="loadPlugins">
          <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
        </el-button>
      </template>
    </AdminPanelToolbar>

    <!-- 插件列表：服务端分页 -->
    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <el-table
        :data="plugins"
        v-loading="loading"
        size="small"
        height="100%"
        style="width: 100%"
        empty-text="暂无插件，点击右上角“安装插件”上传 zip 包"
      >
        <el-table-column label="插件" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <i class="fa-solid fa-puzzle-piece text-[11px] text-blue-500"></i>
              <div class="min-w-0">
                <div class="text-sm font-medium text-slate-800">{{ row.display_name || row.name }}</div>
                <div class="text-[10px] text-slate-400">{{ row.name }} · v{{ row.version }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.description || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="技能" min-width="150">
          <template #default="{ row }">
            <div v-if="(row.skills || []).length" class="flex flex-wrap gap-1">
              <el-tag v-for="skill in row.skills" :key="skill" size="small" type="info" effect="plain">{{ skill }}</el-tag>
            </div>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="MCP server" width="120" align="center">
          <template #default="{ row }">
            <el-button
              v-if="(row.mcp_servers || []).length"
              link
              size="small"
              :type="(row.mcp_servers || []).some((server: PluginMcpServerInfo) => server.admission && !server.admission.ok) ? 'danger' : 'primary'"
              :title="`${row.mcp_servers.length} 个 MCP server，点击查看列表`"
              @click.stop="openMcpServersDialog(row)"
            >
              <i class="fa-solid fa-server text-[11px]" style="margin-right: 4px;"></i>
              {{ row.mcp_servers.length }} 个
            </el-button>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="创建人" width="100" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.create_by_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.create_time || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              size="small"
              @change="(value: any) => togglePlugin(row, !!value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              size="small"
              type="primary"
              :title="row.scope_type === 'selected' ? '指定人员可用，点击查看和修改' : '全部用户可用，点击查看和修改'"
              @click="openPermissionDialog(row)"
            >
              <i
                class="text-[11px]"
                :class="row.scope_type === 'selected' ? 'fa-solid fa-user-lock' : 'fa-solid fa-users'"
                style="margin-right: 4px;"
              ></i>授权
            </el-button>
            <el-button link size="small" type="danger" @click="removePlugin(row)">
              <i class="fa-solid fa-trash-can text-[11px]" style="margin-right: 4px;"></i>卸载
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    <el-dialog
      v-model="permissionVisible"
      :title="`插件授权 · ${permissionPluginLabel}`"
      width="min(560px, 92vw)"
      destroy-on-close
    >
      <div class="space-y-4">
        <div>
          <div class="mb-2 text-xs font-medium text-slate-700">权限范围</div>
          <el-radio-group v-model="permissionScope">
            <el-radio-button value="all">全部用户</el-radio-button>
            <el-radio-button value="selected">指定用户</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="permissionScope === 'selected'">
          <div class="mb-2 text-xs font-medium text-slate-700">username</div>
          <el-select
            v-model="permissionUserNames"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            placeholder="输入 username"
            class="w-full"
          />
          <div class="mt-2 text-[11px] text-slate-400">{{ permissionUserNames.length }} 个用户</div>
        </div>
      </div>
      <template #footer>
        <el-button :disabled="permissionSaving" @click="permissionVisible = false">取消</el-button>
        <el-button type="primary" :loading="permissionSaving" @click="savePermissions">保存</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="mcpServersVisible"
      :title="`MCP server 列表 · ${mcpServersPluginLabel}`"
      width="min(1040px, 96vw)"
      top="5vh"
      class="mcp-server-dialog"
      destroy-on-close
      @closed="resetMcpProbe"
    >
      <div v-if="failedMcpServerCount" class="mb-3">
        <el-alert
          :title="`${failedMcpServerCount} 个 MCP server 未通过准入，请检查配置`"
          type="error"
          :closable="false"
          show-icon
        />
      </div>
      <el-table :data="mcpServers" size="small" border max-height="300">
        <el-table-column label="Server" min-width="140" show-overflow-tooltip>
          <template #default="{ row: server }">
            <span class="font-mono text-xs text-slate-700">{{ server.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="连接方式" width="120" show-overflow-tooltip>
          <template #default="{ row: server }">
            <span class="text-xs text-slate-600">{{ server.transport }}</span>
          </template>
        </el-table-column>
        <el-table-column label="地址" min-width="180" show-overflow-tooltip>
          <template #default="{ row: server }">
            <span class="text-xs text-slate-500">{{ server.url || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="工具配置" min-width="180" show-overflow-tooltip>
          <template #default="{ row: server }">
            <span class="text-xs text-slate-600">{{ mcpToolSummary(server) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="准入" width="90" align="center">
          <template #default="{ row: server }">
            <el-tooltip
              v-if="server.admission"
              :content="server.admission.ok ? (server.admission.warnings.join('；') || '准入通过') : server.admission.errors.join('；')"
              placement="top"
            >
              <el-tag
                size="small"
                :type="server.admission.ok ? (server.admission.warnings.length ? 'warning' : 'success') : 'danger'"
                effect="plain"
              >
                {{ server.admission.ok ? (server.admission.warnings.length ? '告警' : '通过') : '未通过' }}
              </el-tag>
            </el-tooltip>
            <el-tag v-else size="small" type="info" effect="plain">未知</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center">
          <template #default="{ row: server }">
            <el-button link type="primary" size="small" @click.stop="probeMcpServerFromList(server)">
              探测工具
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="probeServerId" class="mt-4 border-t border-slate-200 pt-4">
        <div class="mb-2 flex min-w-0 items-center justify-between gap-3">
          <span
            class="min-w-0 flex-1 truncate text-[11px] text-slate-500"
            :title="probeBreadcrumb"
            data-qa="mcp-probe-breadcrumb"
          >
            {{ probeBreadcrumb }}
          </span>
          <el-button
            v-if="selectedProbeTool"
            data-qa="mcp-probe-back"
            link
            type="primary"
            size="small"
            @click="selectedProbeTool = null"
          >
            <i class="fa-solid fa-arrow-left text-[11px] mr-1"></i>返回工具列表
          </el-button>
          <el-button v-else link type="primary" size="small" @click="resetMcpProbe">收起探测</el-button>
        </div>
        <div v-if="probeLoading" class="py-8 text-center text-sm text-slate-500">
          <i class="fa-solid fa-spinner fa-spin mr-2"></i>正在连接并探测工具…
        </div>
        <el-alert v-else-if="probeError" :title="probeError" type="error" :closable="false" show-icon />
        <el-table
          v-else-if="selectedProbeTool?.parameters?.length"
          data-qa="mcp-probe-parameters"
          :data="selectedProbeTool.parameters"
          size="small"
          border
          max-height="340"
        >
          <el-table-column label="参数名" min-width="160">
            <template #default="{ row: parameter }">
              <span class="font-mono text-xs text-slate-700">{{ parameter.name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row: parameter }">
              <span class="font-mono text-xs text-slate-600">{{ parameter.type }}</span>
            </template>
          </el-table-column>
          <el-table-column label="必填" width="70" align="center">
            <template #default="{ row: parameter }">
              <el-tag size="small" :type="parameter.required ? 'warning' : 'info'" effect="plain">
                {{ parameter.required ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="280">
            <template #default="{ row: parameter }">
              <span class="text-xs leading-5 text-slate-600">{{ parameter.description || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-else-if="selectedProbeTool"
          data-qa="mcp-probe-parameters"
          description="该工具没有参数"
        />
        <el-empty v-else-if="!probeTools.length" description="该 server 没有返回工具" />
        <el-table
          v-else
          :data="probeTools"
          size="small"
          border
          max-height="340"
        >
          <el-table-column label="工具" width="230">
            <template #default="{ row: tool }">
              <div class="min-w-0">
                <div class="truncate text-xs text-slate-700">{{ tool.label || tool.name }}</div>
                <div class="truncate font-mono text-[10px] text-slate-400">{{ tool.name }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="320">
            <template #default="{ row: tool }">
              <el-tooltip
                effect="dark"
                placement="top"
                popper-class="mcp-tool-description-tooltip"
                :show-after="300"
              >
                <template #content>
                  <div class="max-h-[240px] overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5">
                    {{ tool.description || '-' }}
                  </div>
                </template>
                <span
                  data-qa="mcp-tool-description"
                  class="block overflow-hidden text-ellipsis whitespace-nowrap text-xs text-slate-600"
                >
                  {{ tool.description || '-' }}
                </span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="审批" width="70" align="center">
            <template #default="{ row: tool }">
              <el-tag size="small" :type="tool.approval === 'ask' ? 'warning' : 'success'" effect="plain">
                {{ tool.approval === 'ask' ? 'ask' : 'allow' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="超时" width="90" align="center">
            <template #default="{ row: tool }">
              <span class="text-xs text-slate-500">{{ tool.timeout_ms ? `${tool.timeout_ms}ms` : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="参数" width="70" align="center">
            <template #default="{ row: tool }">
              <span class="text-xs text-slate-500">{{ tool.parameters?.length || 0 }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center">
            <template #default="{ row: tool }">
              <el-button link type="primary" size="small" @click.stop="openProbeTool(tool)">
                查看参数
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="mcpServersVisible = false">关闭</el-button>
      </template>
    </el-dialog>

      <div class="flex shrink-0 justify-end pt-2">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          size="small"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>
  </div>
</template>

<style>
.mcp-server-dialog {
  display: flex;
  flex-direction: column;
  max-height: 90vh;
}

.mcp-server-dialog .el-dialog__header,
.mcp-server-dialog .el-dialog__footer {
  flex-shrink: 0;
}

.mcp-server-dialog .el-dialog__body {
  min-height: 0;
  overflow-y: auto;
}

.mcp-tool-description-tooltip {
  max-width: min(420px, 80vw);
}
</style>
