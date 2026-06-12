在[complaint-analysis-new0607.md](complaint-analysis-new0607.md) 基础之上，还需要增加如下需求：

在调用大模型的时候，需要提供目前的工具信息；
需要探讨一下是否可以复用 @server/apps/complaint_app/opencode_runtime/tools的架构，是否可以使用。
我想增加一下search_law工具，这个工具的入参就是一句话，出参就是法律名称及相关条款内容。
它的实现方式是一个技能，skill.md文档。

这个skill.md 需要使用2个工具完成：读文档工具、查文档工具。

读文档工具：是读取指定path的文件内容；
查文档工具：是查询指定path的文件中关键词的内容片段（类似grep），基于目前的shell工具，查看是否可以直接复用，或者进一步包装一下。