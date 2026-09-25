import { EditorView } from '@codemirror/view'

// CodeMirror 主题 - Apple 风格（自 SqlEditor.vue 抽出，纯配置无组件耦合）
export const appleTheme = EditorView.theme({
  '&': {
    backgroundColor: '#FFFFFF',
    color: '#1D1D1F',
    height: '100%',
    fontSize: '14px',
  },
  '&.cm-editor': {
    height: '100%',
  },
  '&.cm-editor.cm-focused': {
    outline: 'none',
  },
  '.cm-scroller': {
    overflow: 'auto',
    height: '100%',
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.6',
  },
  '.cm-content': {
    caretColor: '#1677FF',
    padding: '8px 0',
  },
  '.cm-cursor': {
    borderLeftColor: '#1677FF',
    borderLeftWidth: '2px',
  },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {
    backgroundColor: 'rgba(22, 119, 255, 0.12)',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(22, 119, 255, 0.04)',
  },
  '.cm-gutters': {
    backgroundColor: '#FAFAFA',
    color: '#AEAEB2',
    border: 'none',
    borderRight: '1px solid rgba(0, 0, 0, 0.06)',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 12px 0 12px',
    minWidth: '32px',
    fontSize: '12px',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
    color: '#1677FF',
  },
  // SQL 语法高亮
  '.cm-keyword': {
    color: '#1677FF',
    fontWeight: '600',
  },
  '.cm-string': {
    color: '#FF3B30',
  },
  '.cm-number': {
    color: '#FF9500',
  },
  '.cm-comment': {
    color: '#8E8E93',
    fontStyle: 'italic',
  },
  '.cm-operator': {
    color: '#1677FF',
  },
  '.cm-punctuation': {
    color: '#636366',
  },
  '.cm-variableName, .cm-propertyName': {
    color: '#30B0C7',
  },
  '.cm-typeName': {
    color: '#30B0C7',
  },
  // 自动补全样式
  '.cm-tooltip': {
    backgroundColor: '#FFFFFF',
    border: '1px solid rgba(0, 0, 0, 0.1)',
    borderRadius: '8px',
    boxShadow: '0 4px 16px rgba(0, 0, 0, 0.12)',
  },
  '.cm-tooltip-autocomplete': {
    '& > ul': {
      fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
      fontSize: '13px',
    },
    '& > ul > li': {
      padding: '4px 12px',
    },
    '& > ul > li[aria-selected]': {
      backgroundColor: 'rgba(22, 119, 255, 0.1)',
      color: '#1677FF',
    },
  },
})
