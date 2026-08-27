<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { LogEntry } from '../api'

const props = defineProps<{ log: LogEntry[] }>()
const emit = defineEmits<{ (e: 'clear'): void }>()

const follow = ref(true)
const listEl = ref<HTMLElement | null>(null)

watch(() => props.log.length, async () => {
  if (follow.value) {
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  }
})

function fmt(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}
function originLabel(o: string) {
  return o === 'player' ? '玩家' : o === 'ai' ? 'AI' : '系统'
}
function originCls(o: string) {
  return o === 'player' ? 'player' : o === 'ai' ? 'ai' : 'system'
}
</script>

<template>
  <div class="panel log-panel">
    <div class="head">
      <h3>日志（由谁发起的命令） <span class="muted">({{ log.length }})</span></h3>
      <div class="tools">
        <label class="follow"><input type="checkbox" v-model="follow" /> 跟随</label>
        <button :disabled="!log.length" @click="emit('clear')">清空</button>
      </div>
    </div>
    <div ref="listEl" class="log">
      <div v-if="!log.length" class="muted">暂无记录</div>
      <div v-for="(e, i) in log.slice()" :key="i" class="row">
        <span class="mono t">{{ fmt(e.ts) }}</span>
        <span class="origin" :class="originCls(e.origin)">{{ originLabel(e.origin) }}</span>
        <span class="text">{{ e.text }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.log-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.head { display: flex; align-items: center; justify-content: space-between; }
.tools { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.follow { display: flex; align-items: center; gap: 4px; color: var(--muted); }
.log { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.row { display: flex; gap: 8px; font-size: 12px; padding: 4px 6px; border-radius: 6px; background: rgba(255, 255, 255, 0.05); align-items: baseline; }
.t { color: var(--muted); flex-shrink: 0; }
.origin { flex-shrink: 0; padding: 0 6px; border-radius: 8px; font-size: 11px; }
.origin.player { background: rgba(74, 222, 128, 0.16); color: #6ee7a0; }
.origin.ai { background: rgba(95, 183, 255, 0.16); color: #7dd0ff; }
.origin.system { background: rgba(255, 255, 255, 0.1); color: var(--muted); }
.text { color: var(--text); word-break: break-all; }
.muted { color: var(--muted); }
</style>
