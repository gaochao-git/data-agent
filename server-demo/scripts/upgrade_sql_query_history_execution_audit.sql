-- sql_query_history 从“推荐池/频次统计”调整为“每次执行一条历史”。
ALTER TABLE `sql_query_history`
  ADD COLUMN `execution_time_ms` INT NULL COMMENT '单次执行耗时(毫秒)' AFTER `last_used`,
  ADD COLUMN `result_row_count` INT NULL COMMENT '返回结果行数' AFTER `execution_time_ms`,
  ADD COLUMN `success_flag` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '执行是否成功: 1-成功, 0-失败' AFTER `result_row_count`,
  ADD COLUMN `error_message` TEXT NULL COMMENT '执行失败时的错误信息' AFTER `success_flag`,
  DROP COLUMN `frequency`,
  DROP COLUMN `avg_execution_time_ms`,
  DROP COLUMN `card_icon`,
  DROP COLUMN `card_color`,
  DROP INDEX `idx_datasource_frequency`;
