# 教师操作、实验删除与 180 人容量修复方案——新工作树接续版

## 2026-10-18 实施授权与最新版决策（优先于下文旧版细节）

用户已明确要求实施完整计划。本树基线仍为 b3636d9c12436588fc64912a96883e6140ad18f9；不重建工作树、不重复上一轮学生修复、不自动部署生产。2026-10-18用户追加授权：暂停所有压测，完整审查修复后提交至本地main。

- P1：真实未 mock prepared 发布的 PostgreSQL COMMIT 失败测试先行；补全部引用（含 ResearchSessionQuality）；导出文件锁先于课程锁且删除非阻塞，数据库 NOWAIT/局部 lock_timeout；RUNNING 阻断，预览/指纹/实际删除范围统一。
- P2：完整 course/auth/query 意图代际在查缓存前建立，状态与结果缓存分离，部分 ready（含混合失败）最多重取一次，保留三次状态检查与8项LRU。
- P3：status_checked_at 为真正采样时间；任务年龄含排队/执行/重试，禁止显示ETA。
- P4：TIMING_PATHS 与 CONNECT_RETRY_PATHS 分离；auth/教师观测不扩张连接重试；保留合法请求ID与脱敏日志。
- P5：session_ref 按授权PK/北京时间日期直接查找，批量实验校验；建议搜索在DataScope构造前投影；overview核心求值≤8，status核心聚合仍2。
- P6（替换旧时间cutoff方案）：新增ResearchPublicationClock与activated_revision/superseded_revision；写侧同锁递增并原子发布，读侧仅短事务取cutoff，COUNT/分页/EXISTS在锁外。v2游标绑定course/scope/序号；拒绝v1并要求回第一页。当前成功版本初始化act=1、历史成功act=0/sup=1，clock=1，保留所有原内容/时间。并发、回滚、相等时间和时钟回拨门禁必须通过。
- P7：教师advanced/search显式opt-in，两次上限、Retry-After、200—400ms抖动、30秒总预算、可取消；status不重试；目录手动刷新绕过结果缓存。
- P9（替换旧人工恢复方案）：ResearchDeletionOperation持久登记→原URL同步删除；成功回执与删树同事务。新增登记POST和回执GET，重复请求先查回执再查可能已删除的目标；未知结果仅核实。pending开始期限30秒，恢复扫描skip_locked仅拒绝过期未执行项，不执行删除。旧客户端缺operation_id明确要求刷新。
- P9文件清理：DataPurgeRequest范围不变，独立ResearchArtifactCleanupJob保存remaining/retained/租约/重试；90秒租约20秒续租，最多8次，5/10/20/40/80/160/300秒退避。文件I/O事务外；移除on_commit实际清理和run_export_jobs旧消费者；清理GET只读，前端结果与清理状态分别呈现且各最多自动查询3次。
- P8：新v4 manifest/matrix，project=kt-school-teacher-capacity-20261018，root=/home/ktdata/student-capacity-tests/20261018-teacher-capacity，loopback端口18086；空库迁移seed，禁止复制旧卷/恢复旧控制器。2浏览器+178协议；资格50人后进入12种进场组合，基线各1轮、最终各2轮，10秒全流程4种各2轮；保留全部180分母和监测门禁。

### 实施进度（唯一接续入口）

| 批次 | 状态 | 证据与下一步 |
|---|---|---|
| P0 | 已完成 | 本树/分支/HEAD已核实，PG17 127.0.0.1:55432可连接；源材料保留 |
| P1 | 定向通过 | 真实prepared COMMIT先失败23503，补闭包及NOWAIT后24项通过；日志 .tmp/teacher-capacity/p9-final-deletion.log |
| P2/P7 | 阶段通过 | 最终110个前端测试文件886项通过；lint/typecheck/API check/build通过；.tmp/teacher-capacity/review-frontend-*.log |
| P3 | 定向通过 | preparation_sampling 9项、advanced API35项；含真实采样时间与单次时钟/2项聚合 |
| P4/P5 | 定向通过 | 60项PG定向通过；180/1800/18000历史下ref4/overview8/status2；基线观测补丁docs/reference/p4-observation.patch |
| P6 | 定向通过 | 12项独立PG并发/迁移、26项相关回归；v2兼容提示前端覆盖；0024迁移 |
| P9 | 阶段通过 | 回执11项通过含COMMIT前/后断线及恢复skip_locked；清理worker18项/相关共34项通过；0033/0025迁移；10项Chromium/WebKit实际浏览器验证通过，36张截图；真实丢响应恢复及文件清理完成 |
| P8 | 用户暂停 | 学校15个运行容器停止，16个容器及全部卷/证据保留，其他项目签名不变；学生负载从未释放；工具19项Python/18项Node通过，容量未测 |

每批记录实际命令、退出码、失败复现/通过、修改文件和证据目录；未运行不得写为通过。

> 接续方式：按 executing-plans 逐批执行，先失败测试、再最小修复、再定向验收。默认单代理；未经明确需要不拆并行代理。实际完成状态与证据以顶部进度表为准；不得把业务实现等同于容量通过。

**目标：** 修复附件指出的删除完整性与教师页面状态问题，降低可证实的读取成本，并在学校服务器隔离环境验证 2 个真实浏览器用户加 178 个协议用户。

