import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PresentedFileArtifacts from '../PresentedFileArtifacts.vue'

describe('PresentedFileArtifacts', () => {
  it('renders delivery files and emits preview and download actions', async () => {
    const artifacts = [
      { path: 'outputs/report.docx', name: 'report.docx', artifactType: 'document' as const },
      { path: 'outputs/data.xlsx', name: 'data.xlsx', artifactType: 'spreadsheet' as const },
      { path: 'outputs/deck.pptx', name: 'deck.pptx', artifactType: 'presentation' as const },
      { path: 'outputs/report.md', name: 'report.md', artifactType: 'markdown' as const },
      { path: 'outputs/report.html', name: 'report.html', artifactType: 'html' as const },
    ]
    const wrapper = mount(PresentedFileArtifacts, { props: { artifacts } })

    expect(wrapper.text()).toContain('Word 文档')
    expect(wrapper.text()).toContain('Excel 工作簿')
    expect(wrapper.text()).toContain('PowerPoint 演示文稿')
    expect(wrapper.text()).toContain('Markdown 文档')
    expect(wrapper.text()).toContain('交互 HTML 页面')
    expect(wrapper.text()).toContain('report.docx')

    const buttons = wrapper.findAll('button')
    await buttons[0]!.trigger('click')
    await buttons[1]!.trigger('click')

    expect(wrapper.emitted('preview')?.[0]).toEqual([artifacts[0]])
    expect(wrapper.emitted('download')?.[0]).toEqual([artifacts[0]])
  })
})
