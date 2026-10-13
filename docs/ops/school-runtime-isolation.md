# 学校运行隔离与发布边界

历史验收失败记录见 [上一轮交付说明](school-final-delivery-20261011.md)。当前 `/server`—`/client` 重构按 [执行记录](../superpowers/plans/2026-10-11-unified-frontend-backend-architecture.md) 验证；本轮仅授权隔离候选，不包含生产切换。旧 bootstrap 包含已取消的 300 人流程，禁止整段执行。

## 运行拓扑

学校配置使用同一 Django 应用和物理 PostgreSQL，单独提供核心、学生读取、教师读取与后台连接池。基础编排位于 `deploy/docker-compose.yml`，后端镜像位于 `server/Dockerfile`、前端镜像位于 `client/Dockerfile`；学校使用 `deploy/docker-compose.school.yml` 和 `deploy/Dockerfile.school`，腾讯云部署不自动加入学校 overlay。

- 基线工具链：Python 3.13、Django 5.2.16、PostgreSQL 17、Node 24、Vue 3.5、Vite 8。Compose 需支持 `!override`/`!reset`。
- 外层 TLS Nginx 转发到内层 loopback 8080，覆盖 `X-Forwarded-Proto`，保留 Host、Cookie 和业务 URL；应用和数据库不开放 host 端口。
- `SCHOOL_RUNTIME_ISOLATION_ENABLED=1` 是显式激活条件。角色、排期、反馈和授权仍由 Django 判断。
- 学校基础 overlay 默认核心 12、学生读 2、教师读 1 worker；学校测试 overlay 明确固定 16/2/1，学生读 2 CPU，准入容量仍为 8。`SCHOOL_TEST_WEB_WORKERS` 仅用于隔离测试的显式单变量对照，默认 16；该配置尚未通过 180 人容量验收。
- Nginx 对 `contracts/frontend-routes.json` 登记的 68 条页面路径提供前端 HTML，未知路径不做 SPA 回退；API 依据显式白名单分配读取池。作答写入、登录和发布使用核心池，既有导出 job 创建继续使用教师池。
- PgBouncer 别名 `kt_core`、`kt_student`、`kt_teacher`、`kt_background` 映射到同一个 `POSTGRES_DB`，Django 通过 `POSTGRES_POOL_ALIAS` 选择真实池。核心 16+2、学生 6+1、教师 4+1、后台 4+1。
- 研究、比较、发布、导出使用独立 worker。scheduler 直接连接 PostgreSQL，保留 session advisory lock，不能改成 transaction pool。
- 共享结果及物化功能保留原 Course/User allowlist；测试通配符不能复制到生产。`SAVE_AND_LEAVE_ENABLED`、`ATOMIC_SUBMISSION_ENABLED`、连接建立重试和详细诊断默认关闭。

## 隔离验收

使用 `deploy/docker-compose.school-test.yml`、`deploy/docker-compose.school-test-external-db.yml`，project 固定为 `kt-school-e2e`，独立 `kt_e2e` 数据库和专用角色。测试容器使用独立网络；现有 PostgreSQL 以显式 `school-test-postgres` 别名加入该网络。不能把测试服务加入生产网络或将 fixture 写入生产数据库。

激活前核对 Compose 解析、镜像绑定、数据库 owner、连接预算、主机可用内存及峰值余量。至少保留 2 GiB 主机可用内存，按实测峰值加 30% 检查总工作集。只记录共享 PostgreSQL 现有限制；不关闭 fsync、同步提交，不调整 NTP 或生产卷，不用延长超时掩盖失败。

Redis 会话仅用于显式隔离候选：在上述学校测试 overlay、`kt-school-e2e` project 和 `kt_e2e` 数据库下，同时设置 `SCHOOL_REDIS_SESSION_ENABLED=1`，并给 Compose 增加 `--profile school-redis-session`。核心、学生读、教师读使用同一 `redis-session:6379/0` 和键前缀；服务固定 Redis 7.4、AOF `appendfsync always`、`noeviction`、256 MiB 数据上限、512 MiB 容器上限、1 CPU 和独立持久卷。默认数据库会话、Cookie 名称/属性和 CSRF 契约保持原样。开关开启而服务不可用时返回明确 503，不回退数据库；响应阶段保存失败可能发生在业务已提交之后，必须先按原 token 确认状态。

学校测试 overlay 将 `SESSION_REDIS_TIMEOUT_SECONDS` 默认设为 5 秒，可显式覆盖。2026-10-13 的 1 秒候选配置在响应阶段记录到约 1.002 秒的会话保存超时及 503，失败轮次保留；5 秒配置使用新数据重新验收。该值只是 Redis 访问上限，35 秒操作预算、提交切题 P95≤3 秒/P99≤5 秒和草稿保存 P95≤1 秒均不放宽。AOF always、noeviction、资源限制及故障时的明确 503 保持不变。

