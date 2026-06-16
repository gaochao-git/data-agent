import { ref } from 'vue'

const enableDeepThinking = ref(false)

export function useDeepThinking() {
  return {
    enableDeepThinking,
  }
}
