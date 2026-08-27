<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getState } from '../api'
import type { Status } from '../api'

const props = defineProps<{ status: Status | null }>()

const state = ref<Record<string, any> | null>(null)
const error = ref('')
let timer: number | undefined

function normYaw(yaw: number): number {
  return ((yaw % 360) + 540) % 360 - 180
}
function dirName(yaw: number): string {
  const d = Math.round(((yaw % 360) + 360) % 360)
  if (d >= 45 && d < 135) return '西'
  if (d >= 135 && d < 225) return '北'
  if (d >= 225 && d < 315) return '东'
  return '南'
}
function pct(v: unknown, max = 20) {
  const n = Number(v ?? 0)
  return Math.max(0, Math.min(100, (n / max) * 100))
}
function fmt(v: unknown, d = '?') {
  return v === undefined || v === null ? d : String(v)
}
// 数值取两位小数（坐标/朝向/生命值，防止显示过长）
function round2(v: unknown, d = '-'): string {
  if (v === undefined || v === null || v === '') return d
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n.toFixed(2)
}
// 维度 → 中文名（minecraft:overworld → 主世界，etc.）
const DIM_LABELS: Record<string, string> = {
  overworld: '主世界',
  the_nether: '下界',
  the_end: '末地',
}
function dimLabel(v: unknown): string {
  const raw = fmt(v, '未知')
  const id = String(raw).split(':').pop() || ''
  return DIM_LABELS[id] ?? raw
}
const invSlots = computed(() => {
  const inv = (state.value?.player?.inventory || {}) as Record<string, any>
  return (inv.slots || []).filter((s: any) => s && s.id)
})
// 物品 id → 图标 URL（minecraft:iron_ingot → /mc-icons/item/iron_ingot.png）
function itemIcon(id: unknown): string {
  const raw = String(id ?? '')
  const short = raw.split(':').pop() || ''
  return short ? `/mc-icons/item/${short}.png` : ''
}
// 方块图标在 block/ 目录（oak_log 等）；item 加载失败时回退
function blockIcon(id: unknown): string {
  const raw = String(id ?? '')
  const short = raw.split(':').pop() || ''
  return short ? `/mc-icons/block/${short}.png` : ''
}
function itemName(id: unknown): string {
  const raw = String(id ?? '')
  const short = raw.split(':').pop() || raw
  return short.replace(/_/g, ' ')
}
// img @error：item 失败 → 尝试 block；再失败 → 隐藏
function onIconError(e: Event) {
  const img = e.target as HTMLImageElement
  if (!img.dataset.fallback) {
    img.dataset.fallback = '1'
    const short = decodeURIComponent(img.src.split('/').pop()?.split('.')[0] || '')
    if (short) img.src = `/mc-icons/block/${short}.png`
    else img.style.display = 'none'
  } else {
    img.style.display = 'none'
  }
}
// MC HUD 逻辑：先把数值向上取整（ceil），再每 2 点一格显示 满/半/空。
// 例：5.8 血 → ceil=6 → 3 个满心（与游戏内一致）；5 → 2 满 + 1 半。
function hudIcons(value: unknown, base: 'heart' | 'food'): string[] {
  const v = Math.ceil(Number(value ?? 0))
  const out: string[] = []
  for (let i = 0; i < 10; i++) {
    const remain = v - i * 2
    if (remain >= 2) out.push(`${base}.png`)
    else if (remain >= 1) out.push(`${base}_half.png`)
    else out.push('')
  }
  return out
}
async function poll() {
  if (!props.status?.connected) {
    error.value = '未连接到 mod（请先连接游戏）'
    return
  }
  try {
    state.value = await getState()
    error.value = ''
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  }
}

