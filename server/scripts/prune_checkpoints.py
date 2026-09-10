#!/usr/bin/env python3
"""LangGraph MySQL checkpoint 定时清理脚本（独立运行，不依赖项目代码）。

用法（mode 为必填项，二选一）：
    python3 prune_checkpoints.py dry-run     # 只统计，不写库
    python3 prune_checkpoints.py execute     # 真正删除

清理语义（keep-latest 裁剪）：
  自动发现「checkpoint 体积 > MIN_MB 且最后活动早于 MIN_IDLE_DAYS 天」的会话，
  每个命名空间只保留最新一份快照及其引用的 blob，删除历史快照与未被引用的 blob。
  裁剪后会话仍可正常续聊；损失的是"从中间步骤回溯重跑"的能力（当前代码无该入口）。

安全设计：
  - mode=dry-run 时全程只读，不做任何写操作。
  - 两段式发现，避免全表扫大列：先对 checkpoints 做覆盖索引扫取每线程
    MAX(checkpoint_id)（时间序 uuid6，字典序即时间序）筛闲置候选，
    再只对候选线程走 thread_id 索引精确统计 blob 体积。
  - autocommit=True 运行：每批 DELETE 单语句自提交，绝不使用一个大事务，
    批间休眠 SLEEP_MS 限速；规划读用 conn.begin() 显式圈一致快照、
    try/finally 收口，任何退出路径（skip/dry-run/异常）不遗留事务。
  - 锁冲突(1205/1213)自动退避重试。
  - 保留集按最新快照的 channel_versions 引用取，不按"每通道最大版本"取。
  - 删除前重新校验该会话仍然闲置（发现阶段到执行阶段之间可能有人回来续聊）。
  - keeper 为 v<4 旧格式时给出警告（其父快照被裁后 pending-sends 不可迁移）。
  - 裁剪后回收孤儿 checkpoint_writes（checkpoint_id 已不在 checkpoints 表的行）。
  - 删除后做对账式自检：保留行必须一根不少。

兼容性：语法按 Python 3.6 收敛（无 __future__ annotations、无 fromisoformat）。
依赖：pip install pymysql
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

try:
    import pymysql
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 pymysql：pip install pymysql")

# ============ 固定配置（按部署环境修改） ============
DB_HOST = "62.234.152.217"
DB_PORT = 3307
DB_USER = "gaochao"
DB_PASSWORD = "xxxx"
DB_NAME = "langchian_checkpoint"          # checkpointer 库

# ============ 清理策略 ============
MIN_MB = 20.0                # checkpoint 总体积超过该值才进入裁剪
MIN_IDLE_DAYS = 60           # 最后活动早于该天数才裁剪（活跃会话跳过）
BATCH_ROWS = 200             # 单条 DELETE 覆盖的最大行数
SLEEP_MS = 100               # 每批提交后的间歇
LOCK_TIMEOUT = 5             # innodb_lock_wait_timeout 秒
MAX_RETRIES = 3

MB = 1048576
RETRYABLE_MYSQL_CODES = (1205, 1213)
# checkpoint 的 ts 形如 2026-09-09T10:13:56.098897+00:00
TS_PATTERN = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_mode() -> bool:
    """解析必填 mode，返回是否执行真删。"""
    parser = argparse.ArgumentParser(
        description="LangGraph MySQL checkpoint keep-latest 裁剪", allow_abbrev=False)
    parser.add_argument("mode", choices=["dry-run", "execute"],
                        help="dry-run=只统计不写库；execute=真正删除")
    return parser.parse_args().mode == "execute"


def connect(db_name: str):
    conn = pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                           password=DB_PASSWORD, database=db_name,
                           charset="utf8mb4", autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SET SESSION innodb_lock_wait_timeout = %s", (LOCK_TIMEOUT,))
    return conn


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _zone_offset(raw: Optional[str]) -> timedelta:
    """ISO 时区后缀转 timedelta；缺省按 UTC。"""
    if not raw or raw == "Z":
        return timedelta(0)
    sign = -1 if raw[0] == "-" else 1
    body = raw[1:].replace(":", "")
    return sign * timedelta(hours=int(body[:2]), minutes=int(body[2:4]))


def _parse_ts(raw) -> Optional[datetime]:
    """解析 checkpoint ts 并统一成 UTC aware。

    Python 3.6 既没有 datetime.fromisoformat，其 %z 也不认 "+00:00" 带冒号写法，
    所以手工按段解析。解析不出来返回 None，由调用方按"不可判定"处理。
    """
    match = TS_PATTERN.match(str(raw or "").strip())
    if not match:
        return None
    year, month, day, hour, minute, second, frac, zone = match.groups()
    try:
        local = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second),
                         int((frac or "0")[:6].ljust(6, "0")))
        return (local - _zone_offset(zone)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _idle_too_short(last: Optional[datetime]) -> bool:
    """判不出活动时间一律返回 True（不碰），避免 ts 异常导致误删活跃会话。"""
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(days=MIN_IDLE_DAYS)


def run_with_retry(conn, sql: str, params: tuple) -> int:
    """单批删除：单语句原子，autocommit 下即删即持久，无事务可收口；
    锁冲突(1205/1213)退避重试。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount
        except pymysql.err.OperationalError as err:
            code = err.args[0] if err.args else None
            if code not in RETRYABLE_MYSQL_CODES or attempt >= MAX_RETRIES:
                raise
            time.sleep(attempt * 0.5)
    return 0