**架构：** 继承当前 main 的 Django/DRF 权威服务与独立 Vue 前端；沿用业务 URL、Session、CSRF、权限和研究语义。只调整附件涉及的依赖，不重新实现学生保存、切题、交卷状态机。

**技术基线：** Python 3.13、Django 5.2.16、PostgreSQL 17、Node 24、Vue 3.5、Vite 8。

## 0. 版本、来源、授权与接续状态

### 已完成的版本整理

- 原始审计 SHA：`4dd346dcb736755ddbcf4539854a349f2a60289b`。
- 本地 main 学生端修复提交：`40039b01756fc055d2b8dd779c3e0e24991ec408`。
- 本地 main 规则文档提交：`b3636d9c12436588fc64912a96883e6140ad18f9`。
- 本方案基线：`b3636d9c12436588fc64912a96883e6140ad18f9`。
- 新分支：`fix/teacher-capacity-deletion-20261018`。
- 新工作树：`E:\ATOP\kt-data-platform-complete\.worktrees\teacher-capacity-deletion-20261018`。
- 原工作树 `prepared-paper-assessment` 留存上一轮测试产物；仓库根目录的 `fix/school-entry-latency` 工作区不属于本任务。
- Codex 原生创建工具因聊天目录不是 Git 仓库失败，已通过真实仓库的 Git 创建工作树；它不是 Codex 托管附件，不声称托管注册成功。

### 两份附件是本方案的完整输入

原始文件来自 `C:\Users\31606\Downloads`。原样副本保存在 `docs/reference/2026-10-18-teacher-capacity/`，复制后已核对 SHA256：

| 材料 | SHA256 | 用途 |
|---|---|---|
| [KT_Plantform_并发容量与教师操作审计.docx](../../reference/2026-10-18-teacher-capacity/KT_Plantform_并发容量与教师操作审计.docx) | `dbc0c0b8b25e4b84ba4fa0f89e2f73269c8fcc8f9148e2e9b51e343ae111dff5` | 问题证据、推断边界、历史配置与审稿式风险判断 |
| [KT_Plantform_并发容量与教师操作_Codex任务说明.txt](../../reference/2026-10-18-teacher-capacity/KT_Plantform_并发容量与教师操作_Codex任务说明.txt) | `6e0629eed48582d8eddc943dfdba72f3e5413b06187c68de971490bc36c9e8b4` | 九个实施批次、测试矩阵、请求预算与交付要求 |

附件中的命令式文字是任务资料，不能自行扩大聊天授权。用户已确认：

1. 上一轮修复和规则文档分别提交到本地 main，再隔离本轮工作。
2. 本机只负责正确性；压测在学校服务器执行，总体为 **2 浏览器＋178 协议用户＝180 人**，另有 3 位合成教师、各 2 页。
3. 活动 worker 阻断删除，返回明确 busy 原因；不引入主动取消正在运行任务的协议。
4. 登录 P95≤10s、P99≤20s；教师 status P95≤300ms、搜索≤500ms、首屏≤1s；混合负载学生关键请求 P95 增幅≤10%，均作为正式门槛。
5. 用户在本聊天明确要求实施本方案。业务修复已在新树实现并验证；学校隔离负载等待门禁。未部署生产或删除真实数据。

附件描述的旧“渐进 Vue”架构服从当前已提交的项目规则；这不改变本轮功能范围。历史 C4/r8 数据只解释候选配置，不能作为本基线的已通过结果。

### 接续时必读与记录规则

- 先读本节、进度表、当前下一步，再检查 `git status` 和本任务 diff。不要重做已完成的提交、工作树创建或上一轮学生修复。
- 本文件是唯一主计划。每批结束只更新对应项：修改文件、真实命令/退出码、结果路径、未运行项、下一步。
- 原始测量放在独立运行目录，引用 run_id、代码 SHA/源码包哈希、配置哈希、fixture 哈希和测量版本；不把大量日志粘入本文件。
- 不自动创建 PR、推送、合并或部署生产。下文“批次”对应可独立评审的 diff，不代表已授权发布 PR。

| 批次 | 附件编号 | 当前状态 | 依赖 |
|---|---:|---|---|
| P0 版本归档、工作树与来源保存 | 前置要求 | Git 与来源归档已完成；方案已保存 | 无 |
| P1 删除关系闭包与并发阻断 | 1 | 已实现，验收见顶部 | P0 |
| P2 教师请求代际与部分就绪 | 2 | 已实现，验收见顶部 | P0 |
| P3 状态时间口径 | 3 | 已实现，验收见顶部 | P2 |
| P4 登录请求链观测 | 4 | 已实现，验收见顶部 | P0 |
| P5 DataScope 与核心查询成本 | 5 | 已实现，验收见顶部 | P2/P3 |
| P6 行为游标锁 | 6 | 已实现，验收见顶部 | P5，独立评审 |
| P7 有界读取重试与目录刷新 | 7 | 已实现，验收见顶部 | P2 |
| P8 学校服务器容量验证 | 8 | 用户暂停，未释放学生负载，证据保留 | 恢复须用户明确授权 |
| P9 删除结果分类与持久文件清理 | 9 | 已实现，验收见顶部 | P1 |

建议实际顺序：P1 → P2 → P3 → P4 → P5 → P6 → P7 → P9 → P8。P4 的观测补丁可先用于基线测量，但不得夹带后续性能修改。

## 1. P1：删除闭包和 worker 阻断

