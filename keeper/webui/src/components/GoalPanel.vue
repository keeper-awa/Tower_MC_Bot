<script setup lang="ts">
import { ref } from 'vue'
import { postJSON, sendChat } from '../api'
import type { Status } from '../api'

const props = defineProps<{ status: Status | null }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const goal = ref('')
const chat = ref('')
const busy = ref(false)

async function run(fn: () => Promise<unknown>) {
  busy.value = true
  try {
    await fn()
    emit('changed')
  } catch (e) {
    alert(String(e))
  } finally {
    busy.value = false
  }
}

async function submitGoal() {
  const text = goal.value.trim()
  if (!text) return
  await run(() => postJSON('/api/goal', { goal: text }))
}

async function start() {
  await run(() => postJSON('/api/agent/start', { goal: goal.value.trim() }))
}

async function stop() {
  await run(() => postJSON('/api/agent/stop', {}))
}

async function pause() {
  await run(() => postJSON('/api/agent/pause', {}))
}

async function resume() {
  await run(() => postJSON('/api/agent/resume', {}))
}

async function submitChat() {
  const text = chat.value.trim()
  if (!text) return
  await run(async () => {
    await sendChat(text)
    chat.value = ''
  })
}
</script>

<template>
  <div class="goal-bar">
    <div class="row">
      <input v-model="goal" type="text" placeholder="对 AI 发起目标：如 挖 10 个钻石 / 朝 +Z 走 20 格" @keyup.enter="submitGoal" />
      <button class="primary" :disabled="busy || !goal.trim()" @click="submitGoal">设定目标</button>
      <button v-if="status?.agent_running" :disabled="busy" @click="stop">停止</button>
      <button v-else :disabled="busy || !status?.connected" @click="start">启动 AI</button>
      <button v-if="status?.agent_running && !status?.agent_paused" :disabled="busy" @click="pause">暂停</button>
      <button v-if="status?.agent_running && status?.agent_paused" :disabled="busy" @click="resume">恢复</button>
    </div>
    <div class="row">
      <input v-model="chat" type="text" placeholder="对玩家发聊天消息…" @keyup.enter="submitChat" />
      <button :disabled="busy || !chat.trim()" @click="submitChat">发送</button>
    </div>
  </div>
</template>

<style scoped>
.goal-bar { display: flex; flex-direction: column; gap: 8px; }
.row { display: flex; gap: 8px; align-items: center; }
.row input { flex: 1; }
</style>