# ---------------- 目标发现 ----------------

_UUID_EPOCH_100NS_OFFSET = 0x01B21DD213814000  # 1582-10-15 至 1970-01-01 的 100ns 数


def _idle_cutoff_id(days: int) -> str:
    """构造 days 天前的 uuid6 形字符串作闲置阈值。

    langgraph 的 checkpoint_id 是时间序 uuid6：时间戳 60bit 的高 48bit 在
    uuid 整数的 bits 127..80，低 12bit 在 bits 75..64（中间夹 version=6），
    高位在前，所以规范化字符串的字典序即时间序。按同布局生成阈值后，
    `MAX(checkpoint_id) < 阈值` 即可判闲置，无需逐行解析 JSON。
    """
    unix_ns = int(time.time()) * 10**9 - days * 86400 * 10**9
    ts100 = unix_ns // 100 + _UUID_EPOCH_100NS_OFFSET
    value = (((ts100 >> 12) & 0xFFFFFFFFFFFF) << 80) \
        | (6 << 76) | ((ts100 & 0x0FFF) << 64) | (0x8000 << 48)
    hex32 = "%032x" % value
    return "%s-%s-%s-%s-%s" % (hex32[:8], hex32[8:12], hex32[12:16],
                               hex32[16:20], hex32[20:32])


def discover_prune_targets(cur) -> List[dict]:
    """体积超阈值且闲置超期的会话（两段式，避免全表扫 blob/JSON 大列）。

    第一步：GROUP BY thread_id 取 MAX(checkpoint_id)，thread_id/checkpoint_id
    均在主键与二级索引叶子内，属覆盖索引扫，不触碰 checkpoint JSON 大列；
    与闲置阈值做字符串比较筛出候选（60 天前的会话，可能数千个）。
    第二步：对候选按批 IN(...) 走 thread_id 索引在服务端聚合 blob 体积，
    一次往返算一批，避免逐线程点查的网络往返放大，再按体积过滤。
    """
    cutoff = _idle_cutoff_id(MIN_IDLE_DAYS)
    cur.execute(
        "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id")
    candidates = [row[0] for row in cur.fetchall()
                  if str(row[1] or "") < cutoff]
    targets = []
    sql_tpl = ("SELECT thread_id, COALESCE(SUM(LENGTH(`blob`)), 0)/{mb_div} AS mb"
               " FROM checkpoint_blobs WHERE thread_id IN ({ph})"
               " GROUP BY thread_id HAVING mb > %s")
    for chunk in _chunks(candidates, 1000):
        sql = sql_tpl.format(mb_div=MB, ph=", ".join(["%s"] * len(chunk)))
        cur.execute(sql, tuple(chunk) + (MIN_MB,))
        for thread_id, mb in cur.fetchall():
            targets.append({"thread_id": thread_id, "mb": float(mb)})
    targets.sort(key=lambda item: -item["mb"])
    return targets


