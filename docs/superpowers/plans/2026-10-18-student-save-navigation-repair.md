# 学生切题、草稿保存与整卷提交修复实施计划

批准日期：2026-10-18。基线：4dd346dcb736755ddbcf4539854a349f2a60289b。
工作区：E:\ATOP\kt-data-platform-complete\.worktrees\prepared-paper-assessment。

## 已批准目标与边界

修复事件积压、旧保存恢复、切题及交卷等待；新建会话采用稳定输入采集合约。
停止输入1000ms后生成完整答案状态并保存；失焦、切题、交卷强制固化已确认输入。
连续输入只更新可覆盖的本地候选，不逐键追加研究状态或发送保存请求，不做周期研究快照。
已有会话（包括已预生成未打开的会话）保留绑定合约，历史事实不合并、删除或重新编号。
IndexedDB kt-process-events-v1 v2、事件ID/序列/父链、checkpoint、租约、版本、幂等、授权、
截止时间、评分、反馈投影、Coverage和导出保持一致。禁止改生产数据、自动提交、开分支、PR或部署。

## 任务与验收

- [x] T0 保存接续信息，检查8个原有dirty文件；只读诊断test1-test8在2026-10-17
  21:25-21:48 Asia/Shanghai的session1084/attempt27209。400/409有历史证据；确切业务码、
  生产后端SHA已核验，前端build/缓存版本及worker执行存活未证实；不能根据响应长度猜测原因。
- [x] T1 session-controller.freezeNavigationSnapshot恢复backlog和too_large；首次发送前固定边界，
  按事件/checkpoint/完整请求字节预算分批确认后重冻；导航点击token、时间、离题事件不重建。
  已冻结未知请求不可变。单项超限capture_item_too_large；全部材料被旧意图占用时有限返回
  capture_drain_blocked，控制器恢复拥有者。测试99/100/101/250条、跨题、字节、checkpoint、
  部分/重复回执、中途失败、无进展、未知请求精确恢复。不能调大100上限或删队列。
- [x] T2 服务端通过外层锁事务/内层savepoint保存确定拒绝终态，使用CaptureMutationReceipt JSON；
  证明绑定operation、attempt和材料指纹；事务提交异常为unknown。客户端committed/rejected/
  unknown/blocked四类处理；只有匹配拒绝才释放占用，保留事件。缺材料先补传，缺本地事实则停止。
  修复只比较target_event_id复用回执：完整比较answer/target/inline_confidence，区分缺省与清空。
  测试响应丢失、重载拒绝、伪造证明、null->50->100、版本/租约冲突与提交异常。
- [x] T3 新artifact capture-v2-stable-input-v1，旧V2哈希不改。参数stable_state_idle_ms1000、
  text_state_policy stable_input、text_input_operations burst_summary、无周期快照。
  普通与prepared创建统一选择；已有合约不切换。runtime增加text_capture_policy。
  capture-v2能力判断、transition-entry、Coverage支持新旧；导出保留真实artifact/hash。
  测试旧哈希/旧session、新建两条路径、bundle/lease/navigation、Coverage/导出可区分。
- [x] T4 stable-text-input.ts控制器负责1000ms稳定、IME、flush与dispose；ResponseTrajectory继续
  负责事实/父链/哈希/落盘。新合约beforeinput不逐键operation，稳定段最多一摘要+一transition。
  本地候选使用metadata单行，250ms合并写、generation/head/identity/composition校验；失败不谎报。
  顺序：确认输入->固化答案->trajectory.settled->固化信心->指针队列->冻结->请求->回执。
  blur/click/submit single-flight；组合未确认不当答案；hidden/pagehide保全候选；空值及A-B-A保留。
  选择题逐次事实不改。旧合约仍逐次。信心blur/Enter确认并在切题/交卷显式await；真正确认事件保留。
- [x] T5 导航完整冻结inline_confidence；服务端新指纹版本包含信心，兼容旧回执原公式。
  draft/leave/enter/finish/recovery传输30s，前台等待60s，3s展示阶段；未知保留原意图。
  429遵守Retry-After、离线无快速重试。状态区分本机保存、服务器确认、受理、后台、待核验、冲突、失败。
  submit独立operation ID与阶段耗时，日志不含答案/姓名/凭据。
