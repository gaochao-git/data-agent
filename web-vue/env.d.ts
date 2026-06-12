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