# ---------------- keep-latest 裁剪 ----------------

def load_keepers(cur, thread_id: str) -> List[dict]:
    """每个命名空间保留最新一份快照（checkpoint_id 为时间序 UUID，字典序即时间序）。"""
    cur.execute(
        """SELECT checkpoint_ns_hash, MAX(checkpoint_id) AS keep_id, COUNT(*) AS total
           FROM checkpoints WHERE thread_id = %s GROUP BY checkpoint_ns_hash""",
        (thread_id,),
    )
    return [{"ns_hash": r[0], "keep_id": r[1], "total": r[2]} for r in cur.fetchall()]


def keeper_activity(cur, thread_id: str, keepers: List[dict]) -> Optional[datetime]:
    """取保留快照里最新的 ts，用于删除前复核该会话是否仍然闲置。"""
    stamps = []
    for k in keepers:
        cur.execute(
            """SELECT JSON_UNQUOTE(JSON_EXTRACT(`checkpoint`, '$.ts')) FROM checkpoints
               WHERE thread_id = %s AND checkpoint_ns_hash = %s AND checkpoint_id = %s""",
            (thread_id, k["ns_hash"], k["keep_id"]),
        )
        row = cur.fetchone()
        last = _parse_ts(row[0]) if row else None
        if last:
            stamps.append(last)
    return max(stamps) if stamps else None


def referenced_versions(cur, thread_id: str, keepers: List[dict]) -> Dict[tuple, Set[str]]:
    """保留快照的 channel_versions 引用集。按引用取而非最大版本（见模块 docstring）。"""
    refs = {}  # type: Dict[tuple, Set[str]]
    for k in keepers:
        cur.execute(
            """SELECT `checkpoint` FROM checkpoints
               WHERE thread_id = %s AND checkpoint_ns_hash = %s AND checkpoint_id = %s""",
            (thread_id, k["ns_hash"], k["keep_id"]),
        )
        raw = cur.fetchone()[0]
        ckpt = raw if isinstance(raw, dict) else json.loads(raw)
        for channel, version in (ckpt.get("channel_versions") or {}).items():
            refs.setdefault((k["ns_hash"], channel), set()).add(str(version))
    return refs


def plan_blobs(cur, thread_id: str, refs: Dict[tuple, Set[str]]) -> Tuple[dict, int, int, int]:
    """blob 行按"是否被保留快照引用"切分。返回 (待删分组, 待删行数, 待删字节, 保留行数)。

    待删分组值为 (version, 字节数) 列表，prune_blobs 据此按累计字节分批。
    """
    cur.execute(
        """SELECT checkpoint_ns_hash, channel, version, COALESCE(LENGTH(`blob`), 0)
           FROM checkpoint_blobs WHERE thread_id = %s""",
        (thread_id,),
    )
    plan = {}  # type: Dict[tuple, list]
    del_rows = del_bytes = kept_rows = 0
    for ns_hash, channel, version, nbytes in cur.fetchall():
        if str(version) in refs.get((ns_hash, channel), ()):
            kept_rows += 1
            continue
        plan.setdefault((ns_hash, channel), []).append((str(version), int(nbytes)))
        del_rows += 1
        del_bytes += int(nbytes)
    return plan, del_rows, del_bytes, kept_rows


def plan_old_snapshots(cur, thread_id: str, keepers: List[dict]) -> Dict[bytes, list]:
    """按命名空间收集待删的旧 checkpoint_id；writes 用同一批 id 一起清。"""
    out = {}  # type: Dict[bytes, list]
    for k in keepers:
        cur.execute(
            """SELECT checkpoint_id FROM checkpoints
               WHERE thread_id = %s AND checkpoint_ns_hash = %s AND checkpoint_id <> %s""",
            (thread_id, k["ns_hash"], k["keep_id"]),
        )
        out[k["ns_hash"]] = [r[0] for r in cur.fetchall()]
    return out


