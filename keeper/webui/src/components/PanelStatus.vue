<script setup lang="ts">
import type { Status } from '../api'

const props = defineProps<{ status: Status | null; uptime: string }>()
const emit = defineEmits<{
  (e: 'openModels'): void
  (e: 'connect'): void
  (e: 'disconnect'): void
}>()

function currentModel(): string {
  return props.status?.active_model_label || '未配置'
}

function gamePill(): { cls: string; text: string } {
  if (!props.status) return { cls: 'muted', text: '未知' }
  if (!props.status.mod_up) return { cls: 'bad', text: '离线' }
  if (!props.status.connected) return { cls: 'warn', text: '在线·未连接' }
  return { cls: 'ok', text: '在线·已连接' }
}
</script>

<template>
  <div class="panel">
    <h3>面板状态</h3>
    <div class="row">
      <span class="label">当前模型</span>
      <span class="mono">{{ currentModel() }}</span>
      <button @click="emit('openModels')">管理模型</button>
    </div>
    <div class="row">
      <span class="label">游戏状态</span>
      <span class="pill" :class="gamePill().cls">{{ gamePill().text }}</span>
      <button :disabled="!status?.mod_up || status?.connected" @click="emit('connect')">连接游戏</button>
      <button :disabled="!status?.connected" @click="emit('disconnect')">断开连接</button>
    </div>
    <div class="row">
      <span class="label">运行时间</span>
      <span class="mono">{{ uptime }}</span>
      <span v-if="status" class="muted sub">决策 {{ status.decisions_count }} · 日志 {{ status.log_count }}</span>
    </div>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; gap: 10px; }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.label { color: var(--muted); min-width: 64px; }
select { background: rgba(255, 255, 255, 0.09); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
.pill { padding: 2px 10px; border-radius: 10px; font-size: 12px; }
.pill.ok { background: rgba(74, 222, 128, 0.16); color: #6ee7a0; border: 1px solid rgba(74, 222, 128, 0.35); }
.pill.warn { background: rgba(251, 191, 36, 0.16); color: #fcd34d; border: 1px solid rgba(251, 191, 36, 0.35); }
.pill.bad { background: rgba(248, 113, 113, 0.16); color: #fca5a5; border: 1px solid rgba(248, 113, 113, 0.35); }
.pill.muted { background: rgba(255, 255, 255, 0.1); color: var(--muted); border: 1px solid rgba(255, 255, 255, 0.14); }
.sub { font-size: 11px; }
</style>
