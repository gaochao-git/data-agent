# Excel 操作参考

## 读取与定位

编辑前先读取工作表和目标区域：

```bash
officecli get '/mnt/user-data/workspace/销售分析.xlsx' / --depth 1 --json
officecli get '/mnt/user-data/workspace/销售分析.xlsx' '/Sheet1/A1:D20' --json
officecli query '/mnt/user-data/workspace/销售分析.xlsx' 'cell[value="销售额"]' --json
```

单元格路径形如 `'/Sheet1/A1'`。工作表名称或属性不确定时，先使用 `officecli help xlsx sheet`、`officecli help xlsx cell`、`officecli help xlsx chart` 查询。

## 常见操作

```bash
officecli create '/mnt/user-data/workspace/销售分析.xlsx'

# 单元格内容与样式
officecli set '/mnt/user-data/workspace/销售分析.xlsx' '/Sheet1/A1' --prop value='月份' --prop bold=true
officecli set '/mnt/user-data/workspace/销售分析.xlsx' '/Sheet1/B2' --prop value=1200

# 公式
officecli set '/mnt/user-data/workspace/销售分析.xlsx' '/Sheet1/C2' --prop formula='=SUM(B2:B13)'

# 排序前先确认区域内没有不应移动的合并单元格或公式
officecli set '/mnt/user-data/workspace/销售分析.xlsx' '/Sheet1/A1:C20' --prop sort='B desc' --prop sortHeader=true
```

插入行列、数据透视表、条件格式或图表前，先读目标区域并查询相应 help。不要依赖猜测的工作表、列号、公式范围或数据类型。

## 交付前检查

```bash
officecli save '/mnt/user-data/workspace/销售分析.xlsx'
officecli validate '/mnt/user-data/workspace/销售分析.xlsx'
officecli view '/mnt/user-data/workspace/销售分析.xlsx' issues
```