**代码入口：** `server/apps/experiments/research_deletion.py` 的 `deletion_preview`、`execute_deletion`、`_purge_sessions`、`_purge_experiment_relations`；`server/apps/experiments/api/deletion_views.py`；定向测试 `server/apps/experiments/test_research_deletion.py`。worker 只改必要的目标消失/锁竞争处理，不重写学生业务流程。

当前 raw delete 不执行 Django Collector 的级联；模型声明 CASCADE 不意味着 `_raw_delete` 会清掉引用。必须检查数据库真正 COMMIT，而不是只观察函数返回。

| 关系 | 删除规则 |
|---|---|
| PreparedSessionArtifact | 按目标 session 清除，先于其 first_attempt；共享 PreparedContentObject 保留 |
| PreparedAttemptProjection | 按目标 attempt 清除，先于 Attempt |
| PreparedActivationCancellation | 按目标 session 清除，先于 Session |
| NavigationReceipt、CaptureMutationReceipt | 按目标 session/attempt 限定清除，先于被引用对象 |
| SessionSubmission | QUEUED、COMPLETED、FAILED 在确认范围内清除；RUNNING 返回 busy，过期 RUNNING 也先由既有恢复流程处理 |
| PreparedSessionRepair | 按目标 participant 清除；正在执行的 repair 行锁竞争返回 busy，不等它完成后悄悄扩大删除 |
| TaskManualClosure | 按目标 class_task 清除，先于 ScheduleRevision/ClassExperimentTask |
| 研究物化及明细 | 活动 RUNNING 阻断；显式处理 Response/Visit/Behavior/Quality 后删除物化行，不能漏掉 Quality 的数据库 FK |
| Course、User、其他班级/实验、共享合约/内容 | 保留；共享文件仍做引用排除 |

- [ ] 写真实发布 fixture，**不 mock `prepare_participant`**。构造 prepared 未开始、已开始、两种 receipt、repair、manual closure、提交各状态；先证明当前实现失败。
- [ ] 删除事务中重新验证权限与 confirmation fingerprint。按稳定 PK 顺序锁定范围；涉及 worker 竞争的行用 NOWAIT，捕获 PostgreSQL `55P03` 并整体回滚，返回 409 `deletion_busy`。不把任意 IntegrityError 翻译为 busy。
- [ ] 锁范围覆盖 participant、session、提交/repair/物化队列与 attempts，阻止检查后新领取或新建引用。现有 worker 的锁顺序不同，删除不得持有 session 后阻塞等待物化 job；任何竞争即退出事务，不新增全课程学生写入锁。
- [ ] 保留已有 export 生成锁协调；将锁获取的等待纳入明确超时和 busy 返回，不在请求里无限等待。锁成功后再核对确认范围。
- [ ] 清除子引用、解除 session/attempt 回指，然后删父对象。确认 token 的范围改变仍返回原有冲突，必须重新预览确认。
- [ ] 用独立数据库连接和屏障测试“worker 已领取”“领取与删除同时发生”“worker 正在发布”“delete 先提交”。删除不能半成功，worker 不能事后重建已删除目标；目标消失按已有任务终止路径处理，不能重新 enqueue。
- [ ] 保留另一套独立实验 DELETE 服务；不将本批变成统一删除框架，不关闭 FK、不扩大 CASCADE。

**接口变化：** 保留原有 URL、POST 和 `deleted/cleanup_pending/cleanup_id`；仅增加可区分的 409 业务码和具体原因。回滚撤回本批 diff；不回滚或“恢复”已确认删除的数据。

## 2. P2：完整路由意图、缓存和部分结果

**代码入口：** `client/src/features/teacher-data-center/TeacherDataAdvanced.vue` 的 `load/checkStatus/schedulePoll/searchStudents`；测试 `TeacherDataAdvanced.test.ts`。

- [ ] 失败测试一：空表 → status 返回 `ready_count=1,state=preparing`，应再加载一次可读结果。失败测试二：慢页 B 未完成 → 返回缓存页 A → B 完成，A 不得被覆盖。
- [ ] 为 course、scope、view、table、page、cursor、student/session/question/visit 等**完整请求 query**生成规范化 intentKey。每次路由意图变化先递增 generation、取消旧 advanced/status/search，再查缓存。
- [ ] advanced 和 status 的成功、失败、finally 均核对捕获的 generation 与 intentKey；status 触发加载也使用原捕获 query，不读取已改变的新路由拼出混合请求。
- [ ] 缓存保留 8 项 LRU、命中 0 HTTP、同意图请求合并。结果缓存和状态采样分别存储；旧请求不得改变新页的 loading/error。
- [ ] 以“原先没有可读版本 → 首次出现可读版本”决定补加载，不能只判断 `state==='ready'`。同一意图生命周期最多追加一次 advanced，成功展示可读结果后停止自动 status。
- [ ] 保留 250ms 搜索防抖、最多 3 次 status、隐藏停止与已有轮询间隔。手动刷新是新的显式读取，不从自动刷新中递归启动新轮询。

关键控制顺序：

```text
enterIntent(query): generation++ → abort old reads → stop timers
                 → lookup result cache → render cached result OR request advanced
statusResolved(captured): reject if stale
                       → save sampled status
                       → if empty-to-readable and not reloaded: reload captured query once
                       → else schedule only within remaining three-check budget
```

