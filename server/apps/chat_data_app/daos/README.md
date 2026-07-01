# ChatData DAOs

`daos` 是 ChatData 的数据库访问层，负责 SQL/ORM 查询、持久化读写和数据库模型映射。

约束：
- DAO 不处理 HTTP 入参、权限和响应包装。
- DAO 不编排业务流程，不拼前端展示文案。
- DAO 不主动 `commit()` / `rollback()`，事务由 service 编排，提交回滚机制由 `database.get_session()` 承担。
- 新增数据访问能力优先放入对应 `*_dao.py`，避免在 routers 中直接写查询。
