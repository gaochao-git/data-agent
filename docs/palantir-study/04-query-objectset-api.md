# Palantir 对象查询 / 对象集合 / 分页研读

## 0. 依据

**行号约定**：除显式标注 `server/...` 或 `docs/...` 的条目外，所有 `文件:行号` 均相对 `docs/palantir-ontology/`（官方文档本地副本，抓取日期 2026-10-17）。只读研读，未改动任何代码。为节省篇幅，同一段内紧跟上一处引用的 `:行号` 简写指同一个文件。

**必读文件（20 篇，全部精读）**

| 文件 | 行数 | 用途 |
|---|---|---|
| `api/v2/ontologies-v2-resources/ontology-objects/ontology-object-basics.md` | 8 | 对象定义 |
| `api/v2/ontologies-v2-resources/ontology-objects/list-objects.md` | 83 | 列表查询 + 分页 |
| `api/v2/ontologies-v2-resources/ontology-objects/search-objects.md` | 99 | 条件查询 |
| `api/v2/ontologies-v2-resources/ontology-objects/get-object.md` | 57 | 单对象 |
| `api/v2/ontologies-v2-resources/ontology-objects/aggregate-objects.md` | 128 | 对象聚合 |
| `api/v2/ontologies-v2-resources/ontology-object-sets/ontology-object-set-basics.md` | 9 | Object Set 定义 |
| `api/v2/ontologies-v2-resources/ontology-object-sets/load-object-set.md` | 95 | 集合加载 |
| `api/v2/ontologies-v2-resources/ontology-object-sets/load-object-set-objects-or-interfaces.md` | 115 | 接口维度加载 |
| `api/v2/ontologies-v2-resources/ontology-object-sets/load-object-set-multiple-object-types.md` | 122 | 多对象类型加载 |
| `api/v2/ontologies-v2-resources/ontology-object-sets/create-temporary-object-set.md` | 65 | 临时集合 |
| `api/v2/ontologies-v2-resources/ontology-object-sets/aggregate-object-set.md` | 129 | 集合聚合 |
| `api/v2/ontologies-v2-resources/linked-objects/linked-object-basics.md` | 8 | 关联对象定义 |
| `api/v2/ontologies-v2-resources/linked-objects/list-linked-objects.md` | 85 | 关联列表 + 分页 |
| `api/v2/ontologies-v2-resources/linked-objects/get-linked-object.md` | 59 | 单关联对象 |
| `api/general/overview/paging.md` | 123 | 分页总纲 |
| `api/general/overview/limits.md` | 28 | 速率/并发上限 |
| `api/general/overview/versioning.md` | 77 | 版本与 preview |
| `object-explorer/search-syntax.md` | 57 | UI 搜索语法 |
| `ontology/search-syntax.md` | 51 | 正则搜索语法 |
| `ontology/overview-semantic-search.md` | 27 | 语义搜索 |

**为补齐语义而额外引用的同目录官方页（未越出 `docs/palantir-ontology/`）**：`ontologies/oss-limitations.md`（140 行，Object Set Service 上限，本笔记最关键的一篇）、`object-backend/overview.md`（123 行，OSS 与 object set 分类）、`functions/api-object-sets.md`（474 行，search around 上限）、`ontology-sdk/typescript-osdk.md`（1827 行，link 多重性/聚合/分页）、`object-link-types/link-types-overview.md`、`object-link-types/create-link-type.md`、`object-link-types/edit-link-types.md`、`object-explorer/understanding-text-search.md`、`ontology/derived-properties.md`、`ontology/derived-properties-reference.md`、`workshop/derived-properties.md`、`object-indexing/data-restrictions.md`。

**项目侧对照（仅按要求读指定范围）**：`server/apps/chat_data_app/services/ontology/README.md`（派生属性/对象查询/图查询段）、`docs/ontology-capability-audit.md`。

## 1. 对象查询语义（list vs search vs get）