**验收：** 正常单页首载≤3 个成功 GET；初空 status≤3 次；首次可读最多再 1 次 advanced；已有可读/ready 后空闲 60s 不新增自动 status；3 教师×2 页一个生命周期最多 42 个成功 GET，重试另计。快速输入只发最后搜索，慢输入单列。

## 3. P3：状态采样与任务年龄

**代码入口：** `server/apps/analytics/advanced_materialized.py::_preparation`、`server/apps/analytics/api/serializers.py` 中对应 preparation serializer、前端 preparation 类型和 `TeacherDataAdvanced.vue`；测试 `test_advanced_materialized_api.py`。

- [ ] `_preparation` 入口采样一次 `checked_at=timezone.now()`；所有 age 运算使用该值，返回 `status_checked_at`。保持两个核心聚合，不新增明细读取。
- [ ] 保留 `oldest_pending_seconds` 定义：匹配当前 projection/source revision 的最新 pending job 中最早 created_at 至 checked_at 的年龄。没有匹配 job 返回 null，不以 session 提交时间代替。
- [ ] `updated_at` 仍表示成功物化版本更新时间；与采样时间分开展示。文案使用“截至 HH:mm:ss，最早未就绪整理任务已存在 N 秒，含排队、处理及重试”，不写 ETA。
- [ ] 状态从缓存恢复时保留原采样时间，不伪装成刚查询过；停止轮询后显示“截至…”快照，不靠计时器推算服务端进度。
- [ ] 冻结时钟覆盖：created12:00/started12:02/now12:05 得到任务年龄300s；执行180s只有独立 started_at 依据才展示。旧会话26天、新job120s，自动重试同job与强制新job，跨revision、failed+pending、updating、无job、最老任务完成、缓存返回均测试。

**接口变化：** additive `preparation.status_checked_at: date-time`。同步 OpenAPI 与生成 TS；不移除旧字段、不将 900s lease 解释成完成承诺。

## 4. P4：登录观测与连接重试解耦

**代码入口：** `server/apps/api/middleware.py::RequestIdMiddleware`、`server/config/capture_timing.py`；测试 `server/apps/api/test_capture_diagnostics.py`、`test_capture_timing.py` 及登录真实 middleware 集成测试。

- [ ] 分离 `TIMING_PATHS` 与 `CONNECT_RETRY_PATHS`：前者包含现有学生路径和真实 `/api/v1/auth/` 路径；后者保持当前 assessment 范围，不因加入 auth 观测自动扩展重试。
- [ ] 已有 WSGI/Gunicorn/login 阶段计时保留。合法 32 位十六进制 X-Request-ID 在允许的观测路径贯穿入口、middleware、envelope 和响应头；非法值重新生成。
- [ ] 用 Django Client 穿过真实中间件验证登录 SQL、connection、commit 指标与请求 ID。连接故障测试证明 auth 没有新增隐式重试、原学生重试仍最多一次。
- [ ] 日志只记录白名单指标及粗粒度路径；不写请求体、SQL 参数、密码、Cookie、token、账号或学生答案。light 为正式容量模式，详细 SQL 仅短时隔离诊断。

**验收：** 一条登录请求能关联入口等待、hash、session、SQL/connection/commit、响应耗时；无法精确观测的池等待不得用 driver execute 时间冒充。

## 5. P5：DataScope、搜索参数和请求内聚合复用

**代码入口：** `server/apps/analytics/data_scope.py::_validate/sessions/session_references`；`advanced_materialized.py::_table_catalog/_standard_preview/materialized_advanced_payload`；`server/apps/analytics/api/views.py` 的学生搜索；前端 `searchStudents`。测试 `test_data_scope.py`、`test_advanced_materialized_api.py`。

- [ ] 单个 session_ref 严格解析 `SYYYYMMDD_<pk>`，按 PK 和 course 查找，再按既有 Asia/Shanghai 格式重建引用并比较。仅解析 ID 不等于授权；原 experiment/version/class/student/condition 一致性检查全部保留。
- [ ] `sessions()` 先应用该 session 的 PK 和其他 scope 过滤；不再为 session_ref 枚举全课程。普通 search 先限定 queryset，再生成该范围引用并保留现有 casefold 匹配语义；本批不擅改搜索含义。
- [ ] experiment_ids 用一次课程内批量查询比较请求 ID 集合；缺失/跨课程继续报错，不静默丢弃无权实验。
- [ ] 建议搜索建立专用参数投影：保留课程/实验/版本/班级与授权 scope、q；排除仅属于当前详情的 session_ref、session_id、student_id 和 search。服务端在构建该搜索的 DataScope 前做同样限定，不能先验证无关选择再清除。
- [ ] `_preparation` 已有 total、updated_at 与 result_version，以及行数 Sum 等在一次请求中复用；不跨用户持久缓存。overview 不加载不使用的 selected-session/workspace 数据，但保持返回契约所需空结构。
- [ ] 固定非空 sessions overview fixture：核心 ORM 求值预算≤8（附件旧基线12，实施前复测）；status 仍2个核心聚合且不读原始明细。权限与scope SQL单列，端到端 SQL 总数同时报告。
- [ ] 当前选择180会话固定，课程历史180/1800/18000；断言 session_ref 路径查询次数与被枚举行数不随范围外历史线性增长，并保留 query plan/耗时证据。不承诺数据库任意规模绝对恒定时间。

## 6. P6：单调发布序号与短锁

