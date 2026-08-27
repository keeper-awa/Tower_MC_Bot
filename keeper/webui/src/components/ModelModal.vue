<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import {
  getModels,
  createModel,
  updateModel,
  deleteModel,
  activateModel,
  activateVisionModel,
  clearVisionModel,
  fetchModels,
} from '../api'
import type { ModelEntry, LlmModelInfo } from '../api'

const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const models = ref<ModelEntry[]>([])
const activeId = ref('')       // 对话模型
const visionId = ref('')       // 视觉模型（空 = 跟随对话模型）
const busy = ref(false)
const msg = ref<{ type: 'ok' | 'err'; text: string } | null>(null)

// 右侧面板：null=提示 / edit=编辑
const panelMode = ref<'edit' | null>(null)
const editing = reactive<ModelEntry>(emptyEntry())
const availableModels = ref<LlmModelInfo[]>([])
const loadingModels = ref(false)

function emptyEntry(): ModelEntry {
  return {
    id: '',
    label: '',
    provider: 'openai',
    model: '',
    api_key: '',
    base_url: '',
    temperature: 0.7,
    top_p: null,
    enable_thinking: false,
    reasoning_effort: null,
    max_tokens: 1024,
  }
}

async function load() {
  try {
    const r = await getModels()
    models.value = r.models
    activeId.value = r.active_model_id
    visionId.value = r.vision_model_id
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  }
}
onMounted(load)

// ------------------------------------------------------------ 面板切换
function startAdd() {
  Object.assign(editing, emptyEntry())
  availableModels.value = []
  panelMode.value = 'edit'
  msg.value = null
}

function startEdit(m: ModelEntry) {
  Object.assign(editing, { ...m })
  availableModels.value = []
  panelMode.value = 'edit'
  msg.value = null
}

function closePanel() {
  panelMode.value = null
  msg.value = null
}

// ------------------------------------------------------------ 操作
async function save() {
  if (!editing.model?.trim()) {
    msg.value = { type: 'err', text: '模型名称（model）不能为空' }
    return
  }
  busy.value = true
  msg.value = null
  try {
    const body: Partial<ModelEntry> = { ...editing }
    delete (body as Partial<ModelEntry> & { id?: string }).id
    const r = editing.id
      ? await updateModel(editing.id, body)
      : await createModel(body)
    models.value = r.models
    activeId.value = r.active_model_id
    visionId.value = r.vision_model_id
    const saved = r.models.find(
      (x) => x.id === editing.id || (x.label === editing.label && x.model === editing.model),
    )
    if (saved) Object.assign(editing, { ...saved })
    emit('changed')
    msg.value = { type: 'ok', text: editing.id ? '已保存修改' : '已创建模型' }
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  } finally {
    busy.value = false
  }
}

async function doDelete(m: ModelEntry) {
  const name = m.label || m.model
  if (!window.confirm(`删除模型「${name}」？`)) return
  busy.value = true
  msg.value = null
  try {
    const r = await deleteModel(m.id)
    models.value = r.models
    activeId.value = r.active_model_id
    visionId.value = r.vision_model_id
    if (panelMode.value !== null) closePanel()
    emit('changed')
    msg.value = { type: 'ok', text: `已删除「${name}」` }
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  } finally {
    busy.value = false
  }
}

async function doActivate(id: string) {
  busy.value = true
  msg.value = null
  try {
    const r = await activateModel(id)
    models.value = r.models
    activeId.value = r.active_model_id
    visionId.value = r.vision_model_id
    emit('changed')
    msg.value = { type: 'ok', text: '已设为对话模型' }
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  } finally {
    busy.value = false
  }
}

// 视觉角色：id 为空 → 跟随对话模型
async function doSetVision(id: string) {
  busy.value = true
  msg.value = null
  try {
    const r = id
      ? await activateVisionModel(id)
      : await clearVisionModel()
    models.value = r.models
    activeId.value = r.active_model_id
    visionId.value = r.vision_model_id
    emit('changed')
    msg.value = { type: 'ok', text: id ? '已设为视觉模型' : '视觉已跟随对话模型' }
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  } finally {
    busy.value = false
  }
}