- [x] T6 全题expected_versions、提交token/点击时间/manifest先持久化且不可变；202仅受理。
  刷新恢复原任务；worker queued/running/completed/failed、存活、队列年龄准确，保留租约/重试/事务。
  preparation实际queued/paused/retrying/exhausted，耗尽不能反复点击重置，不重抽不重启计时。
- [x] T7 正确性通过后，select_response_target祖先检查PostgreSQL递归查询，限定attempt/type并防环；
  active=null免无用遍历，锁/版本/分支校验保留。链1/100/1000查询预算及无效分支测试。

## 验证约定

每任务先新增失败用例并实际运行，再最小实现、目标测试、diff检查。
Python仅本工作区.venv\Scripts\python.exe；Node24.20.0与PostgreSQL17.11使用用户指定portable路径。
PG127.0.0.1:55432 kt_user/kt_password，kt_platform开发，kt_e2e隔离E2E；不得用SQLite验证锁/并发。
Windows运行Python全套测试时设`$env:PYTHONUTF8='1'`（配置文件含中文），可设PYTHONUNBUFFERED=1及时落日志。
不重试Docker/WSL，不对开发或生产执行E2E reset/seed。
前端定向：session-controller、process-sender、StudentAssessment、response-trajectory、api、
stable-text-input、offline-cache、capture-initialization。后端定向：capture_bundle、saved_navigation、
capture_v2、close_capture_bundle、session_submission及worker/processes、合约/Coverage/查询预算。
阶段检查：npm ci/lint/typecheck/test:unit/api:check/build；Django check、makemigrations --check --dry-run、
collectstatic --noinput，以及最终一次完整后端回归。E2E Chromium/WebKit，真实iPhone另行实测。
视觉1440x900、390x844、200%缩放、焦点与错误恢复。未运行项逐项保留。
性能固定设备/RTT/带宽/题数/长度/并发，比较稳定到确认、切题分段、交卷受理/排队/处理。
候选目标：100ms忙碌反馈，正常无积压切题P95<=1s，稳定落盘到服务器P95<=1.5s，
材料已确认finish受理P95<=1s；未测不得声称达到。

## 已完成

- 2026-10-18：核对HEAD与8个dirty文件，保留信心/Service Worker/多会话bundle原有修改。
- 读取仓库AGENTS、kt-research-ui和研究约束；执行采用TDD及分批验收。

## 当前任务

- T0–T7代码与本地自动化验证已完成：前端831项；后端完整清单2471项已覆盖；双浏览器、
  20题往返、视觉及查询对照完成。业务源码已冻结，未提交或部署。
  真实iPhone与受控网络性能验收仍未完成；本地完成状态不代表生产验收。

## 实际验证结果

- 初始git status/HEAD检查退出0，基线与批准计划一致。
- T1新增6用例RED复现超限/空转/单项超限，修复后127项sender/controller测试通过；
  另补导航checkpoint/字节预算RED->GREEN，以及250条分批不改token。
- T2后端104项PostgreSQL定向回归通过（42.610s）；覆盖COMMIT失败、响应丢失、拒绝证明、
  旧指纹、分支与祖先链。客户端69项测试通过，包含IndexedDB同事务CAS实现与Memory模型测试。
- T3/T6后端121项PostgreSQL回归通过（173.733s），含独立worker崩溃恢复；额外10项合约/导出/API、
  8项合约分配开关，以及8项连接观测/池化排除测试通过。各批次有重叠，不合计为独立测试总数。
- T4控制器先RED再实现，8项测试通过；页面1秒稳定测试RED显示逐字2次transition，修复后通过。
  另覆盖写入期间新输入、强制固化最新generation、旧落盘不得提前保存新输入、paste来源。
- `npm ci`退出0；`npm run lint`退出0（2条原有WorkspaceShell测试警告）；
  `npm run typecheck`与`npm run build`通过；`npm run api:check`通过。