| 维度 | get object | list objects | search objects | loadObjects（对象集） |
|---|---|---|---|---|
| 方法/路径 | `GET .../objects/{objectType}/{primaryKey}`（`ontology-objects/get-object.md:8`） | `GET .../objects/{objectType}`（`list-objects.md:8`） | `POST .../objects/{objectType}/search`（`search-objects.md:8`） | `POST .../objectSets/loadObjects`（`load-object-set.md:8`） |
| 过滤 | 无（主键定位，`get-object.md:14`） | **无 where 参数**，仅 `select`/`orderBy`（`list-objects.md:41-54`） | `where` 过滤树（`search-objects.md:14-15`） | 集合定义内的 `filter`（`ontology-sdk/websocket-subscriptions.md:102-113`） |
| 排序 | 不适用 | `orderBy=properties.{p}:{asc\|desc}`，逗号分隔多字段，`p` 可简写（`list-objects.md:48`） | 参数表**未列** `orderBy`（`search-objects.md:58-63`） | 未在本地副本明确 |
| 分页 | 单条返回（`get-object.md:46-56`） | `pageSize` 默认 1,000 + `pageToken`（`list-objects.md:45-46`） | 描述提 pageSize，但**参数表无 pageSize/pageToken**（`search-objects.md:38-41,58-63`） | body 内 `pageSize`/`pageToken`，示例 10000（`load-object-set.md:62-63`） |
| totalCount | 无 | 无（`list-objects.md:61-82`） | 无（`search-objects.md:85-98`） | `loadObjects` 无；`loadObjectsOrInterfaces` 有 `totalCount: 3`（`load-object-set-objects-or-interfaces.md:113`） |

**filter 结构**：搜索与聚合共用同一 `where` 树。

- 叶子形如 `{"type":"eq","field":"age","value":21}`（`search-objects.md:70-77`）；聚合请求把同一个 `where` 与 `aggregation`/`groupBy` 并列（`aggregate-objects.md:56-76`）。
- 算子表（`search-objects.md:17-33`）：`lt/gt/lte/gte/eq`（number,string,date,timestamp）、`isNull`（全类型）、`contains`（仅 array）、布尔 `not/and/or`、词项类 `containsAllTermsInOrderPrefixLastTerm`、`containsAllTermsInOrder`、`containsAnyTerm`、`containsAllTerms`（string），`startsWith` 是前者已废弃别名（`search-objects.md:33`）。
- 深度限制：查询最多 **3 层深**（`search-objects.md:35`）；部分词不匹配，"除非显式说明"（`search-objects.md:36`）。

**分页规则（总纲在 `paging.md`）**

- `nextPageToken` 存在=还有下一页；不存在=最后一页（`paging.md:8`）。
- pageToken **短时效、只适合立即顺序使用**（`paging.md:13`）。
- 两种一致性：默认=返回最新数据，**翻页间数据变化会产生重复或缺失条目**（`paging.md:16-18`）；snapshot=翻页前定格，代价是陈旧（`paging.md:20-22`）。仅 `loadObjects` / `loadObjectsMultipleObjectTypes` / `loadObjectsOrInterfaces` 支持 `snapshot`（`paging.md:24-27`），"其余分页端点只有默认行为"（`paging.md:29`）。
- **`pageSize` 只是建议值**：实际可能多也可能少（`paging.md:34-36`）；只要还有结果，**每页至少 1 条**（`paging.md:38`）。
- **默认页大小即最大页大小**，"除非另作说明"；请求超过上限会被**削到上限**（`paging.md:40`）。
- 跨页总量上限：`list objects` 全部分页合计最多 **10,000** 条，超出继续翻页**抛错**（`paging.md:44`）。OSv1 同理，超限报 `ObjectsExceededLimit`；OSv2 无此上限（`list-objects.md:19-20`）。
- `list objects` 明确"不保证一致性，可能缺项或重复"（`list-objects.md:16-17`），页大小可能大于或小于请求值（`list-objects.md:22-24`），null 属性不返回（`list-objects.md:26`）。

**totalCount 约定**：官方在这批端点里**没有**"列表查询返回总数"的约定。仅两个 preview 端点示例出现 `totalCount`（`load-object-set-objects-or-interfaces.md:113`；`load-object-set-multiple-object-types.md:108`），后者写作 `"totalCount": "3,"`（带多余逗号、类型变字符串），属示例笔误，不能当作类型契约。`aggregate` 系列无 totalCount、无分页（`aggregate-objects.md:84-128`）。

**文档内部不一致（按"文档未明确"处理）**

