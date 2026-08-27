// 与 daemon 管理接口交互（REST + WebSocket）

export interface WorkflowStep {
  name: string
  type: string
  status: 'done' | 'running' | 'pending'
  detail: string
}

export interface WorkflowState {
  kind: 'plan' | 'outline' | null
  title: string
  idx: number
  total: number
  steps: WorkflowStep[]
}

export interface Status {
  mod_host: string
  mod_port: number
  mod_up: boolean
  connected: boolean
  protocol: number | null
  llm_ready: boolean
  llm: string
  active_model_label: string | null
  agent_running: boolean
  agent_paused: boolean
  goal: string
  config_error: string | null
  wf: WorkflowState
  decisions_count: number
  events_count: number
  launch_cmd: string
  token_configured: boolean
  player_name: string
  log_count: number
  daemon_started: number
}

export interface LogEntry {
  ts: number
  origin: 'player' | 'ai' | 'system'
  kind: string
  text: string
}

export interface Settings {
  log_enabled: boolean
  log_dir: string
}

export interface ModelEntry {
  id: string
  label: string
  provider: string
  model: string
  api_key: string
  base_url: string
  temperature: number | null
  top_p: number | null
  enable_thinking: boolean
  reasoning_effort: string | null
  max_tokens: number
}

export interface ModelsResult {
  models: ModelEntry[]
  active_model_id: string
  vision_model_id: string
}

export interface LlmModelInfo {
  id: string
  display_name: string | null
  context_length: number | null
}

export interface Decision {
  ts: number
  observation: string
  llm_output: string
  think: string
  action: string | null
  params: Record<string, unknown>
  result: unknown
  error: string | null
}

export interface GameEvent {
  name: string
  data: Record<string, unknown>
  ts: number
}

export interface WsMsg {
  type: 'status' | 'decision' | 'event'
  data: unknown
}

const BASE = ''

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  }
  if (!(init?.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(BASE + path, { ...init, headers })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch { /* ignore */ }
    throw new Error(`${path}: ${detail}`)
  }
  return res.json() as Promise<T>
}

export function getStatus(): Promise<Status> {
  return api<Status>('/api/status')
}

export function getState(): Promise<Record<string, unknown>> {
  return api<Record<string, unknown>>('/api/state')
}

export function getDecisions(limit = 100): Promise<Decision[]> {
  return api<Decision[]>(`/api/decisions?limit=${limit}`)
}

export function getLog(limit = 200): Promise<LogEntry[]> {
  return api<LogEntry[]>(`/api/log?limit=${limit}`)
}

export function disconnect(): Promise<Status> {
  return postJSON('/api/disconnect', {})
}

export function getEvents(limit = 100): Promise<GameEvent[]> {
  return api<GameEvent[]>(`/api/events?limit=${limit}`)
}

export function postJSON(path: string, body: unknown): Promise<Status> {
  return api<Status>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
}

export function sendChat(message: string): Promise<{ sent: boolean }> {
  return api<{ sent: boolean }>('/api/chat', { method: 'POST', body: JSON.stringify({ message }) })
}

export function getSettings(): Promise<Settings> {
  return api<Settings>('/api/settings')
}

export function saveSettings(s: Partial<Settings>): Promise<Settings> {
  return api<Settings>('/api/settings', { method: 'POST', body: JSON.stringify(s) })
}

export function getModels(): Promise<ModelsResult> {
  return api<ModelsResult>('/api/models')
}

export function createModel(body: Partial<ModelEntry>): Promise<ModelsResult> {
  return api<ModelsResult>('/api/models', { method: 'POST', body: JSON.stringify(body) })
}

export function updateModel(id: string, body: Partial<ModelEntry>): Promise<ModelsResult> {
  return api<ModelsResult>(`/api/models/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) })
}

export function deleteModel(id: string): Promise<ModelsResult> {
  return api<ModelsResult>(`/api/models/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function activateModel(id: string): Promise<ModelsResult> {
  return api<ModelsResult>(`/api/models/${encodeURIComponent(id)}/activate`, { method: 'POST', body: JSON.stringify({}) })
}

export function activateVisionModel(id: string): Promise<ModelsResult> {
  return api<ModelsResult>(`/api/models/${encodeURIComponent(id)}/activate-vision`, { method: 'POST', body: JSON.stringify({}) })
}

export function clearVisionModel(): Promise<ModelsResult> {
  return api<ModelsResult>('/api/models/vision/clear', { method: 'POST', body: JSON.stringify({}) })
}

export function fetchModels(id: string): Promise<{ models: LlmModelInfo[] }> {
  return api(`/api/models/${encodeURIComponent(id)}/fetch-models`, { method: 'POST', body: JSON.stringify({}) })
}

export function testLLM(): Promise<{ ok: boolean; reply?: string; error?: string }> {
  return api('/api/llm/test', { method: 'POST', body: JSON.stringify({}) })
}

export function exportLogs(): Promise<{ path: string; count: number }> {
  return api('/api/logs/export', { method: 'POST', body: JSON.stringify({}) })
}

export interface WallpaperInfo {
  url: string | null
  ts?: number
}

export function getWallpaper(): Promise<WallpaperInfo> {
  return api<WallpaperInfo>('/api/wallpaper')
}

export function uploadWallpaper(file: File): Promise<WallpaperInfo> {
  const fd = new FormData()
  fd.append('file', file)
  return api<WallpaperInfo>('/api/wallpaper', { method: 'POST', body: fd })
}

export function removeWallpaper(): Promise<WallpaperInfo> {
  return api<WallpaperInfo>('/api/wallpaper', { method: 'DELETE' })
}

export function connectWS(onMessage: (msg: WsMsg) => void, onOpen?: () => void, onClose?: () => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws`)
  ws.onmessage = (ev) => {
    try { onMessage(JSON.parse(ev.data) as WsMsg) } catch { /* ignore */ }
  }
  if (onOpen) ws.onopen = onOpen
  if (onClose) ws.onclose = onClose
  return ws
}