async function doFetchModels() {
  if (!editing.id) {
    msg.value = { type: 'err', text: '请先保存模型，再拉取模型列表' }
    return
  }
  loadingModels.value = true
  msg.value = null
  try {
    const r = await fetchModels(editing.id)
    availableModels.value = r.models
    if (!r.models.length) msg.value = { type: 'err', text: '未获取到模型列表' }
  } catch (e) {
    msg.value = { type: 'err', text: String(e) }
  } finally {
    loadingModels.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-head">
        <h3>管理模型</h3>
        <button @click="emit('close')">✕</button>
      </div>
      <div class="body">
        <!-- ============ LEFT: 模型列表 ============ -->
        <aside class="left">
          <div class="list-head">
            <span class="list-title">模型列表（{{ models.length }}）</span>
            <button class="primary" @click="startAdd">+ 添加模型</button>
          </div>
          <div v-if="models.length === 0" class="empty">暂无模型，点击右上角「+ 添加模型」。</div>
          <div v-else class="list">
            <div
              v-for="m in models"
              :key="m.id"
              class="card"
              :class="{ sel: panelMode === 'edit' && editing.id === m.id }"
            >
              <div class="card-main" @click="startEdit(m)">
                <div class="card-title">
                  <b>{{ m.label }}</b>
                  <span class="prov">{{ m.provider }}</span>
                  <span v-if="m.id === activeId" class="badge current">对话</span>
                  <span v-if="m.id === visionId" class="badge vision">视觉</span>
                </div>
                <div class="card-sub">{{ m.model || '（未设置模型）' }}</div>
                <div class="card-sub2">{{ m.base_url || '（未填 base_url）' }}</div>
              </div>
              <div class="card-ops" @click.stop>
                <button @click="startEdit(m)">编辑</button>
                <button class="danger" @click="doDelete(m)">删除</button>
              </div>
            </div>
          </div>

          <!-- 角色分配（参照 LingChat：对话模型 / 视觉模型） -->
          <div class="role-assign">
            <label class="role-label">对话模型
              <select :value="activeId" :disabled="busy" @change="doActivate(($event.target as HTMLSelectElement).value)">
                <option v-for="m in models" :key="m.id" :value="m.id">
                  {{ m.label || m.model }}
                </option>
              </select>
            </label>
            <label class="role-label">视觉模型
              <select
                :value="visionId"
                :disabled="busy"
                @change="doSetVision(($event.target as HTMLSelectElement).value)"
              >
                <option value="">跟随对话模型</option>
                <option v-for="m in models" :key="m.id" :value="m.id">
                  {{ m.label || m.model }}
                </option>
              </select>
            </label>
            <div class="role-hint">
              <b>对话模型</b>：大脑决策/聊天；<b>视觉模型</b>：看截图（look 技能）。
              视觉留空则跟随对话模型。
            </div>
          </div>
        </aside>

        <!-- ============ RIGHT: 编辑面板 ============ -->
        <section class="right">
          <template v-if="panelMode === null">
            <div class="placeholder">← 选择一个模型编辑，或点「+ 添加模型」</div>
          </template>

          <!-- 编辑表单 -->
          <template v-else>
            <div class="panel-head">
              <h4>{{ editing.id ? '编辑模型' : '添加模型' }}</h4>
              <button @click="closePanel">✕</button>
            </div>
            <div class="form">
              <label>显示名
                <input v-model="editing.label" type="text" placeholder="如 DeepSeek 主用" />
              </label>
              <label>提供商类型
                <select v-model="editing.provider">
                  <option value="openai">OpenAI 兼容</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="lmstudio">LM Studio</option>
                  <option value="gemini">Gemini</option>
                  <option value="kimicode">Kimi Code</option>
                </select>
              </label>
              <label>模型
                <div class="model-row">
                  <input v-model="editing.model" type="text" placeholder="deepseek-v4-flash" />
                  <button :disabled="!editing.id || loadingModels" @click="doFetchModels">
                    {{ loadingModels ? '拉取中…' : '拉取模型' }}
                  </button>
                </div>
              </label>
              <div v-if="availableModels.length" class="model-list">
                <button
                  v-for="mm in availableModels"
                  :key="mm.id"
                  :class="{ on: editing.model === mm.id }"
                  @click="editing.model = mm.id"
                >{{ mm.display_name ? `${mm.display_name} (${mm.id})` : mm.id }}</button>
              </div>
              <label>Base URL
                <input v-model="editing.base_url" type="text" placeholder="https://api.deepseek.com/v1" />
              </label>
              <label>API Key
                <input v-model="editing.api_key" type="password" placeholder="sk-…（留空保持不变）" />
              </label>
              <div class="two">
                <label>Temperature
                  <input v-model.number="editing.temperature" type="number" step="0.1" min="0" max="2" />
                </label>
                <label>Top P
                  <input v-model.number="editing.top_p" type="number" step="0.05" min="0" max="1" />
                </label>
              </div>
              <div class="two">
                <label>Max Tokens
                  <input v-model.number="editing.max_tokens" type="number" min="1" />
                </label>
                <label class="check">
                  <input v-model="editing.enable_thinking" type="checkbox" /> 思考模式
                </label>
              </div>
              <label v-if="editing.enable_thinking">思考深度
                <select v-model="editing.reasoning_effort">
                  <option :value="null">默认</option>
                  <option value="low">低</option>
                  <option value="medium">中</option>
                  <option value="high">高</option>
                </select>
              </label>
              <div class="actions">
                <button class="primary" :disabled="busy" @click="save">
                  {{ editing.id ? '保存修改' : '创建模型' }}
                </button>
                <button :disabled="busy" @click="closePanel">取消</button>
              </div>
            </div>
          </template>
        </section>
      </div>

      <div v-if="msg" class="msg" :class="msg.type">{{ msg.text }}</div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.modal {
  width: 92vw;
  max-width: 1120px;
  height: 88vh;
  max-height: 720px;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  backdrop-filter: blur(14px);
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-soft);
  flex-shrink: 0;
}
.modal-head h3 { margin: 0; }
.body { display: flex; flex: 1; min-height: 0; margin-top: 12px; gap: 16px; }

/* ---------- 左：列表 ---------- */
.left {
  width: 42%;
  min-width: 300px;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border-soft);
  padding-right: 14px;
}
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  flex-shrink: 0;
}
.list-title { font-size: 13px; color: var(--muted); }
.list { display: flex; flex-direction: column; gap: 8px; overflow-y: auto; flex: 1; min-height: 0; }
.empty { font-size: 12px; color: var(--muted); padding: 16px 0; text-align: center; }
.card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.card:hover { border-color: var(--border); background: rgba(255, 255, 255, 0.05); }
.card.sel { border-color: var(--accent); background: rgba(121, 217, 255, 0.08); }
.card-main { flex: 1; min-width: 0; }
.card-title { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.card-title b { font-size: 14px; }
.prov {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 8px;
  background: rgba(121, 217, 255, 0.16);
  color: var(--accent);
}
.badge.current {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 8px;
  background: rgba(74, 222, 128, 0.16);
  color: var(--ok);
  border: 1px solid rgba(74, 222, 128, 0.4);
}
.badge.vision {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 8px;
  background: rgba(251, 191, 36, 0.16);
  color: #fcd34d;
  border: 1px solid rgba(251, 191, 36, 0.4);
}
.card-sub { font-size: 12px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-sub2 { font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-ops { display: flex; flex-direction: column; gap: 5px; flex-shrink: 0; }
.card-ops button { font-size: 11px; padding: 2px 8px; }

/* ---------- 角色分配（对话 / 视觉） ---------- */
.role-assign {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-soft);
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}
.role-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
.role-label select {
  flex: 1;
  min-width: 0;
  background: rgba(255, 255, 255, 0.09);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
}
.role-hint { font-size: 11px; color: var(--muted); line-height: 1.5; }
.role-hint b { color: var(--text); }

/* ---------- 右：面板 ---------- */
.right { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-size: 13px;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  flex-shrink: 0;
}
.panel-head h4 { margin: 0; font-size: 14px; }
.form { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; flex: 1; min-height: 0; padding-right: 4px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
label.check { flex-direction: row; align-items: center; gap: 6px; }
.two { display: flex; gap: 10px; }
.two label { flex: 1; }
.model-row { display: flex; gap: 6px; }
.model-row input { flex: 1; }
.model-list { display: flex; flex-wrap: wrap; gap: 6px; }
.model-list button {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border-soft);
  color: var(--muted);
}
.model-list button.on { background: rgba(121, 217, 255, 0.16); color: var(--accent); border-color: var(--accent); }
.actions { display: flex; gap: 8px; margin-top: 4px; }

.msg { margin-top: 10px; padding: 6px 10px; border-radius: 6px; font-size: 12px; flex-shrink: 0; }
.msg.ok { background: rgba(74, 222, 128, 0.16); color: #6ee7a0; }
.msg.err { background: rgba(248, 113, 113, 0.16); color: #fca5a5; }
</style>