1. `search-objects.md` 正文描述分页语义（`:38-41`），参数表却没有 `pageSize`/`pageToken`（`:58-63`），响应示例也无 `nextPageToken`（`:85-98`）。
2. `load-object-set.md` 的 `pageSize`/`pageToken` 只出现在 body 示例（`:62-63`），查询参数表未列（`:42-48`）——是否支持、上限多少，**文档未明确**。
3. 临时 object set 过期时间：API 页写"expires after one hour"（`create-temporary-object-set.md:19`），架构页写"expires within 24 hours"（`object-backend/overview.md:61`）。

## 2. 关联对象遍历

**方向语义**：link type 本身是双向的，两侧各有自己的 API name，可分别独立遍历（`object-link-types/link-types-overview.md:20-22`）；创建一条 link type 不会隐式生成反向 link type（`:24`）。API 层面没有 `reverse` 参数，方向由"当前 objectType + 该侧的 linkType API name"决定：`linkType` 是"当前对象与目标对象之间这条 link 的 API name"（`list-linked-objects.md:40`）。

**list linked objects**（`GET .../objects/{objectType}/{primaryKey}/links/{linkType}`，`list-linked-objects.md:8`）

- 语义：列出"某个对象 + 某条 link type"下的关联对象（`:14`）。
- 分页：`pageSize` 默认 1,000（`:47`）、`pageToken`（`:48`），与 list objects 同一套规则——**不保证一致性、可能缺项或重复**（`:16-17`），页可大可小但"还有结果时至少 1 条"（`:22-24`）。
- 上限：OSv1 合计 10,000，超限 `ObjectsExceededLimit`；OSv2 无上限（`:19-20`）。
- `snapshot` 参数**存在**（`:54`），说明该端点也支持定格翻页（与 `paging.md:24-27` 只列 3 个 object set 端点略有出入 → 文档内部不一致）。
- 排序：支持 `orderBy`（`:50`），语义与 list objects 相同；**默认顺序文档未明确**。
- 过滤：**没有任何 where/filter 参数**（参数表 `:43-56` 只有 pageSize/pageToken/select/orderBy/sdkPackageRid/sdkVersion/excludeRid/snapshot/branch/loadOntologyDefinedDerivedProperties）。即官方"查关联对象"不支持在遍历时附加过滤条件。
- 返回结构：`{nextPageToken, data:[{__rid,__primaryKey,__apiName,...}]}`（`:63-84`）。
- **去重**：文档**未明确**是否去重。示例里两条记录 `__rid` 完全相同（`:68` 与 `:76`）但 `__primaryKey` 不同（80060 / 51060），示例自身矛盾，不能据此推断去重语义。

**get linked object**（`:8` 路径带 `{linkedObjectPrimaryKey}`）：取单个关联对象（`get-linked-object.md:14`）；两对象间无 link 时抛 `LinkedObjectNotFound`（`:16`）。即"单条关联对象"有专门的精确读取入口，与"列表"分开。

**集合级下钻（官方真正的 Join 语义，最关键）**

- 从对象集沿 link 遍历叫 search around（TSv1）或 `pivotTo()`（TSv2），返回的是**对端对象集**，可继续链式过滤/聚合（`ontology-sdk/typescript-osdk.md:471-484`）。
- **OSS 用 left-semi join 实现 search around：只返回"在结果集中、且确有匹配 link"的对象，不复制起始集的数据**；10M 上限作用于这个**去重后的对端对象集合**（`ontologies/oss-limitations.md:106`）。→ 官方"关系下钻"的粒度是**对端 distinct 对象**，不是"父行 × 子行"的展开行。
- link 多重性：单基数 link 给 `SingleLinkAccessor`（`fetchOne`），多基数 link 给的是 **ObjectSet**，因此自带分页（`ontology-sdk/typescript-osdk.md:418-421`、`:448-469`）。
- 上限：TSv1 一次 `.all()` 最多 **3 次 search around**，超出运行时报错（`functions/api-object-sets.md:171,193`）；单次 search around 结果集 OSv2 ≤ **10,000,000**、OSv1 ≤ **100,000**（`functions/api-object-sets.md:171`；`oss-limitations.md:82`），全部数据集合计 ≤ **30,000,000**（`oss-limitations.md:82`）。
- 执行策略与阈值：≤100,000 走内存、>100,000 自动降级 Spark；**任何加载步骤需要超过 25 页数据时也降级 Spark**（`oss-limitations.md:42-45`）。
- 索引依赖：改动 link type 的基数/外键/删除 link type 会触发 reindex，**reindex 完成前依赖该 link 的 search around 是坏的**（`object-link-types/edit-link-types.md:16-29`）。