def keeper_old_format_ids(cur, thread_id: str, keepers: List[dict]) -> List[str]:
    """keeper 中 checkpoint 格式 v<4 的旧格式快照 id。

    v<4 时 saver 续聊会读父快照的 pending sends 做迁移：aio_base.py 的
    aget_tuple 在 `checkpoint.v < 4 and parent_checkpoint_id` 时查父快照
    的 writes（_select_pending_sends_sql），父快照被裁后查空、迁移静默
    落空（仅影响中断续跑，不崩）。新写入均为 v=4，此检查只为对历史老数据
    给出人工复核提示。
    """
    old_ids = []
    for k in keepers:
        cur.execute(
            """SELECT CAST(JSON_UNQUOTE(JSON_EXTRACT(`checkpoint`, '$.v')) AS UNSIGNED)
               FROM checkpoints
               WHERE thread_id = %s AND checkpoint_ns_hash = %s AND checkpoint_id = %s""",
            (thread_id, k["ns_hash"], k["keep_id"]),
        )
        row = cur.fetchone()
        if row and row[0] and row[0] < 4:
            old_ids.append(k["keep_id"])
    return old_ids


def prune_blobs(conn, thread_id: str, plan: Dict[tuple, list]) -> int:
    sql_tpl = ("DELETE FROM checkpoint_blobs WHERE thread_id = %s AND checkpoint_ns_hash = %s "
               "AND channel = %s AND version IN ({ph})")
    deleted = 0
    for (ns_hash, channel), versions in plan.items():
        for chunk in _chunks(versions, BATCH_ROWS):
            sql = sql_tpl.format(ph=", ".join(["%s"] * len(chunk)))
            deleted += run_with_retry(conn, sql, (thread_id, ns_hash, channel) + tuple(chunk))
            time.sleep(SLEEP_MS / 1000)
    return deleted


def prune_snapshots(conn, thread_id: str, old_ids: Dict[bytes, list]) -> Tuple[int, int]:
    """先清 writes 再清 checkpoints（同批 checkpoint_id）。"""
    ck_sql = ("DELETE FROM checkpoints WHERE thread_id = %s AND checkpoint_ns_hash = %s "
              "AND checkpoint_id IN ({ph})")
    wr_sql = ("DELETE FROM checkpoint_writes WHERE thread_id = %s AND checkpoint_ns_hash = %s "
              "AND checkpoint_id IN ({ph})")
    ck_del = wr_del = 0
    for ns_hash, ids in old_ids.items():
        for chunk in _chunks(ids, BATCH_ROWS):
            params = (thread_id, ns_hash) + tuple(chunk)
            ph = ", ".join(["%s"] * len(chunk))
            wr_del += run_with_retry(conn, wr_sql.format(ph=ph), params)
            time.sleep(SLEEP_MS / 1000)
            ck_del += run_with_retry(conn, ck_sql.format(ph=ph), params)
            time.sleep(SLEEP_MS / 1000)
    return ck_del, wr_del


def collect_orphan_writes(cur, thread_id: str) -> Dict[bytes, list]:
    """checkpoint_id 已不在 checkpoints 表的 writes 行（异常中断残留）。

    plan_old_snapshots 只覆盖 checkpoints 表出现过的 id，这类孤儿行
    常规路径清不到，故用 LEFT JOIN 反查。per-thread 走 thread_id 索引。

    安全前提（必须满足，否则会误删在飞数据）：saver 的 put_writes 先于
    checkpoint 行落库，所以一个正在执行的 superstep，其 writes 在库里
    看起来就是"孤儿"。本函数安全完全依赖"闲置 MIN_IDLE_DAYS 天"这道闸——
    不得把 MIN_IDLE_DAYS 调到接近 0，也不得把本函数复用到活跃会话上。
    """
    cur.execute(
        """SELECT DISTINCT cw.checkpoint_ns_hash, cw.checkpoint_id
           FROM checkpoint_writes cw
           LEFT JOIN checkpoints c
             ON c.thread_id = cw.thread_id
            AND c.checkpoint_ns_hash = cw.checkpoint_ns_hash
            AND c.checkpoint_id = cw.checkpoint_id
           WHERE cw.thread_id = %s AND c.thread_id IS NULL""",
        (thread_id,),
    )
    out = {}  # type: Dict[bytes, list]
    for ns_hash, ck_id in cur.fetchall():
        out.setdefault(ns_hash, []).append(ck_id)
    return out


