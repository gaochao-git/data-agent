CREATE DATABASE IF NOT EXISTS juicefs_meta;
USE juicefs_meta;
CREATE USER IF NOT EXISTS 'psoss_chatdata_juicefs_rw'@'%' IDENTIFIED WITH mysql_native_password BY 'xxxxxxx';
GRANT select,insert,update,DELETE ON juicefs_meta.* TO 'psoss_chatdata_juicefs_rw'@'%';

CREATE TABLE IF NOT EXISTS `jfs_acl` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键',
  `owner` int DEFAULT NULL COMMENT '所有者 UID',
  `group` int DEFAULT NULL COMMENT '所属组 GID',
  `mask` int DEFAULT NULL COMMENT 'ACL mask 权限',
  `other` int DEFAULT NULL COMMENT '其他用户权限',
  `named_users` blob COMMENT '命名用户 ACL 数据（序列化）',
  `named_groups` blob COMMENT '命名组 ACL 数据（序列化）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB COMMENT='ACL 访问控制项';
CREATE TABLE IF NOT EXISTS `jfs_change_log` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `entry` text NOT NULL COMMENT '变更日志内容',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB COMMENT='元数据变更日志';
CREATE TABLE IF NOT EXISTS `jfs_chunk` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `inode` bigint NOT NULL COMMENT '所属 inode',
  `indx` int NOT NULL COMMENT '块索引',
  `slices` blob NOT NULL COMMENT '切片列表（序列化）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQE_jfs_chunk_chunk` (`inode`,`indx`)
) ENGINE=InnoDB COMMENT='文件数据块元数据';
CREATE TABLE IF NOT EXISTS `jfs_chunk_ref` (
  `chunkid` bigint NOT NULL COMMENT '数据块 ID',
  `size` int NOT NULL COMMENT '数据块大小（字节）',
  `refs` int NOT NULL COMMENT '引用计数',
  PRIMARY KEY (`chunkid`),
  KEY `IDX_jfs_chunk_ref_refs` (`refs`)
) ENGINE=InnoDB COMMENT='数据块引用计数';
CREATE TABLE IF NOT EXISTS `jfs_counter` (
  `name` varchar(255) NOT NULL COMMENT '计数器名称',
  `value` bigint NOT NULL COMMENT '计数器值',
  PRIMARY KEY (`name`)
) ENGINE=InnoDB COMMENT='文件系统计数器';
CREATE TABLE IF NOT EXISTS `jfs_delegation_token` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键',
  `token` blob COMMENT 'NFS 委托令牌数据',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB COMMENT='NFS 委托令牌';