## 3. Object Set（static / dynamic / temporary / permanent）

**存在理由**：object set 表示本体中的一组对象，用于"一次对多个对象做操作"（`ontology-object-set-basics.md:8-9`）；它们是可保存、可分享的资源，用于跨应用引用（`object-backend/overview.md:53`）。OSS 是本体读服务的统一入口，负责 search/filter/aggregate/load（`object-backend/overview.md:47-49`）。

**四种形态（按"定义方式"×"后端状态"两个正交维度划分）**（`object-backend/overview.md:55`）

- **Static**：存成一串主键，输入数据怎么变都不变（`:57`）。
- **Dynamic**：存成"过滤器表示"，新数据命中过滤器就自动进入集合（`:59`）。
- **Temporary**：主要平台内部在应用/服务间传手；只有创建者可访问，带 `temporary-object-set` RID，**24 小时内过期**（`:61`；与 `create-temporary-object-set.md:19` 的 1 小时冲突）。
- **Permanent**：落在 object backend 里长期复用（`:63`）。

**定义语法（官方 object set definition 是递归结构，本地副本只有片段）**：`{"type":"base","objectType":"Country"}`（`ontology-sdk/websocket-subscriptions.md:52-55`）；`{"type":"filter","objectSet":{...},"where":{...}}`（`:102-113`）。集合运算 `union/intersect/subtract` 在 TSv1（`functions/api-object-sets.md:239-243`）与 TSv2（`ontology-sdk/typescript-osdk.md:953-965`）都有。完整定义类型枚举（static/union/intersect 等在 HTTP body 里的名字）**本地副本未收录 → 文档未明确**。

**load / aggregate 语义**

- `loadObjects`：按定义把集合内的对象取出来（`load-object-set.md:14`）；null 属性不返回（`:19`）；vector 属性仅在 `select` 中显式请求时返回（`:21`）；`pageSize` 是上限、因内存限制可能短页（`:23-26`）；OSv1 合计 10,000 上限（`:16-17`）。
- `loadObjectsOrInterfaces`（preview，需 `preview=true`，`:14-17`）：接口维度返回，结果**不能混"接口属性"与"对象类型属性"**，否则报错（`:24-25`）；元数据前缀是 `$` 而非 `__`（`:30-31`）。
- `loadObjectsMultipleObjectTypes`（preview，`:14-17`）：结果可按对象类型或接口作用域，接口作用域额外返回 `interfaceToObjectTypeMappings`（`:19-23`、`:109-120`）。
- `aggregate`：对集合内对象做聚合（`aggregate-object-set.md:14`），请求体是 `objectSet` + `aggregation` + `groupBy`（`:44-78`）。
- **`loadObjects` 已废弃**：`oss-limitations.md:93` 明确"`loadObjects` endpoint is deprecated"，建议迁移到带分页的 `StaticObjectSetV2`；且它必须返回全部请求对象，超内存直接报 `ObjectSet:ResultSizeLimitExceeded`，不做部分返回。

**与查询结果的关系**：查询结果若要"跨轮/跨应用存活"必须落到 object set（static 存主键 / dynamic 存过滤器）。这正是本项目缺的那一层（`docs/ontology-capability-audit.md:27,42,60`：Object Set 无，"查询结果只活在当轮对话"）。

## 4. 聚合语义

**请求/响应结构**（v2 `aggregateObjects`，`aggregate-objects.md:8`）

- `aggregation[]` 每项 `{type, field, name}`，`name` 是**命名聚合**（`aggregate-objects.md:44-55`）；响应 `data[].metrics[].name/.value` 按名返回（`:89-98`）。
- `where` 与查询同构（`:56-60`）。
- `groupBy[]` 每项 `{field, type}`，本地示例出现两种：`type:"range"` 带 `ranges[{startValue,endValue}]`、`type:"exact"`（`:61-76`）；响应 `group` 里 range 字段回显区间对象、exact 字段回显原值（`:99-105`）。