def prune_orphan_writes(conn, thread_id: str, orphans: Dict[bytes, list]) -> int:
    wr_sql = ("DELETE FROM checkpoint_writes WHERE thread_id = %s AND checkpoint_ns_hash = %s "
              "AND checkpoint_id IN ({ph})")
    deleted = 0
    for ns_hash, ids in orphans.items():
        for chunk in _chunks(ids, BATCH_ROWS):
            ph = ", ".join(["%s"] * len(chunk))
            deleted += run_with_retry(conn, wr_sql.format(ph=ph),
                                      (thread_id, ns_hash) + tuple(chunk))
            time.sleep(SLEEP_MS / 1000)
    return deleted


def _missing_messages(cur, thread_id: str, keeper: dict) -> Optional[str]:
    """messages 通道是续聊的关键，必须仍可解析。"""
    cur.execute(
        """SELECT JSON_UNQUOTE(JSON_EXTRACT(`checkpoint`, '$.channel_versions.messages'))
           FROM checkpoints
           WHERE thread_id = %s AND checkpoint_ns_hash = %s AND checkpoint_id = %s""",
        (thread_id, keeper["ns_hash"], keeper["keep_id"]),
    )
    row = cur.fetchone()
    version = str(row[0]) if row and row[0] is not None else None
    if version is None or version == "None":
        return None  # 该命名空间没有 messages 通道（子图/工具图），无需校验
    cur.execute(
        """SELECT COUNT(*) FROM checkpoint_blobs
           WHERE thread_id = %s AND checkpoint_ns_hash = %s AND channel = 'messages' AND version = %s""",
        (thread_id, keeper["ns_hash"], version),
    )
    if cur.fetchone()[0] == 0:
        ns = keeper["ns_hash"].hex()[:8] if isinstance(keeper["ns_hash"], bytes) else "?"
        return "命名空间 %s 的 messages 快照缺失@%s" % (ns, version)
    return None