CREATE TABLE IF NOT EXISTS `jfs_delfile` (
  `inode` bigint NOT NULL COMMENT '被删除文件的 inode',
  `length` bigint NOT NULL COMMENT '文件长度（字节）',
  `expire` bigint NOT NULL COMMENT '清理过期时间',
  PRIMARY KEY (`inode`)
) ENGINE=InnoDB COMMENT='待清理的已删除文件';
CREATE TABLE IF NOT EXISTS `jfs_delslices` (
  `chunkid` bigint NOT NULL COMMENT '数据块 ID',
  `deleted` bigint NOT NULL COMMENT '删除时间',
  `slices` blob NOT NULL COMMENT '待删除切片（序列化）',
  PRIMARY KEY (`chunkid`)
) ENGINE=InnoDB COMMENT='待清理的已删除切片';
CREATE TABLE IF NOT EXISTS `jfs_detached_node` (
  `inode` bigint NOT NULL COMMENT '分离节点的 inode',
  `added` bigint NOT NULL COMMENT '分离时间',
  PRIMARY KEY (`inode`)
) ENGINE=InnoDB COMMENT='已分离的节点';
CREATE TABLE IF NOT EXISTS `jfs_dir_quota` (
  `inode` bigint NOT NULL COMMENT '目录 inode',
  `max_space` bigint NOT NULL COMMENT '空间配额上限（字节）',
  `max_inodes` bigint NOT NULL COMMENT 'inode 配额上限',
  `used_space` bigint NOT NULL COMMENT '已用空间（字节）',
  `used_inodes` bigint NOT NULL COMMENT '已用 inode 数',
  PRIMARY KEY (`inode`)
) ENGINE=InnoDB COMMENT='目录配额';
CREATE TABLE IF NOT EXISTS `jfs_dir_stats` (
  `inode` bigint NOT NULL COMMENT '目录 inode',
  `data_length` bigint NOT NULL COMMENT '目录数据长度（字节）',
  `used_space` bigint NOT NULL COMMENT '已用空间（字节）',
  `used_inodes` bigint NOT NULL COMMENT '已用 inode 数',
  PRIMARY KEY (`inode`)
) ENGINE=InnoDB COMMENT='目录统计信息';
CREATE TABLE IF NOT EXISTS `jfs_edge` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `parent` bigint NOT NULL COMMENT '父目录 inode',
  `name` varbinary(255) NOT NULL COMMENT '目录项名称',
  `inode` bigint NOT NULL COMMENT '目标 inode',
  `type` int NOT NULL COMMENT '目录项类型',
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQE_jfs_edge_edge` (`parent`,`name`),
  KEY `IDX_jfs_edge_inode` (`inode`)
) ENGINE=InnoDB COMMENT='目录项（目录树边）';
CREATE TABLE IF NOT EXISTS `jfs_flock` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `inode` bigint NOT NULL COMMENT '文件 inode',
  `sid` bigint NOT NULL COMMENT '会话 ID',
  `owner` bigint NOT NULL COMMENT '锁持有者',
  `ltype` int NOT NULL COMMENT '锁类型',
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQE_jfs_flock_flock` (`inode`,`sid`,`owner`)
) ENGINE=InnoDB COMMENT='文件锁';
CREATE TABLE IF NOT EXISTS `jfs_node` (
  `inode` bigint NOT NULL COMMENT 'inode 编号',
  `type` int NOT NULL COMMENT '节点类型',
  `flags` int NOT NULL COMMENT '节点标志',
  `mode` int NOT NULL COMMENT '权限模式',
  `uid` int NOT NULL COMMENT '所有者 UID',
  `gid` int NOT NULL COMMENT '所属组 GID',
  `atime` bigint NOT NULL COMMENT '访问时间（秒）',
  `mtime` bigint NOT NULL COMMENT '修改时间（秒）',
  `ctime` bigint NOT NULL COMMENT '状态变更时间（秒）',
  `atimensec` int NOT NULL DEFAULT '0' COMMENT '访问时间纳秒部分',
  `mtimensec` int NOT NULL DEFAULT '0' COMMENT '修改时间纳秒部分',
  `ctimensec` int NOT NULL DEFAULT '0' COMMENT '状态变更时间纳秒部分',
  `nlink` int NOT NULL COMMENT '硬链接数',
  `length` bigint NOT NULL COMMENT '文件长度（字节）',
  `rdev` int DEFAULT NULL COMMENT '设备号',
  `parent` bigint DEFAULT NULL COMMENT '父目录 inode',
  `access_acl_id` int DEFAULT NULL COMMENT '访问 ACL ID',
  `default_acl_id` int DEFAULT NULL COMMENT '默认 ACL ID',
  `tier_id` int DEFAULT NULL COMMENT '存储分层 ID',
  PRIMARY KEY (`inode`)
) ENGINE=InnoDB COMMENT='inode 节点元数据';
CREATE TABLE IF NOT EXISTS `jfs_plock` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `inode` bigint NOT NULL COMMENT '文件 inode',
  `sid` bigint NOT NULL COMMENT '会话 ID',
  `owner` bigint NOT NULL COMMENT '锁持有者',
  `records` blob NOT NULL COMMENT 'POSIX 锁记录（序列化）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQE_jfs_plock_plock` (`inode`,`sid`,`owner`)
) ENGINE=InnoDB COMMENT='POSIX 记录锁';
CREATE TABLE IF NOT EXISTS `jfs_session2` (
  `sid` bigint NOT NULL COMMENT '会话 ID',
  `expire` bigint NOT NULL COMMENT '会话过期时间',
  `info` blob COMMENT '会话信息（序列化）',
  PRIMARY KEY (`sid`)
) ENGINE=InnoDB COMMENT='客户端会话';
CREATE TABLE IF NOT EXISTS `jfs_setting` (
  `name` varchar(255) NOT NULL COMMENT '配置项名称',
  `value` text NOT NULL COMMENT '配置项值',
  PRIMARY KEY (`name`)
) ENGINE=InnoDB COMMENT='文件系统配置项';
CREATE TABLE IF NOT EXISTS `jfs_sustained` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `sid` bigint NOT NULL COMMENT '会话 ID',
  `inode` bigint NOT NULL COMMENT '文件 inode',
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQE_jfs_sustained_sustained` (`sid`,`inode`)
) ENGINE=InnoDB COMMENT='持久会话文件';
CREATE TABLE IF NOT EXISTS `jfs_symlink` (
  `inode` bigint NOT NULL COMMENT '符号链接 inode',
  `target` varbinary(4096) NOT NULL COMMENT '链接目标路径',
  PRIMARY KEY (`inode`)
) ENGINE=InnoDB COMMENT='符号链接目标';
CREATE TABLE IF NOT EXISTS `jfs_user_group_quota` (
  `qtype` int NOT NULL COMMENT '配额类型：用户/组',
  `qkey` bigint NOT NULL COMMENT '用户或组 ID',
  `max_space` bigint NOT NULL COMMENT '空间配额上限（字节）',
  `max_inodes` bigint NOT NULL COMMENT 'inode 配额上限',
  `used_space` bigint NOT NULL COMMENT '已用空间（字节）',
  `used_inodes` bigint NOT NULL COMMENT '已用 inode 数',
  PRIMARY KEY (`qtype`,`qkey`)
) ENGINE=InnoDB COMMENT='用户/组配额';
CREATE TABLE IF NOT EXISTS `jfs_xattr` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `inode` bigint NOT NULL COMMENT '文件 inode',
  `name` varchar(255) NOT NULL COMMENT '扩展属性名',
  `value` blob NOT NULL COMMENT '扩展属性值',
  PRIMARY KEY (`id`),
  UNIQUE KEY `UQE_jfs_xattr_name` (`inode`,`name`)
) ENGINE=InnoDB COMMENT='扩展属性';
