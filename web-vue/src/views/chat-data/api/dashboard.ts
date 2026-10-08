/**
 * Dashboard API 服务
 * 使用主项目统一的 request 封装
 */

import request from '@/utils/request'
import requestStream from '@/utils/requestStream'
import { parseSseChunk } from '../utils/sse'
import { abortableSleep, createAbortError } from '../utils/abort'
import type { ChartType } from '../chartTypes'
import type { MemberQueryResult } from '../types'

// ============ 类型定义 ============

export interface Dashboard {
  id: string
  name: string
  description?: string
  user_id: string
  datasource_id?: string
  theme: 'light' | 'dark'
  background_id?: string
  is_public: boolean
  can_manage?: boolean
  panels?: Panel[]
  create_time: string
  update_time: string
  create_by?: string
  create_by_name?: string
  update_by?: string
  update_by_name?: string
}

export interface Panel {
  id: string
  dashboard_id: string
  name: string
  sql: string
  chart_type: ChartType
  chart_config?: Record<string, unknown>
  member_datasource_id?: string
  layout: {
    i: string
    x: number
    y: number
    w: number
    h: number
  }
  refresh_interval?: number
  source_thread_id?: string
  datasource_id?: string
  create_time: string
  update_time: string
  create_by?: string
  create_by_name?: string
  update_by?: string
  update_by_name?: string
}

export interface PanelData {
  panel_id: string
  name: string
  chart_type: string
  chart_config?: Record<string, unknown>
  member_datasource_id?: string
  member_name?: string
  result: {
    status?: string | null
    sql?: string
    total_count: number
    execution_time_ms?: number | null
    error?: string | null
    results?: MemberQueryResult[]
  }
}

interface QueryTaskStatusResult<T = unknown> {
  task_id: string
  task_type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'partial_success'
  progress_text?: string
  result?: T | null
  error_message?: string | null
}

interface DashboardBatchTaskResult {
  dashboard_id: string
  total: number
  completed: number
  succeeded: number
  failed: number
  results: Record<string, {
    status: 'pending' | 'succeeded' | 'failed'
    payload?: PanelData
    error?: string
  }>
}

// ============ Dashboard API ============

export async function listDashboards(scope: 'mine' | 'public' = 'mine'): Promise<Dashboard[]> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards`,
    method: 'get',
    params: { scope }
  })
  return res.data.dashboards
}

export async function createDashboard(params: {
  name: string
  description?: string
  datasource_id?: string
  theme?: 'light' | 'dark'
}): Promise<Dashboard> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards`,
    method: 'post',
    data: params
  })
  return res.data
}

export async function publishDashboard(dashboardId: string): Promise<Dashboard> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/publish`,
    method: 'post'
  })
  return res.data
}

export async function unpublishDashboard(dashboardId: string): Promise<Dashboard> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/unpublish`,
    method: 'post'
  })
  return res.data
}

export async function getDashboard(dashboardId: string): Promise<Dashboard> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}`,
    method: 'get'
  })
  return res.data
}

export async function updateDashboard(
  dashboardId: string,
  params: {
    name?: string
    description?: string
    theme?: 'light' | 'dark'
    background_id?: string
  }
): Promise<Dashboard> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}`,
    method: 'put',
    data: params
  })
  return res.data
}

export async function deleteDashboard(dashboardId: string): Promise<void> {
  await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}`,
    method: 'delete'
  })
}

// ============ Panel API ============

export async function addPanel(
  dashboardId: string,
  params: {
    name?: string
    sql: string
    chart_type?: ChartType
    chart_config?: Record<string, unknown>
    layout_x?: number
    layout_y?: number
    layout_w?: number
    layout_h?: number
    refresh_interval?: number
    source_thread_id?: string
    datasource_id?: string
    member_datasource_id?: string
  }
): Promise<Panel> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/panels`,
    method: 'post',
    data: params
  })
  return res.data
}

export async function generatePanelName(params: {
  sql: string
  chart_type?: ChartType
  name?: string
}): Promise<{ name: string }> {
  const res = await request({
    url: `/agents/api/chatdata/panel-name-generate`,
    method: 'post',
    data: params,
    timeout: 120000, // LLM 生成面板名，超过全局 30s
  })
  return res.data
}

export async function updatePanel(
  dashboardId: string,
  panelId: string,
  params: {
    name?: string
    sql?: string
    chart_type?: ChartType
    chart_config?: Record<string, unknown>
    layout_x?: number
    layout_y?: number
    layout_w?: number
    layout_h?: number
    refresh_interval?: number
    member_datasource_id?: string
  }
): Promise<Panel> {
  const res = await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/panels/${panelId}`,
    method: 'put',
    data: params
  })
  return res.data
}

export async function deletePanel(dashboardId: string, panelId: string): Promise<void> {
  await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/panels/${panelId}`,
    method: 'delete'
  })
}

