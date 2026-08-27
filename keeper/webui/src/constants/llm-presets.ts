/**
 * LLM 提供商预设（快速配置，参照 LingChat 的 llm-presets）。
 *
 * 前端「管理模型」的添加/编辑面板会遍历本数组渲染预设按钮，
 * 点击即把表单填充为对应配置。新增/修改只需编辑本文件。
 */
export interface LlmPreset {
  key: string
  label: string
  provider: string
  model: string
  base_url: string
}

export const llmPresets: LlmPreset[] = [
  {
    key: 'deepseek-v4-flash',
    label: 'DeepSeek V4 Flash',
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    base_url: 'https://api.deepseek.com/v1',
  },
  {
    key: 'deepseek-chat',
    label: 'DeepSeek Chat',
    provider: 'deepseek',
    model: 'deepseek-chat',
    base_url: 'https://api.deepseek.com/v1',
  },
  {
    key: 'deepseek-reasoner',
    label: 'DeepSeek Reasoner',
    provider: 'deepseek',
    model: 'deepseek-reasoner',
    base_url: 'https://api.deepseek.com/v1',
  },
  {
    key: 'qwen-max',
    label: '通义千问 Max',
    provider: 'openai',
    model: 'qwen-max',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  },
  {
    key: 'kimi',
    label: 'Kimi',
    provider: 'openai',
    model: 'kimi-k2.6',
    base_url: 'https://api.moonshot.cn/v1',
  },
  {
    key: 'ollama',
    label: 'Ollama（本地）',
    provider: 'openai',
    model: '',
    base_url: 'http://localhost:11434/v1',
  },
  {
    key: 'lmstudio',
    label: 'LM Studio（本地）',
    provider: 'openai',
    model: '',
    base_url: 'http://localhost:1234/v1',
  },
]