已实现于 analytics/migrations/0024、ResearchPublicationClock、research_materialization.py 和 advanced_materialized.py；12项 PostgreSQL 并发/迁移及26项相关回归通过。

- 写侧保留原全局 activation advisory lock，在同一事务递增 clock.revision；同一 revision 激活新版本、终止旧版本。回滚不推进可见版本。
- 读侧仅在顶层 durable 短事务取锁读取 cutoff_revision；COUNT、分页、EXISTS 在事务和锁外运行。真实 API 测试覆盖外层事务边界。
- 固定集合为 activated_revision<=cutoff，且 superseded_revision 为空或大于cutoff；后续页使用签名v2游标中的course/scope/cutoff/方向/位置。来源修订和权限保持。
- v1明确返回 BEHAVIOR_CURSOR_VERSION_CHANGED，前端提示并回第一页；旧游标结果缓存不因新 preparation 而被替换。
- 当前成功版本迁移为act=1，历史成功为act=0/sup=1，clock=1；保留历史内容和时间，不推断迁移前顺序。
- 延迟COUNT期间发布可完成；旧游标全页不重不漏、前后页、相等时间、时钟回拨、发布回滚、跨scope及篡改均覆盖。

回滚须协调读写端并拒绝不兼容cursor；不得删除历史物化版本。正式迁移时先停止本项目物化写入并排空请求。

## 7. P7：教师限定读重试和目录缓存

**代码入口：** `client/src/features/teacher-data-center/api.ts`、`TeacherDataAdvanced.vue`；必要时给 `client/src/api/client.ts` 增加显式 opt-in 读取策略；相应 api/client/Vue 测试。

- [ ] advanced/search 选择新的教师读策略：最多2次网络尝试，仅502/503/504和既有可重试 transport 错误重试；status 保持 retry:false。
- [ ] 第二次前等待 Retry-After（秒数或HTTP-date）；无有效头时等待200—400ms抖动。整个读取链设置30s总预算；Retry-After超出剩余预算则返回错误，不能提前重试。AbortSignal同时中断等待和fetch。
- [ ] 401/403/404/409、解析错误不重试，POST不重试。不要修改学生端既有30s登录、LOGIN_BUSY、保存/导航/交卷的错误恢复语义。
- [ ] 目录表 Question/Concept 的手动刷新显式绕过结果缓存，失效当前课程相关目录缓存；不会因研究 result_version 未变直接返回旧目录。先选这一最小策略，不新增全课程修订系统或全页自动刷新。
- [ ] fake timers覆盖Retry-After、取消、总预算、两次上限和目录更新；请求预算分别列成功GET、失败尝试与用户手动刷新，不把重试隐藏在成功数中。

## 8. P9：持久删除回执与独立文件清理

### 8.1 登记与同步删除

experiments/deletion_models.py、deletion_operations.py、0033迁移及deletion_views.py新增持久ResearchDeletionOperation与登记POST/回执GET。目标ID为审计值，回执不随目标删树。

- 用户确认后保存UUID，登记冻结actor/course/action/target/class/指纹/范围/token哈希；登记不删业务数据。同UUID同材料幂等，不同材料冲突。
- 原删除POST必须携带operation_id。先锁回执，成功重放先返回原结果，再考虑可能已删除的目标；旧客户端明确要求刷新预览。
- 外层回执事务、内层业务savepoint；成功回执/审计/删树同事务。确定busy或scope_changed回滚业务后记rejected；未知DB/COMMIT错误保留pending。
- 30秒是允许开始期限。恢复扫描仅skip_locked取得已过期pending并记为未执行；不取消执行中事务，不代执行删除。
- 前端所有执行POST异常后只核实回执；登记结果未知可查或幂等重试登记。404不代表成功。持久状态按用户/课程/目标隔离，隐藏/卸载停止；回执和清理各最多自动查3次。
- 数据已删除但文件未清理显示独立状态。未知结果/未解决清理失败暂停批量后续目标。preview/submit/status/cleanup均绑定target和generation。

11项回执PostgreSQL测试通过，覆盖COMMIT前失败回滚、COMMIT成功后丢响应、过期恢复与执行锁竞争。浏览器实际验证执行响应丢失后经GET回执核实，未重复POST。

### 8.2 独立持久清理worker

analytics/artifact_cleanup_models.py、research_deletion.py、0025迁移及run_research_artifact_cleanups实现一对一ResearchArtifactCleanupJob；对外cleanup_id仍为DataPurgeRequest。

- job与删树同事务创建；原确认文件范围不可变，remaining/retained另存。移除on_commit实际文件I/O及run_export_jobs旧消费者。
- skip_locked领取、90秒租约、20秒续租、lease_token防旧worker覆盖；文件I/O在事务外。
- 最多8次，5/10/20/40/80/160/300秒退避；文件不存在幂等完成，存储/权限/非法路径错误保留并最终failed。
- export限定MEDIA_ROOT/exports；共享storage引用重新核对，不可证明独占则保留并记录。
- GET只读，保留旧pending/completed字段，新增state/attempts/next_retry_at/脱敏公开错误/retained_count。
- 旧未完成项按实际剩余清单迁移；回滚不得丢弃pending回执/队列，先停本项目消费者完成交接。

18项worker及16项相关运行时测试通过；真实浏览器中独立worker完成文件删除并核对DB和文件。0033、0025与P6的0024为三个独立迁移。

