# 修复后50／180人压力测试与配置调优计划

## 一、目标、固定边界与验收标准

本轮使用修复后的完整代码，复用此前的学生、题目与试卷配置，在独立测试副本中验证登录、加载、作答、自动保存、切题、交卷及统计更新。

**仅安排50人与180人压力负载；删除本轮矩阵中的240／360人探索。worker、CPU使用范围、数据库连接数和入口保护不设人为固定上限，依据实测逐步调整。达到全部门槛后，独立复测并冻结配置。**

执行前将本计划保存为：

`E:\ATOP\kt-data-platform-complete\.worktrees\prepared-paper-assessment\docs\superpowers\plans\2026-10-16-student-capacity-retest-50-180.md`

在原计划中增加指向新计划的续测入口，保留旧实验结果及暂停记录。本次规划尚未写入文件、修改代码或启动测试。

### 已确定的实验安排

- 调参使用协议负载；正式验收使用**48名协议用户＋桌面、移动端各1名浏览器用户**，或**178名协议用户＋上述2名浏览器用户**。
- 两个浏览器使用不同账户、独立上下文。移动端采用浏览器移动设备仿真，报告注明不等同于实体手机。
- 保留两题轨迹作为历史可比对照；正式作答验收覆盖已有20题、五种题型和服务器分配的信心采集。
- 全员持续作答每单元600秒。
- 会话继续使用数据库，`SCHOOL_REDIS_SESSION_ENABLED=0`。
- 默认关闭HTTP数据库连接复用：`DATABASE_CONN_MAX_AGE=0`。复用另设有条件候选，不能直接采用此前未通过的REUSE60。
- 仅操作新建测试副本；旧实验库、卷、配置、镜像及归档保留，生产不切换。

### 验收门槛

| 环节 | 判定口径与门槛 |
|---|---|
| 登录 | 点击登录至目标列表可用，P95≤10秒、P99≤20秒 |
| 启动 | 点击开始至首题可交互，P95≤5秒、P99≤10秒；协议结果明确标为模拟可交互边界 |
| 自动保存 | 计划输入变化至服务端确认对应原始事件持久化，P95≤1秒、P99≤3秒 |
| 切题 | 点击至目标题目就绪，新增本轮目标P95≤1秒、P99≤3秒 |
| 交卷受理 | 确认弹窗点击确认至持久202回执，P95≤1秒、P99≤3秒，包含最后保存和材料收尾 |
| 后台交卷 | 持久受理至完成，P95≤60秒、全部≤300秒 |
| 统计 | 会话结束且所需材料到齐后，最新版统计及导出数据在300秒内可读 |
| 正确性 | 全部计划用户和动作保留在分母中；已确认事件不丢失、最终答案一致、交卷唯一、信心绑定正确 |
| 稳定性 | 无OOM、非预期服务重启、监测缺失；600秒内不能靠持续增长的未确认积压维持表面吞吐 |

超时、未执行、失败和重试不得从分母中移除。两个浏览器单独报告实际耗时，不用两个样本估计总体P95／P99，也不与协议样本混算分位数。

## 二、数据复用、运行环境与起始配置

### 1．建立可重复恢复的数据副本

使用既有私有归档：

`E:\ATOP\.tmp\student-capacity-20261016\paused-capacity-20261016-private.tar.gz`

归档SHA256：

`1e3ee222e2bfcd449e3f1d8460ee1129eecd11057a055d64bf8c712824fd644d`

默认复用来源为归档中的：

`evidence/matrix-smoke1/cells/m3563149ed2fa1776d1a4/fixture.json`

该清单已确认包含180名学生、20题、五种题型。清单记录的是历史起始状态，不能用它证明当前数据库仍处于未作答状态。

实施方式：

