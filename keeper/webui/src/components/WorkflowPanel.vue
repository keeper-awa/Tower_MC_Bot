<script setup lang="ts">
import { computed } from 'vue'
import type { Status, WorkflowState } from '../api'

const props = defineProps<{ status: Status | null }>()

const wf = computed<WorkflowState>(() => props.status?.wf ?? {
  kind: null, title: '', idx: 0, total: 0, steps: [],
})

// 步骤类型显示名
const typeLabel = computed(() => {
  if (!wf.value.kind) return ''
  return wf.value.kind === 'plan' ? 'Skill 工作流' : '任务大纲'
})

// 进度百分比
const pct = computed(() => {
  if (!wf.value.total) return 0
  return Math.min(100, Math.round((wf.value.idx / wf.value.total) * 100))
})

// 步骤名称美化（GitHub Actions 风格显示）
function stepLabel(s: { type: string; name: string }): string {
  if (s.type === 'skill') return s.name
  const map: Record<string, string> = {
    move_to: '移动',
    equip: '装备',
    use_item: '使用物品',
    get_state: '查看状态',
    screenshot: '截图',
    look_at: '看向',
    attack: '攻击',
    swim: '游泳',
    craft: '合成',
    mine: '挖掘',
    move: '移动',
    chat: '说话',
  }
  return map[s.name] || s.name
}
</script>

<template>
  <div class="panel">
    <h3>当前工作流</h3>

    <!-- 空闲态 -->
    <div v-if="!wf.kind" class="empty">
      <span class="empty-ico">○</span>
      <span>空闲，等待任务</span>
    </div>

    <!-- 工作流运行中 -->
    <div v-else class="wf">
      <!-- 标题栏 -->
      <div class="wf-head">
        <span class="badge" :class="wf.kind">{{ typeLabel }}</span>
        <span class="wf-title">{{ wf.title }}</span>
        <span class="wf-count">{{ wf.idx }}/{{ wf.total }}</span>
      </div>

      <!-- 步骤时间线（GitHub Actions 风格：竖线连接 + 运行图标） -->
      <div class="steps">
        <template v-for="(s, i) in wf.steps" :key="i">
          <div class="step" :class="s.status">
            <!-- 左侧图标列 + 连接线 -->
            <div class="rail">
              <span class="icon" :class="s.status">
                <!-- 运行中：spinner -->
                <span v-if="s.status === 'running'" class="spinner"></span>
                <!-- 完成：绿色 ✓ -->
                <svg v-else-if="s.status === 'done'" class="check" viewBox="0 0 12 12" width="12" height="12">
                  <path d="M2 6.5 L4.5 9 L10 3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                <!-- 待执行：空心圆 -->
                <span v-else class="pending-dot"></span>
              </span>
              <span v-if="i < wf.steps.length - 1" class="connector" :class="s.status === 'done' ? 'filled' : ''"></span>
            </div>
            <!-- 右侧内容 -->
            <div class="content">
              <div class="content-head">
                <span class="step-name">{{ stepLabel(s) }}</span>
                <span v-if="s.status === 'running'" class="step-status running-text">运行中</span>
                <span v-else-if="s.status === 'done'" class="step-status done-text">完成</span>
                <span v-else class="step-status pending-text">等待</span>
              </div>
              <div v-if="s.detail && s.status !== 'pending'" class="step-detail">{{ s.detail }}</div>
            </div>
          </div>
        </template>
      </div>

      <!-- 进度条 -->
      <div class="progress">
        <div class="progress-fill" :style="{ width: pct + '%' }"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wf { display: flex; flex-direction: column; gap: 10px; }

/* 空闲态 */
.empty {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
  padding: 6px 0;
}
.empty-ico {
  font-size: 14px;
  color: var(--muted);
}

/* 标题栏 */
.wf-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  flex-shrink: 0;
}
.badge.plan {
  background: rgba(59, 130, 246, 0.16);
  color: #60a5fa;
  border: 1px solid rgba(59, 130, 246, 0.4);
}
.badge.outline {
  background: rgba(168, 85, 247, 0.16);
  color: #c084fc;
  border: 1px solid rgba(168, 85, 247, 0.4);
}
.wf-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-count {
  font-size: 12px;
  color: var(--muted);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

/* 步骤时间线 */
.steps {
  display: flex;
  flex-direction: column;
  max-height: 230px;
  overflow-y: auto;
}
.step {
  display: flex;
  gap: 10px;
}
/* 左侧图标 + 连接线列 */
.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 18px;
  flex-shrink: 0;
}
.icon {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  z-index: 1;
}
.icon.done {
  background: rgba(34, 197, 94, 0.2);
  color: #22c55e;
  border: 1px solid rgba(34, 197, 94, 0.5);
}
.icon.running {
  background: rgba(59, 130, 246, 0.18);
  border: 1px solid rgba(59, 130, 246, 0.6);
}
.icon.pending {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.25);
}
/* 运行中：spinner（旋转圆环） */
.spinner {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid rgba(59, 130, 246, 0.3);
  border-top-color: #3b82f6;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
/* 完成：绿色 ✓ */
.check { display: block; }
/* 待执行：空心圆 */
.pending-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.18);
}
/* 连接线（竖线） */
.connector {
  width: 2px;
  flex: 1;
  min-height: 6px;
  background: rgba(255, 255, 255, 0.14);
  margin: 1px 0;
}
.connector.filled {
  background: #22c55e;
}

/* 右侧内容 */
.content {
  flex: 1;
  min-width: 0;
  padding: 1px 0 10px;
}
.content-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.step-name {
  font-size: 12px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.step-status { font-size: 11px; flex-shrink: 0; }
.running-text { color: #60a5fa; }
.done-text { color: #22c55e; }
.pending-text { color: var(--muted); }
.step-detail {
  margin-top: 2px;
  font-size: 11px;
  color: var(--muted);
  font-family: 'Consolas', monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 进度条（流动效果） */
.progress {
  height: 8px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  overflow: hidden;
}
.progress-fill {
  position: relative;
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #22c55e);
  transition: width 1.2s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}
/* 扫描光带：一条高亮从起点持续滑向终点（丝滑轨迹） */
.progress-fill::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  width: 45%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.55), transparent);
  animation: wfSweep 1.4s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}
@keyframes wfSweep {
  from { transform: translateX(-100%); }
  to { transform: translateX(320%); }
}
</style>