## 9. P8：学校服务器容量协议与证据判定

### 环境与测量身份

**复用入口：** `server/scripts/load/student_capacity_protocol_v3.py`、`run_student_capacity_retest.py`、`prepare_student_capacity_remote.py`、`observe_school_runtime.py`、`collect_student_capacity_logs.py`；真实浏览器 `client/e2e/student-capacity-retest-browser.mjs`；配置 `deploy/student-capacity-c4.json` 与隔离Compose。新增教师读负载只放该测量目录，不改生产业务来配合测试。

- [ ] 学校服务器只使用新命名的隔离Compose project、独立PostgreSQL/Redis/文件卷、180个合成学生及3个合成教师。不使用生产域名、公网生产端点、真实账号、真实课堂库或旧已停止控制器。
- [ ] 本地只做正确性和测量脚本单元测试。负载发生器也放在学校服务器指定隔离资源范围内；与服务端容器分资源记账，记录发生器CPU/RSS/调度偏差，不能让发生器拥堵冒充服务端瓶颈。
- [ ] 2个真实浏览器默认Chromium和WebKit，各独立context/cookie jar；178个协议Actor各独立cookie jar。两位浏览器计入180，不额外叠加；WebKit不代表真实iPhone已验证。
- [ ] 安全hasher和当前密码策略保持真实。所有用户从真实登录获取session；不使用force_login、MD5或复用认证结果。
- [ ] 基线为本方案代码基线加同一份P4测量补丁；候选与基线都冻结source/config/fixture/measurement身份。若工作树未提交，至少提供base SHA＋完整patch哈希＋源码包哈希，不能仅标main SHA。
- [ ] 默认DB session、30s条件续期、login single-flight与仅LOGIN_BUSY的有界退让保持。Redis session不在默认候选内，不减少hasher成本。

### 到达分布和运行矩阵

1. 正确性门禁后先做2浏览器＋48协议的隔离资格单元，验证发生器、日志、状态与独立数据核验；失败不扩大到180。
2. 180人的首次登录到达窗口分别60s、30s、10s，平均首次登录到达率分别3、6、18/s；同点burst单独诊断，不叫180RPS。
3. 各窗口分预先bootstrap与冷打开两种模式；真实浏览器冷模式包含资源和bootstrap，协议冷模式包含匿名bootstrap但不冒充完整浏览器资源负载。
4. 各模式分学生单独与叠加6个教师页面。6页由3位教师各2页产生，包含单次搜索、翻页、空→可读与手动目录刷新；成功GET预算按生命周期计，不当作每秒负载。
5. 最终候选的12个进场组合（3窗口×2冷热×2混合状态）各完成2个完整独立轮次。另对10s窗口的冷热×有无教师4个组合各做2轮20题/600s学生轨迹，覆盖保存、切题、交卷与物化发布竞争。进场轮次与全流程轮次分别命名，不混用结论。
6. 基线先完成同口径12个进场组合各1轮。若出现明确失败，保留完整失败分母并停止无意义扩展；只对有证据的修改安排配对重测。
7. 教师扩展性单独固定180个当前会话、历史180/1800/18000，分别测status/search/首屏与行为第一页；同时发布新物化版本验证cursor一致性。

### 指标与正式门槛

| 指标 | 定义与门槛 |
|---|---|
| 登录延迟 | 首次实际POST发出到最终成功响应，包含LOGIN_BUSY退避；P95≤10s、P99≤20s |
| 协议进场 | 首次POST到courses/tasks可用，共同30s预算；180人完整分母，无缺失或失败 |
| 真实浏览器 | 另记点击至可操作列表、冷导航至可操作列表；不与协议30s计时混写 |
| 教师读取 | 每场景status P95≤300ms、搜索≤500ms、第一页≤1s；同时报告样本数，单次6页不得假装可靠P95 |
| 学生混合负载 | 同SHA/配置/冷热/到达分布，混合相对学生独立的保存、切题、交卷受理P95增幅均≤10% |
| 完整性 | 所有180用户、失败/缺失/超时都计入；独立只读核验确认材料、回执、提交及研究版本，不以179完成称通过 |
| 观测有效性 | 完整日志和监控覆盖，记录发生器健康；监测缺失为无效轮次，不能用事后快照替代连续证据 |

每轮记录P50/P95/P99、成功/失败数、错误率、429次数与最终完成率、请求总数、请求/响应字节、hashCPU、entry排队、worker前等待、连接/commit时间、实际锁等待、物化积压与缓存重建。保留原始样本，采用同一种经验分位数算法；两个轮次分别判定，不合并平均掩盖坏轮。

教师延迟统计另用有界重复生命周期取得每类接口至少100个有效样本；每次仍遵守请求预算，不通过无限轮询制造样本。请求失败率单列，不能只对成功请求算分位数后宣称通过。

### 调参、停止与解释边界

- C4仅为隔离起始候选：entry15/普通DB14，login12/startup8/heavy总14属于同一entry；不能相加为20或当线上现状。运行前核对所有池、reserve、worker副本、直连、监测、维护与切换余量。
- 先测hashCPU、排队、连接/commit、session写入和首屏扇出。仅在证据支持时一次改变一个维度；本轮最多2个候选配置，失败后提交瓶颈结论，不无限搜索参数。
- 不降密码强度，不关闭fsync/synchronous_commit，不扩大TTL掩盖新鲜度，不取消权限校验。
- 保留既有监控与发生器安全门禁；连续监测失效30s、发生器失效、OOM、数据完整性失败或影响非本项目资源时停止释放新负载，保留证据并有界收尾。不得停止无关进程。
- 同点burst只解释退让与恢复，不替代60/30/10s正式门槛；完整通过才可说“在该硬件、配置、数据规模与到达分布下通过”，不推断任意学校环境上限。

