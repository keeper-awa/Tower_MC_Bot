<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getSettings, saveSettings, exportLogs, getWallpaper, uploadWallpaper, removeWallpaper } from '../api'
import type { Settings } from '../api'

const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const s = ref<Settings>({
  log_enabled: false,
  log_dir: 'logs',
})
const busy = ref(false)
const msg = ref<{ type: 'ok' | 'err'; text: string } | null>(null)

const fileInput = ref<HTMLInputElement | null>(null)
const wallpaperUrl = ref<string | null>(null)
const wallpaperTs = ref(0)

onMounted(async () => {
  try {
    s.value = await getSettings()
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  }
  try {
    const w = await getWallpaper()
    wallpaperUrl.value = w.url
    wallpaperTs.value = w.ts || 0
  } catch {
    /* ignore */
  }
})

async function save() {
  busy.value = true
  msg.value = null
  try {
    s.value = await saveSettings({ ...s.value })
    msg.value = { type: 'ok', text: '已保存并生效' }
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  } finally {
    busy.value = false
  }
}

async function doExport() {
  busy.value = true
  msg.value = null
  try {
    const r = await exportLogs()
    msg.value = { type: 'ok', text: `已导出 ${r.count} 条决策 → ${r.path}` }
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  } finally {
    busy.value = false
  }
}

async function onPickWallpaper(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  busy.value = true
  msg.value = null
  try {
    const w = await uploadWallpaper(f)
    wallpaperUrl.value = w.url
    wallpaperTs.value = w.ts || 0
    emit('changed')
    msg.value = { type: 'ok', text: '壁纸已更新' }
  } catch (err) {
    msg.value = { type: 'err', text: String(err) }
  } finally {
    busy.value = false
    ;(e.target as HTMLInputElement).value = ''
  }
}

async function doRemoveWallpaper() {
  busy.value = true
  msg.value = null
  try {
    const w = await removeWallpaper()
    wallpaperUrl.value = w.url
    wallpaperTs.value = w.ts || 0
    emit('changed')
    msg.value = { type: 'ok', text: '已移除壁纸' }
  } catch (err) {
    msg.value = { type: 'err', text: String(err) }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-head">
        <h3>设置</h3>
        <button @click="emit('close')">✕</button>
      </div>
      <div class="modal-body">
        <fieldset>
          <legend>日志</legend>
          <label class="check">
            <input v-model="s.log_enabled" type="checkbox" /> 自动保存（decision/event 写入 jsonl）
          </label>
          <label>保存目录
            <input v-model="s.log_dir" type="text" />
          </label>
          <div class="actions">
            <button :disabled="busy" @click="doExport">导出当前日志</button>
          </div>
        </fieldset>

        <fieldset>
          <legend>壁纸</legend>
          <div class="wp">
            <input
              ref="fileInput"
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              class="hidden"
              @change="onPickWallpaper"
            />
            <div class="actions">
              <button :disabled="busy" @click="fileInput?.click()">上传壁纸</button>
              <button v-if="wallpaperUrl" class="danger" :disabled="busy" @click="doRemoveWallpaper">移除壁纸</button>
            </div>
            <img
              v-if="wallpaperUrl"
              class="wp-preview"
              :src="`${wallpaperUrl}?t=${wallpaperTs || Date.now()}`"
              alt="壁纸预览"
            />
            <div class="hint">上传图片作为控制台背景（jpg/png/webp/gif）。面板已半透明，壁纸会透出；未上传时使用默认渐变背景。</div>
          </div>
        </fieldset>

        <div v-if="msg" class="msg" :class="msg.type">{{ msg.text }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.modal {
  width: 460px;
  max-width: 92vw;
  max-height: 86vh;
  overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  backdrop-filter: blur(14px);
}
.modal-head { display: flex; align-items: center; justify-content: space-between; }
.modal-head h3 { margin: 0; }
.modal-body { display: flex; flex-direction: column; gap: 12px; margin-top: 10px; }
fieldset { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
legend { color: var(--accent); padding: 0 6px; font-size: 13px; }
label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); }
.two { display: flex; gap: 8px; }
.two label { flex: 1; }
.check { flex-direction: row; align-items: center; gap: 6px; }
.actions { display: flex; gap: 8px; margin-top: 2px; }
.info { font-size: 13px; }
.info b { color: var(--text); }
.hint { font-size: 11px; color: var(--muted); line-height: 1.5; }
.msg { padding: 6px 10px; border-radius: 6px; font-size: 12px; }
.msg.ok { background: rgba(74, 222, 128, 0.16); color: #6ee7a0; }
.msg.err { background: rgba(248, 113, 113, 0.16); color: #fca5a5; }
.hidden { display: none; }
.wp { display: flex; flex-direction: column; gap: 8px; }
.wp-preview {
  max-width: 100%;
  max-height: 140px;
  border-radius: 8px;
  border: 1px solid var(--border);
  object-fit: cover;
}
</style>
