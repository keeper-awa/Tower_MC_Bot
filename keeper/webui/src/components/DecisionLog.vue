<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { Decision } from '../api'

const props = defineProps<{ decisions: Decision[] }>()
const emit = defineEmits<{ (e: 'clear'): void }>()

const expanded = ref<Set<number>>(new Set())
const follow = ref(true)
const listEl = ref<HTMLElement | null>(null)

watch(() => props.decisions.length, async () => {
  if (follow.value) {
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  }
})

function toggle(i: number) {
  const s = new Set(expanded.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expanded.value = s
}

function fmt(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

function actionClass(a: string | null): string {
  if (!a) return 'none'
  const move = ['move', 'jump', 'jump_once', 'look_at', 'sneak', 'sprint', 'swim', 'fly', 'fall_fly']
  if (move.includes(a)) return 'move'
  if (a === 'attack') return 'attack'
  const interact = ['use_item', 'interact_block', 'interact_entity']
  if (interact.includes(a)) return 'interact'
  const inv = ['drop', 'hotbar', 'equip', 'move_item', 'craft']
  if (inv.includes(a)) return 'inv'
  if (a === 'chat') return 'chat'
  return 'info'
}
</script>

<template>
  <div class="panel log-panel">
    <div class="head">
      <h3>AI 决策日志 <span class="muted">({{ decisions.length }})</span></h3>
      <div class="tools">
        <label class="follow"><input type="checkbox" v-model="follow" /> 跟随</label>
        <button :disabled="!decisions.length" @click="emit('clear')">清空</button>
      </div>
    </div>
    <div ref="listEl" class="log">
      <div v-if="!decisions.length" class="muted">暂无决策（启动 agent 后实时显示）</div>
      <div v-for="(d, i) in decisions.slice().reverse()" :key="i" class="entry" :class="{ error: d.error, expanded: expanded.has(i) }" @click="toggle(i)">
        <div class="head-row">
          <span class="mono t">{{ fmt(d.ts) }}</span>
          <span class="lat mono" :title="`LLM 耗时 ${d.latency ?? '?'}s`">{{ d.latency != null ? `${d.latency}s` : '' }}</span>
          <span class="think">{{ d.think || '—' }}</span>
          <span v-if="d.action" class="act mono" :class="actionClass(d.action)">{{ d.action }} {{ JSON.stringify(d.params) }}</span>
          <span v-else class="none">无动作</span>
          <span v-if="d.error" class="err-flag">✗</span>
        </div>
        <div v-if="expanded.has(i)" class="detail mono">
          <div v-if="d.error" class="err">✗ {{ d.error }}</div>
          <div v-if="d.result !== null && d.result !== undefined">← {{ JSON.stringify(d.result) }}</div>
          <div class="muted">LLM: {{ d.llm_output || '(空)' }}</div>
          <div class="muted">观测: {{ d.observation }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.log-panel { display: flex; flex-direction: column; height: 100%; }
.head { display: flex; align-items: center; justify-content: space-between; }
.tools { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.follow { display: flex; align-items: center; gap: 4px; color: var(--muted); }
.log { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.entry { background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border-soft); border-radius: 6px; padding: 6px 8px; cursor: pointer; }
.entry.error { border-color: var(--err); }
.entry.expanded { border-color: var(--accent); }
.head-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.t { color: var(--muted); font-size: 12px; }
.lat { color: var(--muted); font-size: 11px; }
.think { color: var(--text); }
.act { font-size: 12px; }
.act.move { color: #5aa7ff; }
.act.attack { color: #ff7a7a; }
.act.interact { color: #ffb25a; }
.act.inv { color: #c98aff; }
.act.chat { color: #6fd98a; }
.act.info { color: #9aa7b5; }
.none { color: var(--muted); font-size: 12px; }
.err-flag { color: var(--err); font-weight: bold; }
.detail { margin-top: 6px; font-size: 12px; border-top: 1px dashed var(--border); padding-top: 6px; }
.err { color: var(--err); }
.muted { color: var(--muted); }
</style>
