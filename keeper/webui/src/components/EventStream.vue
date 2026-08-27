<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import type { GameEvent } from '../api'

const props = defineProps<{ events: GameEvent[] }>()
const emit = defineEmits<{ (e: 'clear'): void }>()

const filter = ref('all')
const hidePos = ref(true)
const listEl = ref<HTMLElement | null>(null)

const TYPES = ['all', 'pos', 'chat', 'damage', 'death', 'respawn', 'game_mode', 'mine', 'mine_done']

function samePos(a: GameEvent, b: GameEvent): boolean {
  const pa = a.data.position as any
  const pb = b.data.position as any
  return pa && pb && pa.x === pb.x && pa.y === pb.y && pa.z === pb.z
}

function dedupe(list: GameEvent[]): GameEvent[] {
  const out: GameEvent[] = []
  for (const e of list) {
    const last = out[out.length - 1]
    if (e.name === 'pos' && last && last.name === 'pos' && samePos(last, e)) continue
    out.push(e)
  }
  return out
}

const filtered = computed(() => {
  let list = props.events
  if (filter.value === 'all') {
    if (hidePos.value) list = list.filter((e) => e.name !== 'pos')
  } else {
    list = list.filter((e) => e.name === filter.value)
  }
  return dedupe(list)
})

watch(() => props.events.length, async () => {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
})

function cls(name: string) {
  if (name === 'damage' || name === 'death') return 'bad'
  if (name === 'chat') return 'chat'
  if (name === 'mine' || name === 'mine_done') return 'mine'
  if (name === 'pos') return 'pos'
  return ''
}

function fmt(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

function payload(e: GameEvent) {
  if (e.name === 'chat') return `[${e.data.sender}] ${e.data.message}`
  if (e.name === 'pos') {
    const p = e.data.position as any
    return p ? `x${Number(p.x).toFixed(1)} y${Number(p.y).toFixed(1)} z${Number(p.z).toFixed(1)}` : JSON.stringify(e.data)
  }
  return JSON.stringify(e.data)
}
</script>

<template>
  <div class="panel stream-panel">
    <div class="head">
      <h3>事件流 <span class="muted">({{ filtered.length }})</span></h3>
      <div class="tools">
        <label v-if="filter === 'all'" class="follow"><input type="checkbox" v-model="hidePos" /> 隐藏 pos</label>
        <button :disabled="!events.length" @click="emit('clear')">清空</button>
      </div>
    </div>
    <div class="chips">
      <button v-for="t in TYPES" :key="t" :class="{ active: filter === t }" @click="filter = t">{{ t }}</button>
    </div>
    <div ref="listEl" class="stream">
      <div v-if="!filtered.length" class="muted">暂无事件</div>
      <div v-for="(e, i) in filtered.slice().reverse()" :key="i" class="row" :class="cls(e.name)">
        <span class="mono t">{{ fmt(e.ts) }}</span>
        <span class="tag mono">{{ e.name }}</span>
        <span class="mono data">{{ payload(e) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stream-panel { display: flex; flex-direction: column; height: 100%; }
.head { display: flex; align-items: center; justify-content: space-between; }
.tools { display: flex; gap: 8px; }
.chips { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px; }
.chips button { padding: 2px 8px; font-size: 11px; border-radius: 10px; }
.chips button.active { background: rgba(121, 217, 255, 0.18); border-color: rgba(121, 217, 255, 0.5); color: #fff; }
.stream { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
.row { display: flex; gap: 8px; font-size: 12px; padding: 3px 6px; border-radius: 4px; background: rgba(255, 255, 255, 0.05); align-items: baseline; }
.row.bad { border-left: 3px solid var(--err); }
.row.chat { border-left: 3px solid var(--ok); }
.row.mine { border-left: 3px solid var(--warn); }
.row.pos { border-left: 3px solid #3d5d80; }
.t { color: var(--muted); }
.tag { color: var(--accent); }
.data { color: var(--text); word-break: break-all; }
.muted { color: var(--muted); }
</style>