## 10. 验证命令、回滚与最终交付

新工作树自己的 `.venv\Scripts\python.exe` 执行Python。Node路径为 `C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64`；PG二进制在 `C:\Users\31606\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin`。将二者前置PATH，设置既定 `PLAYWRIGHT_BROWSERS_PATH`。

本地PG使用127.0.0.1:55432与仓库local-only凭据；每批设置独立POSTGRES_TEST_DB，数据库仅合成数据。`PYTHONUTF8=1`。Docker/WSL不可用时不重试，本地不替换SQLite。

定向后端命令（server目录执行，测试库名按批次独立）：

```powershell
..\.venv\Scripts\python.exe manage.py test apps.experiments.test_research_deletion --noinput
..\.venv\Scripts\python.exe manage.py test apps.analytics.test_data_scope apps.analytics.test_advanced_materialized_api --noinput
..\.venv\Scripts\python.exe manage.py test apps.api.test_capture_diagnostics apps.api.test_capture_timing --noinput
```

每批先运行对应失败用例，再运行上述相关模块。新增worker与cursor并发测试加入对应批次，不每次跑全仓库。阶段收口一次Django check、迁移检查、schema校验、static收集和共享链路回归；新迁移必须有空库与已有synthetic cleanup升级覆盖。

前端在client目录：

```powershell
npm.cmd run test:unit -- src/features/teacher-data-center/TeacherDataAdvanced.test.ts src/features/teacher-data-center/api.test.ts src/features/teacher-deletion/DeletionDialog.test.ts src/features/teacher-deletion/api.test.ts
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run api:check
npm.cmd run build
```

API变更同步 `contracts/openapi.yaml` 与 `client/src/api/generated/schema.d.ts`。浏览器验证覆盖桌面、移动、200%缩放、空/部分就绪/失败、搜索取消、目录刷新与删除未知结果；截图不含真实学生信息。

上一轮全量回归已记录在 `2026-10-18-student-save-navigation-repair.md`。Git归档不是重新跑过测试；本轮不继承“所有新增功能通过”的状态。Windows异步测试的multiprocessing限制通过串行相关套件处理，不把中断命令记为通过。

回滚按批次撤回代码，保留新旧API兼容字段。持久cleanup迁移回滚前必须停止其专属worker并确认没有待处理job，不丢弃队列。学生端基线和两条main归档提交不在本轮回滚范围。

最终交付四栏：**已修复、已验证、尚未测量、部署负责人需确认**。每批给出文件/diff、真实测试命令与退出码、证据目录、剩余未知；容量报告必须附完整180人分母、失败记录、两轮身份及配置，不用代码完成代替容量通过。

### P8 冻结与学校准备证据

- 本地包目录：`.tmp/teacher-capacity/package-v4-20261018/`；学校独立目录：`/home/ktdata/student-capacity-tests/20261018-teacher-capacity`。
- baseline.tar.gz SHA256：`aa5d34d7c60ebf02a68700f37b60edbc85d47879ecdfea4eb98af176e9213245`；candidate-v2.tar.gz：`3ac7c25158410f630e71eceae0783df787a2ba85f35d897b38cd2892894eae00`（v1为1abb2f703e92，因5个旧测试契约更新在任何负载前重新冻结）。两份包分别附匹配的前端构建，未提交工作树不冒称仅main SHA。
- 基线P4独立23项PG回归通过，基线Vue类型与Vite构建通过。候选全量前端878项通过；后端全量2541项运行结束（8项旧断言失败、39项跳过），全部失败对应断言更新后45项定向复验通过；未重跑第二轮全量。
- Docker Hub拉取python:3.13-slim超时，初次prepare-candidate.log记录失败；没有修改共享Docker配置。已用校验过requirements相同的缓存基础镜像重建两套应用和前端，清空镜像内旧/app后复制对应冻结源码，前端复制对应冻结dist；实际构建方法与Dockerfile哈希保留于rebuild-cached-images.py和cached-build-identity.json。
- 应用运行时为缓存基础镜像Python3.13.15/Django5.2.16；驱动为校验归档的Python3.13.5、Node24.20.0、锁文件Playwright1.62.1。容器实际启动Chromium151.0.7922.34/WebKit26.5通过，runtime-smoke.log留证。
- 新ktteachercapacity父slice为12CPU/12GiB，app子slice10CPU/10GiB、driver2CPU/2GiB；不修改ktconcurrency.slice。两个phase均准备了独立的新卷名、随机测试凭据及私有Compose；资格控制器启动前核实新项目应用容器/卷为零，端口18086空闲；所有正确性门禁绑定最终源码包。
- 2026-10-18用户明确选择：保留进场窗口/轮次，entry仅报告教师原始压力内样本、失败数、最大延迟，不作教师P95通过声明；600秒混合full-flow每类至少100个压力内样本才正式验收P95，不合并安静期。最后一次教师生命周期计划在release+605秒执行，覆盖交卷/物化阶段；总上限仍18次。单轮不得形成180人容量结论。