- 最终前端全量：`npm run test:unit`，109个文件、831项通过，退出0，
  日志`.tmp/student-repair-frontend-full-verified.log`。包含返回前台重启稳定窗口与跨题旧操作恢复。
  补修后`npm run lint`与`npm run build`（含vue-tsc）均退出0，仍仅2条原有lint警告。
- Django check与makemigrations --check --dry-run退出0；spectacular --validate为0错误、17条枚举命名警告；
  contracts/openapi.yaml及生成TS已同步；collectstatic退出0。没有model/migration变更。
- 后端全量必须从server目录发现；一次仓库根目录启动发现0项，不作为验收，已在server目录重新运行。
- 浏览器新用例RED：旧构建输入`stable `产生6次transition，新用例正确失败。
  新构建Chromium稳定输入/切题/整卷完成及130次离线积压/响应丢失恢复已通过。
- Chromium 20题完整往返通过：20题、38条导航回执、1次整卷提交，Q6最终文本仅1条稳定transition。
  同次本地功能回归的38个navigation阶段观测范围420–654ms、经验P95约606ms；
  未固定RTT/带宽/系统负载且无匹配修复前成功对照，仅作诊断，不作为性能目标验收。
- 1440×900、390×844、200%截图已人工检查，输入区、导航、提交按钮与焦点边框可见，无重叠。
  最新截图和安全阶段耗时保存于`client/.e2e-results/capture-repair-verified/`，20题证据在`capture-repair-final/`。
- 全量后端第一次发现新合约反馈曝光投影缺漏，已修复为统一V2能力判断，4项API用例串行通过。
  后续keepdb重跑出现账号测试残留数据；该用例在全新库单独通过，故改用全新隔离库重跑全部2471项。
- 全新库第一次全量被Windows默认GBK读取中文Nginx配置中断，并行异常不可序列化引起后续隔离清理连锁失败；
  设PYTHONUTF8=1后，`manage.py test apps.experiments.test_school_capture_priority apps.experiments.test_school_runtime tests.test_frontend_gateway --noinput`
  27项通过、退出0，无需修改这些无关模块。最终全量使用新库`test_kt_full_repair_utf8`及UTF-8模式。
- UTF-8全量命令退出1，原因是现有`NativeSchoolProbeTests(unittest.IsolatedAsyncioTestCase)`的
  `_contextvars.Context`不能由Windows multiprocessing序列化，并非业务断言失败。
  精确比对全部发现ID与日志已完成ID：2471项=2270项已完成+201项未执行，无重叠、无遗漏。
  清单`.tmp/student-repair-backend-partitions.json`；仅剩余201项以串行DiscoverRunner在全新库
  `test_kt_repair_remaining_utf8`补跑，日志`.tmp/student-repair-backend-remaining.log`。
  命令：`.venv/Scripts/python.exe .tmp/student-repair-complete-regression.py`，仍使用PYTHONUTF8=1。
- 串行补跑结果：201项、159.004s、OK、退出0，测试库正常销毁。核对日志中的201个测试ID
  与剩余清单完全一致；与2270个已完成ID无重叠，合计2471项全部覆盖。
  最终汇总2432项通过、39项环境跳过：35项需要独立Redis，2项需要独立POSIX发布用户，
  2项需要专用PostgreSQL协议验证数据库。该39项不能计作已验证；最终日志对账补入此前尚未汇总的3项。
  并行命令本身退出1，完整覆盖由其已完成部分及串行补跑共同组成，不能记成单命令退出0。
- 旧合约五题浏览器用例发现前题工具保存响应后跳转、本地ACK未落盘，当前题恢复遗漏前题拥有者；
  已先RED复现后修复，73项定向测试通过：同会话旧操作先精确重放、完整ACK后释放，当前题CAS不变。
  未发送旧草稿缺原租约时明确blocked，禁止借用当前题租约；未知/不完整回执保留原意图及事实。
  IndexedDB仅新增metadata读取API，数据库仍为v2。浏览器最终复测通过。
