# 本体 Action

## 适用

```text
修改对象属性
状态变更
业务标记
用户明确要求执行操作
```

## 工具

```text
list_action_types
execute_action
```

## 执行前

```text
1. list_action_types 确认 Action 存在且已启用
2. 确认目标对象
3. 确认参数完整、值类型合法
4. 高风险/批量/状态迁移先预览或征求确认
```

## 执行

```text
execute_action
  action_type
  object_key
  parameters
```

## 约束

```text
只有 experimental/certified Action 可执行
派生属性只读，不能写
标识属性不可随意改
部署开关关闭时如实说明，不绕过
写操作必须用户明确表达，不能从分析问题推断
```

## 返回

```text
执行记录
编辑列表
审计信息
```

## 失败处理

```text
Action 不存在 → 列出可用 Action
参数不合法 → 指出字段与期望类型
对象不存在 → 不产生写入
未启用 → 说明能力边界
```