启用及回滚均需先排空候选请求，再清除浏览器会话 Cookie 并重新登录；两种存储之间不迁移会话、不复用旧 Cookie 所指的认证状态。回滚设置 `SCHOOL_REDIS_SESSION_ENABLED=0`，移除 Redis profile，重建候选应用；保留会话持久卷和业务数据库。禁止将该候选会话开关复制到生产配置。

本地验证使用 Windows Cygwin Redis 7.4.3：可执行文件 `C:\Users\31606\Downloads\ATOP-tooling\Redis-7.4.3-Windows-x64-cygwin\Redis-7.4.3-Windows-x64-cygwin\redis-server.exe`，数据目录 `C:\Users\31606\Downloads\ATOP-tooling\Redis-7.4.3-session-test-data`，监听 `127.0.0.1:56379`；测试键与浏览器验证键使用独立前缀。已检查 AOF always、noeviction、TTL、会话故障与正常重启持久化；这些本地验证不等同 Linux Docker 资源一致性或异常断电验证。

保留的可复用工具：

- `server/scripts/ops/school_architecture_runtime.py`：本轮隔离 fixture、发生器启动、保护采样、后台排空与对账入口。支持 3/10 人全卷和独立 formal-1/formal-2；正式轮固定 180 人、20 题、58 次访问、18 秒绝对节奏、1750/1800 秒预算。
- `server/scripts/load/school_architecture_load.py` 与 `capture_protocol.py`：实际请求尝试、原始人数分母、操作耗时、迟到量、失败及未发生操作。
- `verify_school_protocol_load`：全部流程与已确认事实分别核对；真实浏览器由 `client/e2e/school-navigation-probe.mjs` 独立见证。
- `server/scripts/load/observe_school_runtime.py`、`server/scripts/ops/school_diagnostic_runtime.py`：资源、绑定、隔离范围及保护。
- `server/scripts/ops/school_cached_build.py`、`deploy/Dockerfile.school-cached`：已核实基础镜像的离线构建，不修改依赖版本。
- 其他通用 bootstrap、混合负载、探针与发布校验工具继续保留；文件存在不代表授权执行，也不表示对应验收已通过。

每次验收使用新的任务和独占证据文件，绑定 source/frontend/config/镜像。详细诊断与性能验收分开。阶段结束后对已消费任务排空后台，仅停止身份、project 标签和镜像均匹配的自有测试角色。保留原始远端证据与数据库，不执行生产 project 的 down 或卷删除。

## 发布与恢复

只有新协议功能、完整流程、授权、持久化、真实浏览器、资源及等待门槛全部通过，才能提出生产发布。当前没有此结论。后续生产发布仍需单独授权和有效的同源验收；`school_production_promote.sh` 会检查已接受报告和不可变镜像，不能用手工修改报告绕过门禁。

发布需保留数据库备份、原源码、原镜像、真实 Compose/卷/网络记录，保留学校已有 Dockerfile 修改。回滚应用时保留数据库增量迁移及新写入事实，不自动恢复旧 dump 覆盖新数据。

关闭新能力开关后，后端仍需支持已发送令牌的原回执查询/重放。未知导航或提交必须先以原 token 核验；不删除本地恢复队列、不修改材料重试、不强制正式提交普通草稿。

原始日志、fixture 凭据、Cookie、答案与 SQL 参数只保存在受限位置；公开报告只保留脱敏事实和哈希。当前最终证据及限制由交付说明统一维护，不继续保留旧阶段操作流水账。

## 独立产物与应用回滚

前端通过 `client/package-lock.json` 独立安装构建，API 类型只读取 `contracts/openapi.yaml`。后端以当前 checkout 的 `server/.venv` 安装 `server/requirements.txt`，单独生成并核查 OpenAPI。学校离线构建分别接收源码归档和前端 `dist/spa-assets` 归档；在部署前核对两份 SHA256、镜像 ID、有效 Compose 配置及题目运行时开关。

候选重建只替换已确认 project、角色、镜像及 `kt_e2e` 专用凭据的容器。冻结配置写入新的私有 run 目录，旧源码与证据不覆盖。生产 `kt_plantform`、数据库 `kt_platform` 和 8080 入口保持独立。

`frontend_assets` 持久卷追加保存旧哈希 JS/CSS 与 `capture-shell-<build>.html`；固定 Worker URL 继续使用原 scope，并由前端产物提供。版本切换或回滚不得删除该卷，也不得清空 IndexedDB、未确认意图或重建答案。恢复已验证的前后端镜像与有效配置后，先以原 token 查询/重放不确定操作，核对原材料与回执，再恢复当前题目；不能把未确认保存推定为成功。

数据库保留历史迁移和新事实。应用回滚版本必须能够理解当前 token、回执及本地恢复材料；不存在已验证兼容产物时，保持候选停止并保留事实，不倒退数据库或用旧 dump 覆盖新数据。
