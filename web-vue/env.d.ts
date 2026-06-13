/// <reference types="vite/client" />

declare module '*.json' {
  const value: any
  export default value
}

declare module 'markdown-it' {
  interface MarkdownIt {
    render(src: string): string
  }
  interface MarkdownItConstructor {
    new (options?: Record<string, unknown>): MarkdownIt
    (options?: Record<string, unknown>): MarkdownIt
  }
  const MarkdownIt: MarkdownItConstructor
  export default MarkdownIt
}

declare module '@/utils/request' {
  const request: <T = any>(options: Record<string, unknown>) => Promise<T>
  export default request
}

declare module '@/utils/requestStream' {
  const requestStream: (options: Record<string, unknown>) => Promise<Response>
  export default requestStream
}

declare module '@/store/modules/user' {
  const useUserStore: () => {
    id?: string | number
    name?: string
    permissions?: string[]
    getInfo?: () => Promise<unknown>
  }
  export default useUserStore
}

declare module '@/api/psoss/records' {
  export function getUserOrgList(...args: unknown[]): Promise<unknown>
}