1. 从保留的旧隔离数据库取得一致性备份，恢复至新项目专属数据库卷；复制对应预生成内容与媒体文件。
2. 复用上述清单的学生账户、题目内容、题序和实验配置。在副本内建立本轮专属待测任务与会话，保留复制进来的历史作答，不倒改旧任务的终态。
3. 比对原题目快照与本轮快照的内容、题型、评分、KC、信心配置；记录旧新ID映射。新任务的发布根及分配只生成一次，随后全部候选复用。
4. 通过正常发布服务生成整卷资源，确认20题齐全、无待处理交卷和统计积压，建立本轮起始快照。
5. 每个单元执行前，停止新副本的写入服务，恢复同一快照及文件基线，启动当前候选配置。
6. 混合场景的预登录、预启动、预填答案在恢复后通过真实服务接口准备，不直接修改会话状态。
7. 每轮结束先保存结果和核对证据，再恢复下一轮；旧实验目录始终不被覆盖。

清单分离三个身份：`source_fixture_id`、`baseline_snapshot_id`、`run_id`，避免继续要求历史fixture的run_id与新实验run_id相同。

50人采用固定且按题型、信心分配覆盖检查的子集；180人使用全体。同一人数的候选与复测使用相同名单和固定行为种子。正式混合驱动时，浏览器账户从该名单中扣除，不增加隐藏的第51／181人。

### 2．新隔离环境

- 项目：`kt-school-e2e-capacity-retest-20261016`
- 目录：`/home/ktdata/student-capacity-tests/20261016-retest`
- 服务监听：`127.0.0.1:18085`
- 本地证据：`E:\ATOP\.tmp\student-capacity-retest-20261016`
- 使用新镜像标签与源码摘要，不能覆盖旧v6／v5镜像。
- 构建必须包括修复后的前端产物，并核对运行页面实际引用的资源摘要。

压测流量优先由本地Windows主机发起，经独立SSH转发到新隔离端口，使协议生成器和两个浏览器不占用被测服务器CPU。启动前验证本地CPU、内存、调度延迟和隧道吞吐；生成端不能按计划发出负载时，该单元标为生成器受限，不能判服务器容量失败或通过。

### 3．起始配置R0

R0沿用暂停时保存的W12D8配置，作为**修复后重新测量的起点**，不是推荐最终值。

| 项目 | R0 |
|---|---:|
| 入口worker／普通数据库池 | 15／11 |
| 作答保存worker／普通数据库池 | 12／8 |
| 采集worker／普通数据库池 | 3／3 |
| 核心、教师读取worker／池 | 各1／1 |
| 交卷消费者／池 | 1／1 |
| 统计消费者／后台池 | 1／2 |
| 登录／启动／合计并发保护 | 12／8／14 |
| 激活事务并发 | 8 |
| 启动速率／burst | 64次每秒／8 |
| PostgreSQL `max_connections` | 初始64，可随预算调整 |
| HTTP连接复用 | 0秒 |
| CPU | 默认共享全部可用CPU，不固定绑到少数核 |

本次只读核验为16个在线逻辑CPU、约15.6GiB内存。执行时重新检查容器、父cgroup、CPU affinity及实际节流，不能只读宿主机CPU数量。

## 三、需要修改的代码位置

以下修改均在现有工作树增量实施。行号以本次读取为准，实施时以函数或配置块定位，先检查已落盘修改，避免覆盖已有成果。

### A．解除旧数值白名单，保留资源一致性校验

