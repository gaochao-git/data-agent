import { ref } from 'vue'

/**
 * 深度思考用户意图，三态：
 * - true / false：用户显式选择，请求携带 enable_thinking；
 * - null：用户未触碰，请求不传该字段，由后端注册表按模型 default_on 落点
 *   （forced / none 模型下该值无意义，前端开关展示由模型能力推导）。
 */
const enableDeepThinking = ref<boolean | null>(null)

export function useDeepThinking() {
  return {
    enableDeepThinking,
  }
}