def verify_kept(cur, thread_id: str, keepers: List[dict], expected_blobs: int) -> List[str]:
    """对账式自检。

    只应删掉"未被引用"的行，所以保留行必须精确等于计划数——不多不少。
    注意不能用"每个引用都要有 blob 行"来判断：__start__、branch:to:* 这类控制通道
    本就没有任何存储值，按引用逐条查会大量误报。
    """
    problems = []
    cur.execute("SELECT COUNT(*) FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,))
    left = cur.fetchone()[0]
    if left != expected_blobs:
        problems.append("blob 残留 %d 行 != 计划保留 %d 行" % (left, expected_blobs))

    cur.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = %s", (thread_id,))
    ck_left = cur.fetchone()[0]
    if ck_left != len(keepers):
        problems.append("checkpoint 残留 %d != 命名空间数 %d" % (ck_left, len(keepers)))

    for k in keepers:
        missing = _missing_messages(cur, thread_id, k)
        if missing:
            problems.append(missing)
    return problems


def prune_one(conn, target: dict, apply_delete: bool) -> dict:
    """对单个会话执行 keep-latest 裁剪。"""
    thread_id = target["thread_id"]
    result = {"thread_id": thread_id, "reclaim_mb": 0.0}
    # 规划阶段显式开事务：五条规划读必须共享同一 REPEATABLE READ 快照，
    # 否则规划中途落库的新 checkpoint 的 blob 会被误判为"未引用"而删除。
    # autocommit=True 下用 begin() 圈定，finally commit 保证 skip/dry-run/
    # 异常等所有退出路径都收口，事务绝不遗留。
    conn.begin()
    try:
        with conn.cursor() as cur:
            keepers = load_keepers(cur, thread_id)
            if not keepers:
                result["status"] = "skip(无checkpoint)"
                return result
            last = keeper_activity(cur, thread_id, keepers)
            if _idle_too_short(last):
                reason = ("已重新活跃 %s" % last.strftime("%m-%d %H:%M")) if last else "无法判定活动时间"
                result["status"] = "skip(%s)" % reason
                return result
            refs = referenced_versions(cur, thread_id, keepers)
            blob_plan, blob_rows, blob_bytes, kept_rows = plan_blobs(cur, thread_id, refs)
            old_ids = plan_old_snapshots(cur, thread_id, keepers)
            orphans = collect_orphan_writes(cur, thread_id)
            ck_rows = sum(len(v) for v in old_ids.values())
            orphan_rows = sum(len(v) for v in orphans.values())
            old_keepers = keeper_old_format_ids(cur, thread_id, keepers)

        print("  [裁剪] %s… %.0fMB, 闲置至 %s | 待删 blob %d 行/%.1fMB, 旧快照 %d, 孤儿 writes %d, 保留 %d 行" % (
            thread_id[:13], target["mb"], last.strftime("%m-%d"), blob_rows,
            blob_bytes / MB, ck_rows, orphan_rows, kept_rows))
        if old_keepers:
            print("    !! 警告：keeper 含 %d 个 v<4 旧格式快照，父快照裁剪后 pending-sends "
                  "将不可迁移（仅影响中断续跑），请人工确认" % len(old_keepers))
        result["reclaim_mb"] = round(blob_bytes / MB, 1)
        if not apply_delete:
            result["status"] = "dry-run"
            return result
    finally:
        conn.commit()

    ck_del, wr_del = prune_snapshots(conn, thread_id, old_ids)
    orphan_del = prune_orphan_writes(conn, thread_id, orphans)
    deleted = prune_blobs(conn, thread_id, blob_plan)
    with conn.cursor() as cur:
        problems = verify_kept(cur, thread_id, keepers, kept_rows)
    result["status"] = "ok(blob %d/ckpt %d/writes %d/孤儿 %d) 自检 %s" % (
        deleted, ck_del, wr_del, orphan_del, "PASS" if not problems else "FAIL")
    for problem in problems[:5]:
        print("    !! 自检异常：%s" % problem)
    return result


# ---------------- 主流程 ----------------

def main() -> int:
    apply_delete = parse_mode()
    mode = "EXECUTE(真删)" if apply_delete else "DRY-RUN(只统计)"
    print("目标库 %s:%s/%s  模式 %s  策略: 体积>%sMB 且闲置>%s天 → keep-latest 裁剪" % (
        DB_HOST, DB_PORT, DB_NAME, mode, MIN_MB, MIN_IDLE_DAYS))
    conn = connect(DB_NAME)
    results = []  # type: List[dict]
    try:
        with conn.cursor() as cur:
            prune_targets = discover_prune_targets(cur)
        # autocommit=True：发现阶段各 SELECT 已自提交，无遗留事务
        print("发现裁剪目标 %d 个" % len(prune_targets))
        for target in prune_targets:
            try:
                results.append(prune_one(conn, target, apply_delete))
            except Exception as err:  # 单会话失败不影响其余（prune_one 的
                # finally 已收口其规划事务，此处无遗留事务需要 rollback）
                print("    !! 裁剪失败 %s: %s: %s" % (
                    target["thread_id"], type(err).__name__, err))
                results.append({"thread_id": target["thread_id"], "status": "error:%s" % err,
                                "reclaim_mb": 0.0})
    finally:
        conn.close()

    total_mb = sum(r.get("reclaim_mb", 0.0) for r in results)
    print("\n===== 汇总 =====")
    for r in results:
        print("  %-38s %-46s 回收 %sMB" % (r["thread_id"][:36], r.get("status", ""),
                                          r.get("reclaim_mb", 0.0)))
    print("合计 %.1fMB" % total_mb)
    if not apply_delete:
        print("（dry-run 未做任何修改；确认无误后把参数换成 execute）")
    else:
        print("提示：InnoDB 不自动归还磁盘空间，页仅作 free 复用；确需缩表低峰执行 OPTIMIZE TABLE。")
    failed = [r for r in results
              if str(r.get("status", "")).startswith("error") or "FAIL" in str(r.get("status", ""))]
    if failed:
        print("!! %d 个会话失败或自检未通过，请人工复核" % len(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
