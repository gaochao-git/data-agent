# PowerPoint 操作参考

## 读取与定位

编辑前先读取页面和形状，页面/形状顺序会随编辑变化：

```bash
officecli view '/mnt/user-data/workspace/经营汇报.pptx' outline
officecli get '/mnt/user-data/workspace/经营汇报.pptx' '/slide[1]' --depth 1 --json
officecli query '/mnt/user-data/workspace/经营汇报.pptx' 'shape:contains("销售额")' --json
```

页面路径形如 `'/slide[1]'`。`shape[1]` 往往是标题占位符，修改正文前必须先重新 `get`；不确定属性时使用 `officecli help pptx slide`、`officecli help pptx shape`、`officecli help pptx chart`。

## 常见操作

```bash
officecli create '/mnt/user-data/workspace/经营汇报.pptx'
officecli add '/mnt/user-data/workspace/经营汇报.pptx' / --type slide --prop title='经营总览'
officecli add '/mnt/user-data/workspace/经营汇报.pptx' '/slide[1]' --type shape --prop text='销售额同比增长' --prop x=2cm --prop y=4cm --prop size=24

# 仅替换已经确认存在的文字
officecli set '/mnt/user-data/workspace/经营汇报.pptx' / --find '草稿' --replace '正式版' --json
```

添加图表、表格、图片、动画、主题或连接线前，先读取目标页面并查询对应 help。不要启动 `watch`，也不要创建长期运行的预览服务。

## 交付前检查

```bash
officecli save '/mnt/user-data/workspace/经营汇报.pptx'
officecli validate '/mnt/user-data/workspace/经营汇报.pptx'
officecli view '/mnt/user-data/workspace/经营汇报.pptx' issues
```
