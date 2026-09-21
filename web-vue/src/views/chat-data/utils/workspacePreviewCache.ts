/**
 * 控制台「工作区文件」预览缓存的失效逻辑。
 *
 * 预览缓存（文本内容 / 二进制 blob）以「归一化后的可见相对路径」为 key，命中时直接短路不发请求
 * （见 conversation/Conversation.vue 的 loadWorkspaceFileContent / loadWorkspaceBinaryFile）。
 * 因此文件在磁盘上被替换后，缓存里的旧副本不会自愈——必须由「知道文件已变」的那一方显式作废。
 *
 * 删除就是最容易被忽略的那一方，而且它不是理论风险：uploads 的重名策略是「已存在才加 -1 后缀」
 * （后端 uploaded_file_service.py::_unique_storage_name），文件删掉后再传同名文件会拿回
 * **与删除前完全相同的路径**，于是旧缓存必然命中——文本预览显示上一份文件的内容，
 * 二进制预览复用一个指向已删文件的 blob URL。
 *
 * 这里只做「按路径作废」这一件事，刻意不在刷新文件列表时按「列表里没有就清」做全量回收：
 * 列表请求失败会得到空列表，那时回收会把用户正在看的内容一起清掉（预览区闪空），代价大于收益；
 * 智能体侧删改同名文件的场景留给「点刷新」与「重复点击同一文件强制重载」两条既有兜底。
 */

/** 预览缓存里的二进制条目至少能提供 blob URL。 */
export interface CachedWorkspaceBlob {
  url: string
}

export interface WorkspacePreviewCacheEviction<T extends CachedWorkspaceBlob> {
  /** 清理后的文本内容缓存；未命中该路径时原样返回入参引用 */
  contents: Record<string, string>
  /** 清理后的二进制缓存；未命中该路径时原样返回入参引用 */
  blobs: Record<string, T>
  /** 被丢弃条目的 blob URL，调用方负责 `URL.revokeObjectURL`；无则 null */
  revokedUrl: string | null
}

/**
 * 从预览缓存里移除一个路径。
 *
 * 两个实现细节：
 * - **返回新对象而不是原地 delete**：缓存在组件里是 ref 的 `.value`，原地删属性不触发依赖更新，
 *   而读取侧用的是 `cache[path] !== undefined` 这类判断，会读到旧值。
 * - **用 hasOwnProperty 判定命中**：空文件的文本内容是 `''`，用真值判断会漏掉它。
 * - 未命中时返回入参原引用，避免制造无意义的新对象去触发一次多余的渲染。
 */
export function evictWorkspaceFileCache<T extends CachedWorkspaceBlob>(
  path: string,
  contents: Record<string, string>,
  blobs: Record<string, T>,
): WorkspacePreviewCacheEviction<T> {
  if (!path) return { contents, blobs, revokedUrl: null }

  // 错误态条目的 url 是空串，此时不该拿空串去 revoke（调用方按真值判断即可）
  const revokedUrl = blobs[path]?.url || null

  let nextContents = contents
  if (Object.prototype.hasOwnProperty.call(contents, path)) {
    nextContents = { ...contents }
    delete nextContents[path]
  }

  let nextBlobs = blobs
  if (Object.prototype.hasOwnProperty.call(blobs, path)) {
    nextBlobs = { ...blobs }
    delete nextBlobs[path]
  }

  return { contents: nextContents, blobs: nextBlobs, revokedUrl }
}