| 文件与定位 | 明确修改 |
|---|---|
| [settings.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/config/settings.py:84)，容量配置分支及约第145行全局准入检查 | 新增本轮`shared-entry-v3`配置分支。允许任意合法正整数worker、池大小、消费者数、入口并发、速率和burst；移除本轮15/19、12、1/2/4、64等白名单。将v3加入共享入口准入判定。旧v1/v2规则保留用于历史重放。 |
| [pgbouncer-entrypoint.sh](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/deploy/docker/pgbouncer-entrypoint.sh:11)，前置校验及容量分支 | 前置`≤20`不能误拦截v3；v3不执行`≤128`及固定档位检查。与Django保持相同的正数、资源预算及保护关系校验。 |
| [docker-compose.student-capacity.yml](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/deploy/docker-compose.student-capacity.yml:6) | 参数化采集、核心、教师读取worker与池，启动速率、PostgreSQL连接数、资源配置及PgBouncer客户端数。覆盖继承配置中的固定数据库启动命令，使实际`max_connections`与预算一致。 |
| [pgbouncer-school-capacity.ini.template](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/deploy/docker/pgbouncer-school-capacity.ini.template:1) | 参数化核心／教师池、各池reserve与`max_client_conn`；池大小变化同步模板渲染。保留事务池。 |
| [kt-school-capacity.conf.template](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/deploy/nginx/kt-school-capacity.conf.template:39) | 保留登录、启动、合计三层保护，速率和burst从候选配置读取；保存及采集请求不纳入登录／启动保护。保留429类型及请求关联日志。 |

v3必须保留的关系：

```text
登录并发 ≥ 1
启动并发 ≥ 1
max(登录并发, 启动并发) ≤ 合计并发 < 入口worker
1 ≤ 激活并发 ≤ min(启动并发, 入口worker, 入口数据库池)
后台池 ≥ 2 × 统计消费者数
交卷池 ≥ 交卷消费者数
```

入口worker与数据库池不要求一比一。保存、采集worker也允许多于数据库连接，以便测量排队到底发生在哪一层。

数据库预算统一计算：

```text
全部普通池
+ 全部reserve池
+ 直连任务
+ 监测连接
+ 维护连接
+ 实际配置切换重叠连接
≤ max_connections − PostgreSQL保留连接
```

本轮采用排空后切换，重叠预算设为0并验证不存在旧池残留。初始数据库保留连接3、直连1、监测2、维护2；需求增加时同步记录。需要突破64时，按完整预算向上取整到8的倍数，不单独改环境变量而漏改数据库启动参数。

