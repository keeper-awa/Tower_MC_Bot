<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import PanelStatus from './components/PanelStatus.vue'
import StatusPanel from './components/StatusPanel.vue'
import WorkflowPanel from './components/WorkflowPanel.vue'
import CommandLog from './components/CommandLog.vue'
import GoalPanel from './components/GoalPanel.vue'
import SettingsModal from './components/SettingsModal.vue'
import ModelModal from './components/ModelModal.vue'
import { getStatus, getLog, connectWS, postJSON, disconnect, getWallpaper } from './api'
import type { Status, LogEntry, WsMsg } from './api'

const status = ref<Status | null>(null)
const log = ref<LogEntry[]>([])
const connected = ref(false)
const showSettings = ref(false)
const showModels = ref(false)

let ws: WebSocket | null = null
let timer: number | undefined
let secTimer: number | undefined
const now = ref(Date.now())

function uptimeText(): string {
  const startMs = (status.value?.daemon_started || now.value / 1000) * 1000
  const s = Math.floor(Math.max(0, (now.value - startMs) / 1000))
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`
}

async function refresh() {
  try { status.value = await getStatus() } catch { /* ignore */ }
  try { log.value = await getLog(300) } catch { /* ignore */ }
}

async function applyWallpaper() {
  try {
    const w = await getWallpaper()
    const body = document.body
    if (w.url) {
      body.style.backgroundImage = `linear-gradient(180deg, rgba(9,12,19,0.55), rgba(9,12,19,0.68)), url('${w.url}?t=${w.ts || Date.now()}')`
      body.style.backgroundSize = 'cover'
      body.style.backgroundPosition = 'center'
      body.style.backgroundAttachment = 'fixed'
    } else {
      body.style.backgroundImage = ''
      body.style.backgroundSize = ''
      body.style.backgroundPosition = ''
      body.style.backgroundAttachment = ''
    }
  } catch { /* ignore */ }
}

function onWs(msg: WsMsg) {
  if (msg.type === 'status') status.value = msg.data as Status
  else if (msg.type === 'log') {
    log.value.push(msg.data as LogEntry)
    if (log.value.length > 400) log.value.shift()
  }
}

async function act(path: string, body?: unknown) {
  try { status.value = await postJSON(path, body ?? {}) } catch (e) { alert(String(e)) }
}

async function doDisconnect() {
  try { status.value = await disconnect() } catch (e) { alert(String(e)) }
}

onMounted(() => {
  refresh()
  applyWallpaper()
  timer = window.setInterval(refresh, 3000)
  secTimer = window.setInterval(() => { now.value = Date.now() }, 1000)
  ws = connectWS(onWs, () => { connected.value = true; refresh() }, () => { connected.value = false })
  // 首次启动引导：LLM 未配置或 token 缺失时自动打开设置
  window.setTimeout(() => {
    if (status.value && (!status.value.llm_ready || !status.value.token_configured)) {
      showSettings.value = true
    }
  }, 600)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (secTimer) clearInterval(secTimer)
  ws?.close()
})
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <span class="logo">◈</span>
        <b>Tower 控制台</b>
      </div>
      <div class="top-right">
        <button @click="showSettings = true">⚙ 设置</button>
      </div>
    </header>
    <!-- 配置缺失引导（clone 后首次使用：game_dir 未填 / tower.json 不存在） -->
    <div v-if="status?.config_error" class="config-banner">
      <span class="cfg-warn">⚠ 配置未完成</span>
      <span class="cfg-text">{{ status.config_error }}</span>
    </div>
    <main class="grid">
      <section class="col-left">
        <PanelStatus
          :status="status"
          :uptime="uptimeText()"
          @open-models="showModels = true"
          @connect="act('/api/connect', { launch: false, timeout: 15 })"
          @disconnect="doDisconnect"
        />
        <StatusPanel :status="status" />
      </section>
      <section class="col-right">
        <WorkflowPanel :status="status" />
        <div class="log-wrap"><CommandLog :log="log" @clear="log = []" /></div>
        <GoalPanel :status="status" @changed="refresh" />
      </section>
    </main>

    <SettingsModal v-if="showSettings" @close="showSettings = false" @changed="applyWallpaper" />
    <ModelModal v-if="showModels" @close="showModels = false" @changed="refresh" />
  </div>
</template>

<style scoped>
.app { height: 100%; display: flex; flex-direction: column; }
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.12), rgba(255, 255, 255, 0.05));
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
}
.brand { display: flex; align-items: center; gap: 10px; font-size: 16px; letter-spacing: 0.5px; }
.logo {
  color: var(--accent2);
  text-shadow: 0 0 12px rgba(63, 208, 255, 0.6);
  font-size: 18px;
}
.top-right { display: flex; align-items: center; gap: 12px; }
/* 配置缺失引导横幅（clone 后首次使用） */
.config-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 20px;
  background: linear-gradient(180deg, rgba(248, 113, 113, 0.16), rgba(248, 113, 113, 0.08));
  border-bottom: 1px solid rgba(248, 113, 113, 0.3);
  font-size: 13px;
  color: #fca5a5;
}
.config-banner .cfg-warn {
  font-weight: 600;
  color: #f87171;
  flex-shrink: 0;
}
.config-banner .cfg-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sub { color: var(--muted); font-size: 12px; }
.pill { padding: 3px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }
.pill.ok { background: rgba(74, 222, 128, 0.16); color: #6ee7a0; border: 1px solid rgba(74, 222, 128, 0.35); }
.pill.warn { background: rgba(251, 191, 36, 0.16); color: #fcd34d; border: 1px solid rgba(251, 191, 36, 0.35); }
.pill.bad { background: rgba(248, 113, 113, 0.16); color: #fca5a5; border: 1px solid rgba(248, 113, 113, 0.35); }
.pill.muted { background: rgba(255, 255, 255, 0.1); color: var(--muted); border: 1px solid rgba(255, 255, 255, 0.14); }
.grid { flex: 1; display: grid; grid-template-columns: 380px 1fr; gap: 14px; padding: 14px 20px; overflow: hidden; }
.col-left { display: flex; flex-direction: column; gap: 14px; overflow-y: auto; }
.col-right { display: flex; flex-direction: column; gap: 14px; overflow: hidden; }
.log-wrap { flex: 1; min-height: 0; }
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; overflow-y: auto; }
  .col-right { overflow: visible; }
}
</style>