**算子**：v2 REST 本地副本**未枚举**算子清单（只出现 `min`/`avg`，`aggregate-objects.md:46-53`）→ 该端点算子全集**文档未明确**。可交叉引用 OSDK 文档的可用矩阵：所有类型 `approximateDistinct`/`exactDistinct`；数值类型 `sum/avg/min/max/approximateDistinct/exactDistinct`；date/timestamp 只有 `min/max`（`sum`/`avg` 不可用），请求不支持的组合是类型错误（`ontology-sdk/typescript-osdk.md:1065-1071`、`:1155`）。派生属性聚合目录另有 9 种 + `approximatePercentile`（Ontology Manager 中不可用）（`ontology/derived-properties-reference.md:20-31`）。

**时间桶**：官方 v2 示例只有"显式区间的 range 分桶"（`aggregate-objects.md:62-71`），**没有**"按天/周/月自动时间桶"的描述 → 自动时间桶**文档未明确**（本项目 gap 清单同样记为"无官方 range 时间桶"，`docs/ontology-capability-audit.md:21`）。

**limit / 排序 / 空结果**

- 聚合结果行数上限：**10,000**，超出报 `ObjectsExceededLimit`（`ontology-sdk/typescript-osdk.md:1008`）。
- 指标排序只在 `$groupBy` 最多 1 个属性时可用；2 个及以上分组键时所有指标必须 `"unordered"`（`ontology-sdk/typescript-osdk.md:1016`）。
- 分组聚合返回数组，**无命中时返回空数组**；不分组聚合返回单对象而非数组（`:1063`）。
- v2 聚合端点无分页、无 totalCount（`aggregate-objects.md:84-128`）；分组数超限行为**文档未明确**。

## 5. 搜索语义

**v2 search API（结构化词项搜索）**：见 §1 算子表（`search-objects.md:17-33`）；`pageSize` 是"上限"，页可能因内部内存限制而变小，**不代表没有更多结果**（`:38-41`）；查询 ≤3 层（`:35`）。

**"关键词"vs"全文"的真实差别在 analyzer**（`object-explorer/understanding-text-search.md`）

- 默认 standard analyzer：按空白与标点切词、转小写 → 搜索**默认大小写不敏感**（`:14-15,25`）。
- 非短语查询按 token 独立匹配，顺序/邻近不敏感；要在一起必须用短语 `"brown fox"`（`:26`）。
- **`rown` 不匹配 `brown`**，因为不是完整 token（`:27`）→ 子串语义必须显式用前缀/通配。
- underscore 与句点被当作 token 的一部分：`banana_pudding` 搜 `banana` 不命中，需 `banana*`（`:49-56`）。
- 只有开 `Searchable` render hint 的属性可搜（`:45`）。

**UI 语法（Object Explorer）**：引号=短语（`object-explorer/search-syntax.md:12-16`）、`AND/OR/NOT` 与括号（`:18-36`）、通配 `?`（单字符）与 `*`（0+ 字符）（`:38-44`）、前导通配需在 Ontology Manager 开 **Enable leading wildcards** hint（`:44`）、**`*term*` 双向通配不支持**（`:48`）、`~` 模糊匹配（`:52-54`）。