onMounted(() => {
  poll()
  timer = window.setInterval(poll, 1000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="panel">
    <h3>玩家状态</h3>
    <div v-if="error" class="error">⚠ {{ error }}</div>
    <template v-else-if="state">
      <div class="list">
        <div class="item">
          <span class="label">玩家名</span>
          <b>{{ status?.player_name || '未知' }}</b>
        </div>
        <div class="item">
          <span class="label">所处世界</span>
          <b class="mono">{{ dimLabel(state.player?.dimension) }}</b>
        </div>
        <div class="item">
          <span class="label">坐标</span>
          <b class="mono">x {{ round2(state.player?.position?.x) }} / y {{ round2(state.player?.position?.y) }} / z {{ round2(state.player?.position?.z) }}</b>
        </div>
        <div class="item">
          <span class="label">视角朝向</span>
          <b class="mono">yaw {{ round2(normYaw(Number(state.player?.rotation?.yaw) || 0)) }}° {{ dirName(Number(state.player?.rotation?.yaw) || 0) }} · pitch {{ round2(state.player?.rotation?.pitch) }}°</b>
          <span class="compass"><i>N</i><i>E</i><i>S</i><i>W</i><span class="needle" :style="{ transform: `rotate(${180 - normYaw(Number(state.player?.rotation?.yaw) || 0)}deg)` }"></span></span>
        </div>
        <div class="item bar">
          <span class="label">生命值</span>
          <div class="hud-row">
            <template v-for="(ic, i) in hudIcons(state.player?.health, 'heart')" :key="i">
              <img v-if="ic" :src="`/mc-icons/${ic}`" alt="" />
            </template>
          </div>
          <b class="mono">{{ round2(state.player?.health) }}/20</b>
        </div>
        <div class="item bar">
          <span class="label">饥饿值</span>
          <div class="hud-row">
            <template v-for="(ic, i) in hudIcons(state.player?.food, 'food')" :key="i">
              <img v-if="ic" :src="`/mc-icons/${ic}`" alt="" />
            </template>
          </div>
          <b class="mono">{{ round2(state.player?.food) }}/20</b>
        </div>
        <div class="item">
          <span class="label">物品栏</span>
          <div class="inv-grid" v-if="invSlots.length">
            <div v-for="(s, i) in invSlots" :key="i" class="inv-slot" :title="itemName(s.id) + ' ×' + s.count">
              <img :src="itemIcon(s.id)" loading="lazy" alt="" @error="onIconError" />
              <span class="inv-count">{{ s.count }}</span>
            </div>
          </div>
          <span class="pending" v-else>空</span>
        </div>
        <div class="item">
          <span class="label">背包</span>
          <span class="mono">{{ invSlots.length }} 种物品</span>
        </div>
      </div>
    </template>
    <div v-else class="muted">加载中…</div>
  </div>
</template>

<style scoped>
.list { display: flex; flex-direction: column; gap: 8px; }
.item { display: flex; align-items: center; gap: 8px; font-size: 13px; flex-wrap: wrap; }
.item .label { color: var(--muted); min-width: 64px; }
.item b { font-size: 13px; }
.item.bar { gap: 8px; }
.hud-row { display: flex; gap: 2px; flex: 1; min-width: 0; align-items: center; }
.hud-row img {
  width: 18px;
  height: 18px;
  image-rendering: pixelated;
  image-rendering: crisp-edges;
}
.bar { flex: 1; height: 10px; background: rgba(255, 255, 255, 0.1); border-radius: 5px; overflow: hidden; min-width: 80px; }
.fill { height: 100%; transition: width 0.4s; }
.fill.health { background: linear-gradient(90deg, #b53838, #e05d5d); }
.fill.food { background: linear-gradient(90deg, #7a5a1f, #d3a44a); }
.pending { color: var(--muted); font-size: 12px; }
.inv-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.inv-slot {
  position: relative;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-soft);
  border-radius: 6px;
}
.inv-slot img {
  width: 28px;
  height: 28px;
  image-rendering: pixelated;
  image-rendering: crisp-edges;
}
.inv-count {
  position: absolute;
  right: 2px;
  bottom: 0;
  font-size: 10px;
  color: var(--text);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.9);
}
.compass { position: relative; width: 34px; height: 34px; border: 1px solid var(--border); border-radius: 50%; display: inline-block; flex-shrink: 0; }
.compass i { position: absolute; font-style: normal; font-size: 8px; color: var(--muted); }
.compass i:nth-child(1) { top: 0; left: 50%; transform: translateX(-50%); color: var(--err); }
.compass i:nth-child(2) { right: 0; top: 50%; transform: translateY(-50%); }
.compass i:nth-child(3) { bottom: 0; left: 50%; transform: translateX(-50%); }
.compass i:nth-child(4) { left: 0; top: 50%; transform: translateY(-50%); }
.needle { position: absolute; left: 50%; top: 50%; width: 2px; height: 13px; background: var(--accent); transform-origin: 50% 100%; margin-left: -1px; margin-top: -13px; }
.error { color: var(--err); }
.muted { color: var(--muted); }
</style>