- 最终E2E：`playwright test --config playwright.capture-v2.config.ts --grep 'stable input records|navigation drains|five short-response' --output .e2e-results/capture-repair-verified`，
  退出0，5项通过、1项按设计跳过。Chromium旧合约五题35.9s、积压22.7s、稳定17.7s；
  WebKit积压45.1s、稳定25.5s。跳过项为依赖Chromium CDP的原IME旅程，不能代替真实iPhone。
  丢响应恢复验证相同navigation token及完整请求SHA256，数据库逻辑回执计数一致。
  整卷成功submit阶段记录已跨硬跳转保留并断言非空（Chromium约2.58s、WebKit约5.53s，非finish受理耗时）。
- T7同数据对照：`.venv/Scripts/python.exe .tmp/student-repair-selector-benchmark.py`在独立
  `test_kt_repair_selector_probe`上执行1项通过、退出0，完成后销毁测试库。取HEAD原函数与工作区函数，
  相同1000条合法链，长度1/100/1000、active空/首项，每种3次。长度1000：active空时SQL从1000降为1，
  中位耗时1338.950ms→1.198ms；active首项时SQL从1000降为2，1229.780ms→311.164ms。
  日志`.tmp/student-repair-selector-benchmark.log`；这是本地选择器测量，不是端点/生产P95，
  SQL次数有界不代表递归计算耗时恒定。探针首次因Windows解码git输出失败，指定UTF-8后完成。
- 最终git diff --check退出0。原有8项dirty保留；执行期间另有AGENTS及研究UI skill文档并发修改，
  不是本轮修复产物，保留且不覆盖。没有提交、分支、PR、部署或生产数据变更。

### 生产只读核验（2026-10-18）

- web/capture/assessment容器revision标签均为4dd346dcb736755ddbcf4539854a349f2a60289b；
  容器运行正常，submission/publication worker容器存在。此事实不证明每个任务都在处理。
- test1–test8会话映射已读取；截图确认test7/session1084/attempt27209；固定时段72次capture400、
  18次draft409；该题response_version0、active_response_event_id空、draft_saved_at空，只有3条入题事件。
- 旧访问日志未存业务错误码；不能把400进一步归因为invalid_event_order或混包。
- 生产经PgBouncer连接数据库。新接口在池化路径返回worker.status=unknown，不能使用池后端tag冒充worker存活。
  直接PostgreSQL环境可观察DB连接，仍不等同执行进度；队列年龄和任务状态可分别核验。
- 没有修改生产数据、重排任务或部署。密码仅在交互进程内存使用，进程已关闭。

### 接续与发布注意事项

- 新增CAPTURE_STABLE_INPUT_ENABLED（默认1）；设0仅停止新建会话绑定稳定合约，既有会话继续按绑定合约解释。
  如后续授权分阶段部署，先以0部署双合约支持，再启用1；回退不可使用不认识新合约的旧程序。
- Prepared试卷本来就要求V2传输；统一选择器以require_capture_v2=True保留该约束；普通入口仍受原V2允许名单控制。
- 新合约Coverage使用coverage-v4/recording-coverage-v4，携带真实合约名称和哈希；旧范围维持v3结果。
- 主要新增文件：stable-text-input.ts、operation-budget.ts、mutation-material.ts与测试；后端新增
  test_stable_input_contract.py/test_response_target_ancestry.py。其余均为限定既有链路修改。

## 尚未验证

- 真实iPhone Safari（IME/挂起/返回/交卷）未运行，当前工具无真实iOS设备。
- 没有匹配设备/RTT/带宽/并发条件的修复前后成功延迟对照，不宣称任何候选P95目标达标。
- 生产前端资源build与截图浏览器缓存版本尚未独立核对；容器SHA不替代该证据。

## 本地归档授权（2026-10-18）

- 用户在上述实现与验证完成后，明确要求将本轮修复提交到本地 main；并同意将 AGENTS.md
  及研究 UI skill 的 6 个既有规则文档修改另作一条本地提交，再创建新工作树开展教师操作与容量优化。
- 该授权替代本文实施期间“不提交、不开分支”的限制；不包含远程推送或生产部署。
- 本轮业务代码不再修改。已有验证记录沿用，不将本次 Git 归档描述为重新运行全部测试。
- 后续方案以用户提供的《KT_Plantform_并发容量与教师操作审计.docx》和
  《KT_Plantform_并发容量与教师操作_Codex任务说明.txt》为依据，在新工作树独立保存。