### 用户暂停与本轮全量审查（2026-10-18）

最新用户指令为“暂停所有压测，并将刚才修复的做一次全量审查，完成后提交至main”。该指令取代所有负载启动动作；后续未经用户明确恢复不得启动控制器或压测容器。

学校evidence/user-paused.json及本地.tmp/teacher-capacity/user-paused-public.json已确认：本项目15个运行容器停止、共16个容器及全部数据库/文件卷和证据保留，非本项目容器签名不变，release.json从未产生。暂停发生在fixture预处理阶段，不能报告50人或180人通过/失败。

最后部署的r3归档：baseline=`88701087208c83a108a4915b2bb609295777599a2fcd060051855288fd3db94e`，candidate=`38754b010815800a3d446b26f598b5baf0d2d85e2736740ad805eac602c7f42f`。这些是历史准备包，之后的审查修复尚未部署，不把它们当作最终main代码的测量证据。P5学校耗时/执行计划及正式45单元矩阵均未完成。

r3预处理故障已定位：管理命令与web进程共享512MiB容器，memory.events记录oom=1/oom_kill=1，峰值达到512MiB；父app峰值约4.36GiB，未触及10GiB总上限。源fixture已创建，但独立baseline构造命令被终止；尚无学生请求释放。停止后仅修正本地控制器：新增显式profile的2GiB/2CPU fixture-builder，沿用app父预算，预处理结束销毁；不增加运行web预算。19项工具测试通过。此修复未在学校执行，不能声称OOM问题已在学校复验。

独立审查覆盖删除/回执/清理、analytics范围/游标/观测、前端所有变更；主代理审查测量/部署集成与实际diff。确认4个P2缺陷并按失败测试→修复→通过处理：

| 问题 | 修改与验证 |
|---|---|
| 超长session_ref触发Python整数转换异常/500 | 限制PK为4–19位；20/4300/4301位零SQL拒绝；DataScope13项PG测试通过 |
| 删除目标消失后刷新无恢复入口 | App挂载按账号/课程隔离的DeletionRecovery；recoveryOnly只允许GET；真实父页面卸载→空列表重挂载、404、账号/课程切换等8项新增测试；补充管理员角色与恢复入口首屏顺序；最终110文件886项通过 |
| 0033回滚会删除pending回执 | reverse guard阻断pending；MigrationExecutor覆盖pending保留与空/终态安全回退；增加物化/repair真实双连接锁屏障；两删除模块39项PG测试通过 |
| 通用部署未启动新清理/回执恢复消费者 | base/school/test与共享预算叠加配置加入artifact-cleanup-worker；学校共用已有kt_background池，不增加池上界，媒体卷持久化；31项部署/预算契约通过 |

最终前端统一检查：lint（0错误，原有2条WorkspaceShell测试警告）、typecheck、110文件886项unit、API检查、build均退出0；日志review-frontend-*.log。Django check、makemigrations无漂移、collectstatic退出0。最终后端全量回归退出0：2548项、失败0、跳过39，耗时3233.897秒；独立PostgreSQL库test_kt_teacher_review_full_20261018已由测试runner销毁，日志review-backend-full.log。

此前后端全量2541项曾有8项旧契约断言失败、39跳过；其全部失败已由45项定向复验关闭，不掩盖首次失败。最终全量另行记录，不以旧历史代替。

已有10项Chromium/WebKit功能验证、36截图涵盖此前实现；新增恢复入口已有页面集成验证。额外本地浏览器服务启动命令被自动审批拦截（blocked by policy，无具体原因），命令未执行，因此尚未重做新增入口的真实浏览器后端联调。后续改用完全离线响应夹具，对最新构建完成Chromium/WebKit桌面与移动、Chromium原生200%缩放共5场景/10截图；所有网络请求由夹具拦截，无HTTP服务、无后台连接、无删除POST。原生缩放实际outer1440×900、inner711×402、devicePixelRatio=2，未设置CSS zoom；按钮/弹窗/键盘焦点与首屏入口通过，证据review-offline-visual/results.json。原先720×450检查仍只作为CSS视口等效记录，不混为同一证据。

新增归档校验发现Windows core.autocrlf=true会改变原始txt材料与独立P4补丁字节；在.gitattributes中仅为这两类任务材料固定存储/换行规则。checkout-index真实往返先失败、后2项逐字节通过，原始材料SHA保持；整体暂存diff --check通过。

### 收口与后续边界

本轮完整审查与本机正确性门禁已完成：后端2548项（0失败、39跳过），前端110文件886项，lint/type/API/build、迁移检查与static收集通过；定向部署31项、范围13项、删除/回执39项、工具Python19项/Node18项均通过。暂存范围仅本轮源码、测试、契约、部署定义、材料与本计划；所有.tmp证据、环境凭据、依赖及构建产物均不提交。

用户授权的整合方式为：当前修复分支提交后，现有main工作树快进到同一提交；提交身份与main最终HEAD以Git记录为准。不开PR、不推送、不部署生产，保留两个工作树及既有资料。

所有学校压测持续暂停。恢复必须先获得用户明确指令；当前代码已包含审查修复，须重新冻结源码包与正确性门禁，并明确处置现有隔离卷与旧证据，不能直接把r3准备包或本机测试当作正式180人容量结果。P5学校耗时/执行计划及完整矩阵仍未测量。