export async function updateLayout(
  dashboardId: string,
  layouts: Array<{ panel_id: string; x: number; y: number; w: number; h: number }>
): Promise<void> {
  await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/layout`,
    method: 'put',
    data: { layouts }
  })
}

export async function executePanel(
  dashboardId: string,
  panelId: string,
  memberDatasourceId?: string,
  signal?: AbortSignal
): Promise<PanelData> {
  const createResp = await request({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/query-tasks`,
    method: 'post',
    data: {
      panel_ids: [panelId],
      panel_member_datasource_ids: memberDatasourceId ? { [panelId]: memberDatasourceId } : {}
    }
  })
  const taskId = createResp.data?.task_id as string
  const pollIntervalMs = Number(createResp.data?.poll_interval_ms || 1500)
  const task = await waitForDashboardTask<DashboardBatchTaskResult>(taskId, pollIntervalMs, undefined, signal)
  const entry = task.result?.results?.[panelId]
  if (!entry) {
    throw new Error('面板任务无返回结果')
  }
  if (entry.status === 'succeeded' && entry.payload) {
    return entry.payload
  }
  throw new Error(entry.error || task.error_message || '面板任务执行失败')
}

/**
 * 轮询任务状态
 * @param signal 取消信号:组件卸载等场景中止轮询,抛出 AbortError 由调用方静默处理
 */
async function waitForDashboardTask<T = unknown>(
  taskId: string,
  pollIntervalMs = 1500,
  onProgress?: (task: QueryTaskStatusResult<T>) => void,
  signal?: AbortSignal
): Promise<QueryTaskStatusResult<T>> {
  const startedAt = Date.now()
  while (true) {
    // 每轮发起查询前先响应取消,避免组件卸载后仍发起下一次状态查询
    if (signal?.aborted) {
      throw createAbortError()
    }
    const resp = await request({
      url: `/agents/api/chatdata/query-tasks/${taskId}`,
      method: 'get',
    })
    const task = resp.data as QueryTaskStatusResult<T>
    onProgress?.(task)
    if (task.status === 'succeeded' || task.status === 'partial_success') {
      return task
    }
    if (task.status === 'failed' || task.status === 'cancelled') {
      throw new Error(task.error_message || '任务执行失败')
    }
    if (Date.now() - startedAt > 15 * 60 * 1000) {
      throw new Error('任务执行超时，请稍后重试')
    }
    // sleep 可被取消信号打断,卸载后不再空等一个完整轮询间隔
    await abortableSleep(Math.max(500, pollIntervalMs), signal)
  }
}

/**
 * 批量执行面板（异步任务轮询）
 */
export async function executePanelsBatch(
  dashboardId: string,
  params: {
    panel_ids?: string[]
    panel_member_datasource_ids?: Record<string, string>
  } | undefined,
  onPanelData: (panelId: string, data: PanelData | { error: string }) => void,
  signal?: AbortSignal
): Promise<DashboardBatchTaskResult> {
  const response = await requestStream({
    url: `/agents/api/chatdata/dashboards/${dashboardId}/panels/execute-batch`,
    method: 'POST',
    data: params || {},
    signal,
  })
  const contentType = response.headers.get('content-type') || ''

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}`
    try {
      const errorData = await response.json()
      errorMessage = errorData?.msg || errorData?.message || errorMessage
    } catch {
      try {
        errorMessage = await response.text() || errorMessage
      } catch {
        // ignore
      }
    }
    throw new Error(errorMessage)
  }

  if (!contentType.includes('text/event-stream')) {
    let payload: any = null
    try {
      payload = await response.json()
    } catch {
      throw new Error('看板批量加载响应格式异常')
    }

    if (payload?.status === 'success') {
      const resultData = payload?.data || {}
      return {
        dashboard_id: dashboardId,
        total: Number(resultData?.total || 0),
        completed: Number(resultData?.completed || 0),
        succeeded: Number(resultData?.succeeded || 0),
        failed: Number(resultData?.failed || 0),
        results: resultData?.results || {},
      }
    }

    throw new Error(payload?.msg || payload?.message || '看板批量加载失败')
  }

  if (!response.body) {
    throw new Error('响应流为空')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const summary: DashboardBatchTaskResult = {
    dashboard_id: dashboardId,
    total: params?.panel_ids?.length || 0,
    completed: 0,
    succeeded: 0,
    failed: 0,
    results: {},
  }
  const emitted = new Set<string>()
  let buffer = ''
  let streamCompleted = false

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    // 分帧与字段解析复用共享 parseSseChunk;多行 data 由旧实现的"无分隔符累加"
    // 改为规范的 "\n" 连接,不再黏行(本接口 data 均为单行 JSON,正常链路无影响)。
    const { events, rest } = parseSseChunk(buffer)
    buffer = rest

    for (const { data: dataStr } of events) {
      if (!dataStr) continue
      if (dataStr === '[DONE]') {
        streamCompleted = true
        return summary
      }

      let payload: any
      try {
        payload = JSON.parse(dataStr)
      } catch {
        continue
      }

      const panelId = String(payload?.panel_id || '').trim()
      if (!panelId || emitted.has(panelId)) {
        continue
      }
      emitted.add(panelId)

      if (payload?.error) {
        summary.results[panelId] = {
          status: 'failed',
          error: String(payload.error || '执行失败'),
        }
        summary.failed += 1
        summary.completed += 1
        onPanelData(panelId, { error: String(payload.error || '执行失败') })
        continue
      }

      summary.results[panelId] = {
        status: 'succeeded',
        payload: payload as PanelData,
      }
      summary.succeeded += 1
      summary.completed += 1
      onPanelData(panelId, payload as PanelData)
    }
  }

  if (!streamCompleted) {
    throw new Error('看板流式加载中断')
  }

  return {
    ...summary,
    total: Math.max(summary.total, summary.completed),
  }
}