**正则**（`ontology/search-syntax.md`）：string 属性必须开"Enable regex queries"才可正则（`:16`）；**pattern 匹配整值不做子串**，要子串需 `.*cat.*`（`:17`）；`^`/`$` 不支持（`:18`）；支持算子见 `:22-46`（单独列出 `.`、`?`、`+`、`*`、`{}`、`|`、`()`、`[]`、`"`、`\`）。

**数组 / 地理 / 日期**

- 数组：v2 search 只有 `contains` 适用于 array（`search-objects.md:25`）；数组属性过滤"命中任一元素即返回该对象"（`functions/api-object-sets.md:165`）。
- 地理：v2 search 算子表**无**地理算子（`search-objects.md:17-33`）；地理过滤出现在 Functions/ObjectSet 层（`withinBoundingBox/intersectsPolygon` 等，`functions/api-object-sets.md:85`）；派生属性可作为过滤条件，支持 exact/range/prefix/phrase/full-text/geographic/relative date（`ontology/derived-properties-reference.md:115-116`）。
- 日期：v2 search 的 `lt/gt/lte/gte/eq` 显式支持 date 与 timestamp（`search-objects.md:19-23`）；offset/相对时间在本地 v2 副本中**未明确**。

**语义搜索**：把文本用模型转成 embedding 向量，按 N 维空间最近邻检索（`ontology/overview-semantic-search.md:10-14`）；近邻查询参数受限（KNN `0 < K <= 100`，OSDK `1 <= numNeighbors <= 500`）（`functions/api-object-sets.md:199`；`ontology-sdk/typescript-osdk.md:986`）。

## 6. 分页与上限汇总表

| API | pageSize 默认 / 上限 | 跨页/结果总量上限 | 分页参数位置 | 一致性 | 返回条数约定 |
|---|---|---|---|---|---|
| `getObject` | — | — | — | — | 单条（`get-object.md:14`） |
| `listObjects` | 默认 1,000；按"默认即上限"规则推断上限 1,000（`list-objects.md:45`；`paging.md:40`），页面**未写 maximum** | 合计 10,000（OSv1），超限 `ObjectsExceededLimit`；OSv2 无（`list-objects.md:19-20`；`paging.md:44`） | query | 默认/snapshot 均可（`list-objects.md:52`），但页面自称"不保证一致性"（`:16-17`） | 页可大可小，还有结果时 ≥1（`:22-24`） |
| `searchObjects` | 正文称 pageSize 为上限（`search-objects.md:38`），参数表未列（`:58-63`）→ 上限**未明确** | 未明确 | 未明确 | 未明确 | 短页≠结束（`:38-41`） |
| `aggregateObjects` | 无分页 | 未明确 | — | — | 分组行数组，空命中为空数组（`ontology-sdk/typescript-osdk.md:1063`） |
| `listLinkedObjects` | 默认 1,000（`list-linked-objects.md:47`） | 合计 10,000（OSv1），OSv2 无（`:19-20`） | query | `snapshot` 存在（`:54`）；页面称不保证一致（`:16-17`） | 页可大可小，≥1（`:22-24`） |
| `getLinkedObject` | — | — | — | — | 单条；无 link 抛 `LinkedObjectNotFound`（`get-linked-object.md:16`） |
| `loadObjects` | body 示例 10000（`load-object-set.md:62`），参数表未列 → 上限**未明确** | 合计 10,000（OSv1）（`:16-17`） | body | snapshot（`paging.md:25`） | 短页≠结束（`:23-26`）；端点为 deprecated（`oss-limitations.md:93`） |
| `loadObjectsOrInterfaces` | 示例 10000（`load-object-set-objects-or-interfaces.md:75`） | 合计 10,000（OSv1）（`:27-28`） | body | snapshot（`paging.md:27`） | 短页≠结束（`:35-38`）；含 `totalCount`（`:113`） |
| `loadObjectsMultipleObjectTypes` | 示例 10000（`load-object-set-multiple-object-types.md:73`） | 合计 10,000（OSv1）（`:25-26`） | body | snapshot（`paging.md:26`） | 短页≠结束（`:33-36`）；含 `totalCount`（`:108`） |
| `aggregateObjectSet` | 无分页（`aggregate-object-set.md:84-128`） | 结果 ≤10,000（`ontology-sdk/typescript-osdk.md:1008`） | — | — | 同上 |
| search around / pivotTo | — | 单次 OSv2 10M、OSv1 100k；合计 30M；≤3 次/查询（TSv1）（`oss-limitations.md:82`；`functions/api-object-sets.md:171,193`） | — | — | 对端 distinct 对象集（`oss-limitations.md:106`） |
| 派生属性 collect | 上限默认 10、最大 100（`ontology/derived-properties-reference.md:57`） | 同上 | — | — | 截断只影响返回值，不影响过滤（`ontology/derived-properties.md:80-84`） |
| 派生属性排序 | — | Workshop 中把集合**限制到 200 行**（`workshop/derived-properties.md:60`；`ontology/derived-properties-reference.md:116`） | — | — | 大于 200 行时无法完整排序 |
| Functions `.all()` | — | 100,000 上限，>10,000 可能超时（`functions/api-object-sets.md:249-251`；`oss-limitations.md:45,69,83`） | — | — | — |

## 7. 对本项目的直接含义

### 7.1 与项目现状对照

- 本项目对象查询是"直连源库投影 + 自建 where 树"，**不是**官方 OSS；官方分页/上限规则只在"设计意图"层面对我们有效（`server/apps/chat_data_app/services/ontology/README.md:130-141,178-189`；`docs/ontology-capability-audit.md:19-21`）。
- 本地硬约束：对象一跳 links（外键型，**单关系最多 20 条**）（`README:136`；`audit:20`）；对象查询 **limit 上限 100**（`README:200`）；派生属性在列表页**按当前页逐行计算、分页有界**（`README:73-75`）。
- 官方**没有任何**"单关系 20 条"或"limit 100"这类规则；官方同类上限是 10,000 / 10M / 30M 且**超限报错而非静默截断**（`list-linked-objects.md:19-20`；`oss-limitations.md:82`）。→ 我们这两个数字都是本地方案自带约束，官方文档无法为其背书。
- 搜索能力缺口已自认：官方 prefix/phrase/full-text/geographic/relative date，我们只有 keyword LIKE + 比较/isNull/in（`README:198`；`audit:40`）。

### 7.2 官方文档中可能解释「一对多关系下钻少返行」的规则（按可能性排序）

| # | 官方规则 | 依据 | 如何导致少返行 |
|---|---|---|---|
| A | **短页 ≠ 结束**：pageSize 只是建议值，服务端因内存限制可返更少；必须看 `nextPageToken` 继续翻 | `oss-limitations.md:91`（"Do not interpret a short page as the end of the result set"）；`paging.md:34-38`；`list-objects.md:22-24`；`search-objects.md:38-41`；`list-linked-objects.md:22-24`；`load-object-set.md:23-26` | 只取第一页/把"返回行数 < 请求值"当终止条件，一对多子集合会直接被截断 |
| B | **官方下钻返回的是对端 distinct 对象集**（left-semi join），不按父行复制 | `oss-limitations.md:95-106`；`ontology-sdk/typescript-osdk.md:418-421,448-469,471-484` | 若我们按"父行 × 子行"预期行数，官方语义天然更少；反之若我们 JOIN 后按 object_key 去重，也会比 SQL 行数少 |
| C | **默认分页不保证一致**：翻页间数据变化会缺项或重复 | `paging.md:13-18`；`list-objects.md:16-17`；`list-linked-objects.md:16-17` | 翻页期间子表新增/更新 → 中间页丢行；官方解法是 snapshot（`paging.md:20-27`） |
| D | **默认页大小即上限，超出被削到上限** | `paging.md:40` | 把大 limit 当 pageSize 传，期望 N 行实际被削；或反过来把被削后的页当全量 |
| E | **多基数 link 是 ObjectSet，必然分页**；单基数才是 `fetchOne` | `ontology-sdk/typescript-osdk.md:418-421` | 一对多链路上误用"取单对象"语义（select 而非 aggregate）→ 只得到 1 条 |
| F | **一对多遍历必须聚合**：任一 traversal 可达多对象时只允许聚合，`select` 不合法 | `ontology/derived-properties-reference.md:14,97-107` | 用 select 型派生属性承载一对多，语义上只能取一个对象（我们的 select 仅限 from 侧单对象，`README:63-64`） |
| G | **collect 类聚合有截断上限**：默认 10、最大 100 | `ontology/derived-properties-reference.md:55-58`；`ontology/derived-properties.md:78-84` | 若用 collect 承载子对象列表，官方语义本身只返 10 条；我们当前只做 count/sum/avg/min/max 标量（`README:61-63`）→ 暂不适用，但扩能力时必踩 |
| H | **排序派生属性时集合被限制到 200 行**（Workshop） | `workshop/derived-properties.md:60`；`derived-properties-reference.md:116` | 官方先例：排序+派生属性会静默缩集合。我们"排序按 LEFT JOIN 下推"（`README:75`）若照抄护栏就会出现 200 行截断 |
| I | **超限是显式报错**，不是静默少行 | `list-objects.md:19-20`；`paging.md:44`；`oss-limitations.md:82,93` | 若我们线上"少行但无错"，说明截断发生在我们自己代码里，而不是官方式上限 |
| J | **link 索引未就绪时下钻是坏的** | `object-link-types/edit-link-types.md:16-29` | 改了基数/外键后 reindex 未完成，一对多侧会缺链接（我们直连源库，此条对应"关联列未解析/未映射"，`README:149-150`） |
| K | **null 属性不返回**（非少行，但常被误判为少行/少列） | `list-objects.md:26`；`list-linked-objects.md:26` | 行数不变、列缺失，容易和"少返行"混为一谈 |
| L | **默认排序未定义**，只有 collect 明确"顺序无保证" | `list-objects.md:48`（orderBy 可选，无默认序说明）；`derived-properties-reference.md:53` | 无稳定排序 + 翻页 → 重复/遗漏（与 C 叠加） |

**判定建议（依据上面的规则，不改代码即可区分）**

1. 若返回行数**恰好等于**某个本地常量（20 或 100）→ 是本地上限（`README:136,200`），不是官方式分页语义（I）。
2. 若返回行数**小于** limit 但对方表确有更多子行 → 先查是否只取了首页（A）、是否被 JOIN 去重（B）、是否 select 型只取一条（E/F）。
3. 若有 `total_count` 与 `data.length` 不一致 → 注意我们的 count 查询与取行查询**构造不同**（`README:190` 明确 count 不带 select-only 派生 JOIN，列表带 LEFT JOIN，`README:74-75`），这本身就能造成"总数 > 实返行数"。
4. 审计中"客户→订单 3 笔，与派生 `order_count` 一致"（`audit:129`）说明该次少行是**真实基数**，不是分页截断——后续核查应先取一个已知一对多父对象做对照。

### 7.3 明确排除项

- 官方**没有**"单对象 links 最多 20 条"的规定；我们要么承认这是本地硬上限并在返回里显式标注截断，要么把上限抬到有依据的数量级（10,000 级，`list-linked-objects.md:19-20`）。
- 官方**没有**"列表查询返回 totalCount"的约定（`list-objects.md:61-82`；`search-objects.md:85-98`），我们若对外承诺 `total_count`，那是自有契约，需要自己定义语义（是否等于过滤后全量）并自行保证与行查询一致。
- 官方 `list linked objects` **不支持遍历时过滤**（`list-linked-objects.md:43-56`）；"带条件的关系下钻"在官方要走 object set（`pivotTo` + `where`，`typescript-osdk.md:471-484`）。我们若在 links 上直接挂过滤，属能力扩展，不是官方语义。

## 8. 待确认问题

1. `searchObjects` 到底支不支持 `pageSize`/`pageToken`、上限多少？（正文提、参数表不列，`search-objects.md:38-41,58-63`）本地副本无法定论，需查线上 API 或 OpenAPI 快照。
2. `loadObjects` 的 `pageSize` 上限（示例 10000 是否即上限）文档未明确（`load-object-set.md:62`；`paging.md:40`）。
3. `listLinkedObjects` 是否对同一对端对象去重？示例自身 `__rid` 重复（`list-linked-objects.md:68,76`）无法作为证据 → 需实测。
4. 官方默认排序（未传 `orderBy` 时）是什么，是否稳定？本地副本**未明确**（仅 `derived-properties-reference.md:53` 对 collect 说明"顺序无保证"）。
5. 聚合分组数上限、时间桶（按天/周/月）是否存在于 v2 aggregate？本地副本未收录（`aggregate-objects.md` 全文）。
6. 官方 object set definition 的完整类型枚举（static/union/intersect/subtract 在 HTTP body 的 `type` 名）本地副本缺失（只有 `base`/`filter`，`websocket-subscriptions.md:52-55,102-113`）→ 若要做 Object Set，需要补抓 API 定义页。
7. 临时 object set 过期时间 1 小时 vs 24 小时（`create-temporary-object-set.md:19` vs `object-backend/overview.md:61`），以哪个为准？
8. 派生属性 `select` 在多基数链路上的官方行为是"报错"还是"任取一条"？`derived-properties-reference.md:97-107` 只说"必须聚合"，未说违反时如何失败。
9. 本项目 `limit=100` 与官方 `pageSize=1000`/`10,000` 的差距是否需要重新论证（`README:200` vs `list-objects.md:45`）——这直接决定"少返行"是设计约束还是缺陷。