PgBouncer客户端额度按实际客户端进程和心跳连接预算配置，初始至少200，扩容时重新计算，同时检查文件描述符额度。客户端额度与PostgreSQL后端连接数分别核算。[PgBouncer配置说明](https://www.pgbouncer.org/config.html)

### B．将固定矩阵改为实测驱动的候选队列

| 文件与定位 | 明确修改 |
|---|---|
| [prepare_student_capacity_remote.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/scripts/load/prepare_student_capacity_remote.py:11)，`ROOT/PROJECT/PORT`、`variants()`、`main()` | 从本轮运行清单读取项目、目录、端口、源数据和R0；取消累计生成固定W8/W12/E19候选的逻辑。所有资源必须属于新项目，拒绝挂载原实验卷或生产卷。 |
| [build_student_capacity_remote.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/scripts/load/build_student_capacity_remote.py:28)，`main()` | 使用本轮源码、前端产物和唯一镜像标签；不再固定构建旧v1标签或启动`compose-B.json`。 |
| [run_student_capacity_matrix.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/scripts/load/run_student_capacity_matrix.py:130)，`parameters()`、`modified()` | 完整记录worker、所有池、reserve、数据库总连接、CPU/cpuset、内存、入口保护及连接复用。修改函数必须同步所有HTTP角色、后台副本、Nginx、PgBouncer和数据库启动命令，不能继续跳过`db`。 |
| 同文件，`execute()`约355行 | 用“恢复基准＋准备场景”替代每轮重新seed180人；支持外部协议生成器与两个浏览器的统一就绪屏障、独立记录和合并分母。 |
| 同文件，`stage()`约513行及`run()`约535行 | 取消自动3人压力单元及240／360人分支；压力单元只接受50／180。取消固定候选白名单，按下节规则追加候选。 |
| 同文件，`select_candidate()`、最终输出 | 候选选择同时检查50和180人，不只看180人的单个最快指标；不合格的探索候选不能标为验收配置。最终通过字段由实际复测结果生成，不能继续固定输出`formal_capacity_accepted=False`。 |

本轮新增专用基准准备命令：

`E:\ATOP\kt-data-platform-complete\.worktrees\prepared-paper-assessment\server\apps\assessment\management\commands\prepare_student_capacity_baseline.py`

职责仅为：验证旧fixture及隔离库身份、复用原账户和题目、创建本轮任务、导出冻结映射及基准清单。数据库备份／恢复由远程准备和矩阵脚本执行，命令不删除旧作答。

时间预算拆分：

- 准备阶段最多300秒；未就绪则不释放负载。
- 完整持续作答阶段600秒。
- 最后保存与持久受理最多60秒，超出业务门槛仍记失败。
- 后台处理及排空最多300秒。
- 统计观察最多300秒；每名学生仍按自己的“终态且材料到齐”时间判断是否超过300秒。
- 观察器覆盖以上全过程并额外预留60秒，不再固定940秒。

### C．协议对齐修复后的加载与保存行为

主要修改：

[student_capacity_protocol.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/scripts/load/student_capacity_protocol.py:22)

发布新版本`student-capacity-protocol-v3`，旧协议文件及旧结果不覆盖。

- `select_roster()`：接收完整50／180人名单和驱动分配；协议子集允许48／178人，但必须与2名浏览器账户合并后恰好等于总人数。
- `build_plan()`：区分`comparison-2q`和`paper-20q`，后者读取全部20题及冻结信心分配。
- 覆盖`Actor.login()`：按最新登录页面实际请求链模拟CSRF、登录响应上下文、课程列表和任务列表。不能继续只调用登录与任务API，也不能重新加回已移除的重复bootstrap。
- `Actor.install()/enter()`：保留有效初始租约，按当前浏览器逻辑恢复失效租约；不额外获取已经随进入回执返回的租约。
- 覆盖保存调度：与当前前端**100ms静默保存、250ms最长等待、单个在途请求＋一个待发送最新指针**对齐。保留全部原始事件，仅合并未发送指针。
- 已发送但结果未知的请求保留原操作身份，按现有恢复契约核验，不能新造提交或事件令牌重试。
- 交卷复用现有整卷流程，测量从确认提交开始，覆盖最后保存、采集收尾、202受理和后台完成。
- 不调用已退役的逐题submit、skip及旧finish执行路径。

协议契约对照源为：

- [StudentAssessment.vue](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/client/src/assessment-runtime/StudentAssessment.vue:503)的保存与交卷调度。
- [draft-save-queue.ts](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/client/src/assessment-runtime/draft-save-queue.ts:1)的在途请求和待发送指针规则。

本轮不为了通过压测改变这些产品行为；如测出具体业务缺陷，先形成独立修复、更新源码版本，再重新测量相关单元。

### D．修正数据核对与两个浏览器的接入

| 文件 | 修改 |
|---|---|
| [capacity_reconciliation.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/apps/assessment/capacity_reconciliation.py:364)，`PROTOCOL_VERSIONS`、`adapt_inputs()`及证据核对 | 接受v3与双驱动记录；移除v3“信心全部为空”和“动作数等于保存请求数”的假设。分别核对原始事件、合并指针、信心、正式提交、统计版本与导出。旧版本核对规则保留。 |
| [verify_student_capacity_load.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/apps/assessment/management/commands/verify_student_capacity_load.py:18) | 接收协议和浏览器证据，输出总分母、各驱动分母、缺失动作、确认事件及最终状态；禁止仅凭协议部分完成判整个单元通过。 |
| [student-concurrency-two-page.mjs](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/client/e2e/student-concurrency-two-page.mjs:45)，`validateTwoPageFixture()`、`runAnswerFlow()`、`runTwoPageWitness()` | 将固定18082端口改为经过本轮清单校验的目标；支持20题、600秒固定轨迹、信心输入、统一开始屏障及与协议一致的指标记录。 |
| [student-capacity-visual.mjs](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/client/e2e/student-capacity-visual.mjs:90)，`runCapacityVisual()` | 按总名单分配桌面／移动账户；输出浏览器功能结果和实测耗时。教师页面核验放在学生压力阶段后，统计后台始终运行。 |

必须核对：A→空→B→A、同内容不同事件头、信心0与缺失、未访问未作答、人工待评分、刷新后跨时钟质量降级。不能为了“完整率”把估计时间标成精确，也不能把未答题补成0分或伪造事件。

## 四、负载场景与配置调整规则

### 1．负载场景

| 场景 | 用户分配与用途 |
|---|---|
| 登录及列表加载 | 全体同时登录，覆盖修复后的页面加载链 |
| 集中启动 | 全体预登录后同时开始，测首题就绪 |
| 登录＋持续作答 | 50人为34＋16；180人为120＋60 |
| 启动＋持续作答 | 同上 |
| 登录＋启动＋持续作答 | 50人为17＋17＋16；180人为60＋60＋60，验证合计并发保护 |
| 全员持续作答 | 全部用户作答600秒，最后整卷提交，验证50／180名保存用户容量 |
| 完整流程 | 全体登录→列表→启动→20题作答→确认交卷→完成 |
| 集中交卷 | 准备阶段完成材料，全体同时确认提交 |
| 交卷／统计＋持续作答 | 50人为34名交卷＋16名作答；180人为120＋60，检查后台抢占 |

两题对照沿用原15次、每2秒一次的逻辑轨迹，仅用于比较，不代替整卷验收。

20题持续作答采用每题30秒、总600秒的固定计划；每题在第1、3、5……29秒产生计划答案变化。文本题包含固定的短输入突发、清空和返回旧内容，以检验保存合并。信心只在服务器分配要求时填写，覆盖0、非零值和允许的明确缺失。

所有原始动作在执行前冻结。网络慢、保存慢或切题慢时，不把后续计划时间整体顺延，也不静默丢弃动作；记录计划时间、实际动作时间、发送时间和确认时间。

正常容量单元不人为断网。刷新、短时离线、响应丢失和交卷取消放在单独标记的恢复单元，仍保持总人数50／180，避免把故障注入结果混入正常SLA。

### 2．按瓶颈调整，不预先枚举最大档位

每个新候选先测50人，再测180人相关场景。性能失败不自动阻止下一档；数据错误和资源安全失败立即停止。

| 实测现象 | 下一步配置动作 |
|---|---|
| 进入Gunicorn前排队明显，CPU仍有余量、池等待较少 | 只增加对应HTTP worker，初始步长为当前值的25%，向上取整且至少增加1 |
| worker增加后PgBouncer等待上升，数据库CPU／锁／提交尚有余量 | 保持worker，增加对应普通池，先测与worker相匹配的额度；同步总连接预算 |
| capture队列或确认延迟主导 | 独立调整capture worker和池，不能只扩大assessment |
| 登录主要耗在密码校验，CPU持续饱和 | 不继续盲目加worker；测收紧登录准入对尾延迟及作答隔离的影响 |
| 入口429较多，但应用和数据库有余量 | 分别提高登录、启动或合计并发，每次增加约25%；保持轻量读取至少1个worker余量 |
| 启动主要被burst拒绝 | 先测burst 8→16；仍由突发拒绝主导再倍增。速率64仅在持续速率拒绝主导时提高 |
| 激活事务保护形成等待，数据库有余量 | 联动提高启动并发、激活名额及必要的入口池；记录这是耦合变更 |
| 交卷队列等待主导、单份处理时间稳定 | 消费者与专用池1→2，随后按约25%增加，不以4为上限 |
| 统计超过300秒且前台未受资源限制 | 增加统计消费者，并保持每消费者至少2个后台连接 |
| SQL锁、WAL或提交耗时随并行显著恶化 | 回退该候选，降低对应并行或保留较小池；转入具体事务／查询诊断 |
| 连接建立占据主要耗时 | 单独比较0秒与60秒复用；候选必须通过重启恢复验证才能进入正式验收 |

Nginx登录、启动与合计保护限制的是处理中请求，不是学生总人数；不能把“并发12”解释成系统只能服务12名学生。[Nginx说明](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html)

候选比较采用相同快照、名单、轨迹与代码。观察到改善后补一次反向顺序对照，防止缓存、背景负载或运行顺序造成假收益。以相关SLA是否达标、P95及完成吞吐判断，不以CPU越高越好作为目标。

连续两次增大同一维度仍无可重复收益时，停止沿该维度扩大，检查其他瓶颈。这是搜索停止规则，不是数值上限。所有维度均无有效候选时，交付未达标结论和具体瓶颈，不无限扩容。

### 3．CPU、内存与连接总量联动

- 默认所有被测服务可使用全部有效逻辑CPU，不添加固定CPU quota或cpuset。
- 不把worker数限制为CPU核数；由CPU时间、等待时间和内存实测决定是否需要更多进程。
- 检查父cgroup是否仍有更小配额。`cpus: 0`本身不能证明整个执行链无配额。
- 内存限制随实测进程峰值和新增worker估算调整，不能让旧3／4GiB容器限制成为未记录的瓶颈。
- 增加数据库连接前评估数据库实际内存与并发查询成本；同时改变实际启动参数并重启新副本数据库，核验`SHOW max_connections`。PostgreSQL该参数需要启动时生效，并会影响资源分配。[PostgreSQL说明](https://www.postgresql.org/docs/17/runtime-config-connection.html)
- 统计和交卷扩副本时记录总CPU权重，避免副本数变化无意中成倍增加后台调度权重。
- 不调整生产或其他项目的CPU归属与进程。

## 五、监测、测试与最终交付

### 1．监测修改与证据

修改[observe_school_runtime.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/scripts/load/observe_school_runtime.py:80)中的：

- `observer_http_roles()/observer_required_roles()`：识别v3和实际副本。
- `capacity_runtime_budget()`：取消64、1/2/4等固定判定，按候选清单核对。
- `cgroup_metrics()`：记录有效cpuset、CPU quota、节流、内存及父级约束。
- `pool_metrics()`：核对实际普通池、reserve、客户端数和等待。
- 将“心跳时间”和“完整有效样本时间”分开，继续拒绝用心跳掩盖缺失监测。

[collect_student_capacity_logs.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/scripts/load/collect_student_capacity_logs.py:50)改为读取本轮项目身份，复用已修复的容器生命周期处理，不重新实现一套采集器。

每个单元至少保存：

- 计划输入、实际动作、请求、持久确认及客户端积压。
- Nginx入口拒绝、进入应用前等待、WSGI耗时、密码校验、建连、SQL／锁／提交耗时。
- 每池连接使用及等待、各服务CPU、CPU节流、内存、I/O与数据库等待事件。
- 交卷队列年龄、执行时间、统计来源revision及更新时间。
- 本地生成器CPU、内存、实际释放偏差及SSH隧道错误。

通过request_id／操作身份逐请求关联时间；不通过相减两个独立P95推算排队。

安全停止条件：数据不一致、重复正式提交、OOM、非预期服务重启、完整监测超过30秒未更新，或连续三个15秒样本可用内存低于`max(2GiB, 总内存15%)`。停止新增负载并保留现场；已受理任务保留恢复能力，不删除数据库。

### 2．代码验证清单

先为实际行为修改补失败测试，再实现：

- 配置：32以上worker、12以上保存池、4以上交卷消费者、128以上数据库连接可通过一致性校验；负数、预算超额、激活超配仍失败。
- 配置传播：Compose、Django、PgBouncer、PostgreSQL、Nginx和实际副本一致。
- 数据复用：恢复后名单、题目内容、分配和起始状态一致；原项目资源不能成为恢复目标。
- 矩阵：只生成50／180人压力单元；48＋2／178＋2无重复账户；600秒正常作答不被旧超时提前终止。
- 协议：100／250ms调度、合并待发指针、不丢原始事件、未知结果不换身份、20题与信心绑定正确。
- 核对：合并保存不会被误判为缺动作；缺事件、错误最终指针、信心错绑和统计旧版本必须失败。
- 监测：正常配置替换可继续采集；真实采样超时、日志覆盖缺失必须终止资格判定。

对应补充既有测试：

`server/tests/test_student_capacity_deployment.py`、`server/scripts/load/test_student_capacity_matrix.py`、`test_student_capacity_protocol.py`、`test_student_capacity_logs.py`、`test_student_connection_recovery.py`、`server/apps/experiments/test_capacity_observer.py`、`server/apps/assessment/test_capacity_reconciliation.py`及两个浏览器脚本的测试。

新基准命令增加`test_capacity_baseline.py`，验证隔离边界、旧新映射和重复恢复。

最终代码版本做一次后端完整回归、PostgreSQL并发检查及规定的前端lint／类型／单测／API／构建检查；仅调整运行参数时执行配置、健康、资源预算和相关压力检查，不反复跑未变化的全套单测。

连接复用若进入候选，修改[verify_student_connection_recovery.py](/E:/ATOP/kt-data-platform-complete/.worktrees/prepared-paper-assessment/server/scripts/load/verify_student_connection_recovery.py:27)，使用本轮身份和实际连接预算，保留固定HTTP进程证据，验证PgBouncer及PostgreSQL重启后的首次请求。任何未解释的500均淘汰复用候选，不自动重试写入掩盖失败。

### 3．执行顺序与完成条件

1. 保存计划，冻结修复后源码与构建，准备数据副本、运行清单和监测。
2. 完成定向测试及两个浏览器的功能预检；这属于功能检查，不建立第三种压力人数。
3. R0运行50／180人两题对照，随后运行完整场景定位当前瓶颈。
4. 按上述规则调整相关参数；每个候选保留变更理由、前后配置和失败结果。
5. 找到全部场景达标的配置后冻结，进行**两轮独立正式验收**：9个场景×2个人数×2轮，共36个单元，均采用48／178协议用户加两个浏览器。
6. 任一正式单元失败，保留结果并返回对应瓶颈处理；不得只挑通过的一轮作为最终结论。
7. 全部通过后归档有效配置、源码／镜像摘要、基准快照摘要、逐单元数据核对、分层延迟、资源峰值及两条浏览器证据。

最终交付明确给出：入口、保存、采集、交卷、统计各自worker与池大小，实际可用CPU与配额，PostgreSQL总连接及余量，登录／启动／合计保护、速率／burst、连接复用状态，以及50／180人的验收结果。

**通过结论限定为：冻结配置和本轮数据、负载条件下，50／180名混合驱动用户达标。不能外推为180个真实浏览器、实体手机或校园网络均已验证。**

## 实施进度

- [x] v3容量配置与部署预算。
- [x] 基准副本与恢复工具。
- [x] v3协议、信心和数据核对。
- [x] 矩阵、监测与双浏览器集成。
- [x] 本地回归与隔离部署。
- [ ] 50／180人调参及独立验收。

2026-10-17按用户要求撤销追加的批量保存与切题优化，返回本计划。代码基线为优化前R5，测量工具保留原计划的measurement-v3c1修正；此前prepared-paper功能、基准快照及原计划实验记录保留。当前暂停压测，未启动新的调参或正式验收。

2026-10-17接续实施计划：[R5版本50／180人容量调参与完整指标验收](2026-10-17-student-capacity-resume-50-180.md)。
