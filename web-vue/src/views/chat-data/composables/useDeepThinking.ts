import { ref } from 'vue'

/**
 * 深度思考用户意图，三态：
 * - true / false：用户显式选择，请求携带 enable_thinking；
 * - null：用户未触碰，请求不传该字段，由后端注册表按模型 default_on 落点
 *   （forced / none 模型下该值无意义，前端开关展示由模型能力推导）。
 */
const enableDeepThinking = ref<boolean | null>(null)

/**
 * 思考档位（low / medium / high），三态：
 * - null：用户未选择，请求不传字段，由后端按模型 default_effort 落点；
 * - 显式值仅在模型声明了对应 efforts 时被后端采纳，非法值回退默认档。
 */
const thinkingEffort = ref<string | null>(null)

export function useDeepThinking() {
  return {
    enableDeepThinking,
    thinkingEffort,
  }
}
