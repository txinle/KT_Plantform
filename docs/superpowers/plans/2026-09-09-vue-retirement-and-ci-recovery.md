# Vue 业务界面迁移收尾与 CI 恢复计划

日期：2026-09-09。项目树：`E:\ATOP\kt-data-platform-complete`。

## 1. 目标与执行边界

最终目标是：师生业务界面由独立构建的 Vue 3 + TypeScript + Vite 提供，Django 5.2 + DRF 继续承担认证、权限、业务规则、事务、数据与文件服务；逐模块删除已退出使用的 Django 业务模板、页面渲染分支及其专用静态资源，同时恢复 GitHub CI。

本计划采用顺序推进：**先修复验证环境，再按业务模块补齐替代路径、验证、删除旧界面，最后收敛入口和开关。** 不把“Vue 页面已经存在”视为迁移完成，不通过删除测试、放宽业务断言或延长超时掩盖问题。

执行约束：

- 在当前项目树修改，不新建分支、不切换工作树，不覆盖其他人的未提交修改。
- 每阶段开始检查实际 diff、已有结果和未完成项，已验证的步骤不重复执行。
- 本计划包含完整迁移顺序；按阶段验收，不一次删除整个 `templates/` 或 `static/`。
- 代码修改、本地验证、提交推送、生产部署是分别记录的状态；提交、推送和部署须按用户明确授权执行。
- 迁移本身不删除题库、实验、学生、会话或采集数据，不修改现有数据库记录的采集契约。
- 不新增业务功能、不重做 KC 图、随机化、评分或元认知指标，不升级依赖。
- 阶段中遇到缺少必要 API、权限语义无法保持或历史会话不能续答时，先补齐当前模块的缺口；保留该模块旧入口，暂停其删除，其他已验证模块可以继续。

必须保留的后端能力：

- 原业务 URL 和必要的兼容地址；API、下载、健康检查、`/admin/` 不被前端兜底接管。
- Session、CSRF、角色与课程授权；匿名、越权、过期任务等响应边界。
- A+ 草稿、确定性发布预检、原子提交、Experiment × Class 根、参与者快照和排期版本。
- 服务端 DeadlineDecisionService、正式提交幂等性、响应丢失恢复、服务端反馈裁剪。
- 发布题目与 KC 快照、Capture V1/V2 固定契约、Coverage 和导出血缘。
- 离线 Worker 的 URL/scope、IndexedDB、待上传队列和旧版本仍引用的哈希资源。

## 2. 已核实的起点

基线提交：`a6e1463`。检查前 `main` 工作树干净。用户要求先完成计划时，已有下列 **13 个文件的修复草稿**，共约 84 行新增、22 行删除；尚未运行修复后测试，没有删除旧页面，没有提交、推送或部署：

- `.github/workflows/ci.yml`
- `frontend/playwright.config.ts`
- `frontend/playwright.capture-v2.config.ts`
- `frontend/playwright.capture-initialization.config.ts`
- `frontend/playwright.submission-repair.config.ts`
- `frontend/e2e/capture-initialization.spec.ts`
- `frontend/e2e/course-kc-lifecycle.spec.ts`
- `frontend/e2e/experiment-visual.spec.ts`
- `frontend/e2e/foundation.spec.ts`
- `frontend/e2e/layout-audit.spec.ts`
- `frontend/e2e/question-readiness.spec.ts`
- `frontend/e2e/student-identity-password.spec.ts`
- `frontend/e2e/submission-repair.spec.ts`

这些草稿属于 P1 待审查项，不视为已完成。计划编写期间暂停进一步代码编辑和测试。

当前架构已有 `frontend/src/app/router.ts`、`route-boundaries.ts`、各模块 routes，及 `apps/api/frontend_entry.py` 的显式 HTML 入口。`README.md` 已说明独立前端运行方式，同时明确旧模块开关和模板尚未物理删除。当前是迁移并存状态。

特别注意：`TeacherCourseSettings.vue` 仍有课程归档、清除预览和执行清除的传统 POST 表单；数据中心现有 E2E 仍验证 Django document form 的范围选择、版本选择和导出确认。此类能力必须单独核查读写契约，不能随模板一并删除。

## 3. CI 失败证据与对应修复

以下来自 GitHub job 日志、Playwright 失败报告/trace 与本地只读 schema 生成比较。#19 状态记录于本次调查最后一次查询：后端和前端已失败，E2E 仍运行；执行 P1 前刷新一次即可。

| CI | 后端 | 前端 | E2E | PostgreSQL 专项 |
| --- | --- | --- | --- | --- |
| [#15](https://github.com/txinle/KT_Plantform/actions/runs/34196291306) | 通过 | 通过 | 2 失败 | 通过 |
| [#16](https://github.com/txinle/KT_Plantform/actions/runs/34220510122) | 通过 | API 类型检查失败 | 14 失败 | 通过 |
| [#17](https://github.com/txinle/KT_Plantform/actions/runs/34234024851) | 通过 | API 类型检查失败 | 17 失败 | 通过 |
| [#18](https://github.com/txinle/KT_Plantform/actions/runs/34258297562) | 1 failure、3 errors | API 类型检查失败 | 27 失败 | 通过 |
| [#19](https://github.com/txinle/KT_Plantform/actions/runs/34261455037) | 同样 4 项失败 | API 类型检查失败 | 查询时未结束 | 通过 |

| 已确认原因 | 修复决策 | 验证证据 |
| --- | --- | --- |
| backend job 未配置 PostgreSQL，实际使用 SQLite；测试实验清理服务明确要求 PostgreSQL | 后端全量 CI 使用 PostgreSQL 17，保留服务端数据库保护和清理测试 | 清理模块测试、后端全量与现有锁/并发专项通过 |
| schema 生成依赖数据库后端。SQLite 为两个时限字段产生 int64/64 位上限，PostgreSQL 为 32 位上限 | schema/types 生成及检查统一 PostgreSQL 环境，不以 SQLite 输出覆盖生产契约 | PostgreSQL 生成结果与仓库一致，`api:check` 通过；检查脚本继续检出真实漂移 |
| 通用 Playwright 自动收集专用 spec，但未启动其 8124/8126 服务或使用了错误 Capture allowlist | 四组独立 CI profile：application、capture-v2、capture-initialization、submission-repair；每组独立数据库/服务 | 列表检查证明每个 spec 有唯一合适入口，随后四组实际运行 |
| 新测试和部分配置写死 `.venv/bin/python`，GitHub setup-python 未创建此路径 | 使用已有的 `PYTHON` → 当前 checkout 本地 `.venv` → PATH python 选择方式；暴露 spawn 错误 | setup-python 风格运行及实际 Django seed 成功，错误不再退化为 `status=null` |
| 导入、KC 操作、数据中心断言落后于当前业务 | 按“导入后可用”“归档/恢复”“学生作答预览”更新，并保留数量、状态、内容等实质断言 | 对应浏览器用例与接口结果一致 |
| Firefox/WebKit 调用 Chromium 专属 CDP；DPR 被误当成 200% 缩放 | 通用布局使用半尺寸逻辑视口验证 200% 等效布局；真实浏览器缩放保留在 Chromium 专项，IME 等确需 CDP 的专项也限定 Chromium并注明范围 | 三浏览器功能通过；等效视口证据不冒称浏览器原生缩放 |
| 发布测试用 `.first()` 选中先前测试创建的零成员班级，页面明确提示“没有可发布参与者” | 显式选定有参与者的 fixture 班级，预检检查业务结果；后续夹具隔离避免依赖执行顺序 | 独立执行与整组执行都通过；空班级拒绝规则保持 |
| 改密测试访问 8011 的旧 Django 页面，trace 标签为“当前密码:”，却精确匹配 Vue 的“当前密码” | 学生上下文继承当前 Vue application profile 的 baseURL；P2 退役旧认证页面 | 改号不改密码、可选提醒、自主改密、教师重置和会话失效完整通过 |
| 初始化专项仍断言初始密码必须 302 跳转改密，违背已批准的非阻断提醒 | 更新为授权会话可正常进入并获取其 Capture header；保留匿名和越权拒绝测试 | 匿名无 header、他人会话无 header、自有会话正常进入同时成立 |

已有的一次 Firefox 登录导航超时不自动归因于同一问题。若修复后的运行再次出现，使用已有 `login-navigation.ts` 的受限判定，查看该次 trace；不添加无条件重试。

## 4. 统一的模块退出流程

每个模块按下面的顺序执行，完成后才能删除该模块旧界面：

1. 列出 URL → Django view → 模板 → Vue route/component → 读/写 API → 消费方测试。只检查该模块及直接依赖。
2. 标出缺口：传统表单提交、缺失 API、缺失权限/错误展示、文件下载、历史兼容入口。
3. 先写会失败的行为测试；复用现有 CI 失败证据修复测试或配置，不为简单配置添加重复实现的测试。
4. 用现有 service 实现缺失 API 或让 Vue 调用现有 API；禁止复制业务逻辑到前端。
5. 让原 HTML URL 提供独立 Vue 入口；保留必须在 Django 页面入口检查的规则，尤其作答归属和 Capture header。
6. 替换旧模板断言，覆盖同样的业务规则、失败反馈与状态持久化。旧文案或模板名称不作为保留旧 UI 的理由。
7. 通过模块后端、Vue 单元与三浏览器关键链路；刷新后重新读取权威 API/UI 状态。
8. 删除该模块旧模板、旧渲染分支、仅服务该模板的代码和测试；共享资产待最后一个消费者退出后删除。
9. 再运行直接受删除影响的检查，更新本计划状态。只重跑受新增修改影响的验证。

传统 POST 的处理必须明确：Vue 仍使用或有已知外部消费者的 POST 保留后端契约；确认仅供已退役 HTML 表单使用且替代 API 已验收的 POST 才可退役，并以测试锁定明确响应，不静默改写提交含义。文件下载可以继续使用 Django 普通 HTTP 端点，无需仅为形式统一改造成 JSON。

### 同批修改前后端与旧表单退役

用户已明确要求同步修改仍依赖 Django 表单的链路。每个模块将 **Vue 交互 + DRF 接口 + 原 service 衔接 + 前后端测试 + 旧 UI 删除** 作为一个交付批次。上述传统 POST 消费者核对是本批次中的迁移步骤，不是把迁移无限期搁置的理由。

| 链路 | 同批前端修改 | 同批后端修改 | 必须同时通过的验证 |
| --- | --- | --- | --- |
| 数据范围选择 | Vue 提交范围，显示服务端确认的范围与错误 | 复用现有范围校验与持久化语义，以 JSON 返回权威范围 | 跨课程/班级越权拒绝、无效组合拒绝、刷新后范围一致 |
| 导出版本与确认 | Vue 提交版本和确认信息，展示任务状态与可用下载链接 | 复用版本授权、确认、任务创建及审计，不把创建任务与实际文件下载混在一个 HTML 响应中 | 版本和范围匹配、身份信息确认、失败反馈、重复请求符合现有幂等契约、下载再次授权 |
| 课程归档 | Vue 显示后端 capability，提交后重新读取状态 | 复用归档权限与事务，返回结果；客户端判断不能替代后端授权 | 协作教师权限、归档后新会话限制、旧数据仍可读 |
| 清除预览与执行 | Vue 显示服务端预览、确认条件与执行结果 | 复用现有受控清除 service、授权、审计及影响范围检查 | 未授权和不满足条件的执行被拒绝；不扩大既有清除范围；仅对隔离测试数据验证 |
| 登录与改密 | Vue 使用现有认证 API，展示结构化校验错误 | 保持 Session/CSRF、密码校验与会话失效；退出纯 HTML 表单入口 | 错误密码、safe next、初始密码可选提醒、教师重置后的旧会话失效 |

优先使用已有 API；只有核实缺失时才在现有 API 模块补充接口。请求/响应契约变更同步更新 PostgreSQL 环境生成的 OpenAPI 和 TypeScript 类型。新链路通过后，同批删除旧模板与无消费者的旧表单渲染代码；存在已知非页面消费者的后端端点可保留，但不得继续保留一套面向用户的重复旧页面。

## 5. 分阶段实施

### P0：冻结起点和完成计划

- [x] 查阅 #15–#19 日志与两类超时的实际 trace，确认主要原因。
- [x] 核对当前独立前端入口、模块路由、旧模板及部分传统 POST 消费者。
- [x] 记录已落盘、未验证的 13 个修复草稿。
- [x] 编写本完整计划。
- [x] 开始执行时核对 `git status`，将新增用户修改与这些草稿区分。

### P1：恢复可复现的 CI 验证基础

范围：上述 13 个文件，以及确有必要的 `frontend/scripts/check-api-types.mjs`、`package.json` 或对应测试。

- [x] 审查现有草稿是否准确对应失败原因；修正遗漏，不重复编辑已正确的内容。
- [x] 后端和前端 schema job 配置 PostgreSQL 17；保留独立 PostgreSQL 合约 job。
- [x] 四个 E2E profile 在独立 runner/数据库运行，`fail-fast: false`；通用 profile 排除专用 spec，专用 spec 明确进入对应 CI job。
- [x] 修正 Python 选择、专用端口、Capture allowlist 和服务重用设置，防止误连已存在但配置不同的服务。
- [x] 上传每个 profile 的 trace、报告和截图，名称唯一；确保点目录下的测试产物没有被漏掉。
- [x] 修复过时断言、错误的页面入口、CDP 调用与班级选择；保留业务验证强度。
- [x] 用 Playwright `--list` 核对归属、浏览器和合理 skip 原因；不能把失败计数减少直接当成覆盖改善。
- [x] 先跑原失败用例，再完整运行四个 profile；共享本地 `kt_e2e` 时必须串行准备/执行，不能运行中 flush。
- [x] 运行后端及前端阶段检查。P1 完成标准是本地对应 CI 入口通过，GitHub 状态另记，不能提前声称远端已恢复。

### P2：认证页面先完成一条退役闭环

原页面：`/login/`、`/password/change/`；旧模板 `templates/registration/login.html`、`password_change.html`。

主要文件：`config/urls.py`、`apps/accounts/views.py`、`apps/api/frontend_entry.py`、`frontend/src/app/{LoginPage,PasswordChangePage,InitialPasswordReminder}.vue`、`auth.ts`、认证 API 和现有测试。

- [x] 核对登录、错误凭证、CSRF 获取、Session 建立、safe next、退出、改密和提醒的 Vue/API 等价覆盖。
- [x] 当前 `StudentPasswordChangeForm` 被 `password_services.py` 用于后端校验，保留；不得因删除模板而删除校验逻辑。
- [x] 将认证 GET/HEAD 页面入口固定到独立 Vue HTML，不再受旧业务界面开关影响；未登录改密和错误角色跳转仍保持明确契约。
- [x] 盘点旧登录/改密 POST 消费者；仅在新 API 闭环通过后决定删除纯 HTML form 提交入口，更新相应测试和调用方。
- [x] 将 `apps/accounts/tests.py` 的登录模板文案测试迁移为 Vue 内容测试与后端入口/API 测试；确保原本有意义的信息仍可见。
- [x] 验证新建学生、修改学号不修改密码、初始密码提醒可跳过、自主改密、教师重置和旧会话失效。
- [x] 完成桌面、移动、CSS 200% 缩放视觉检查，再物理删除两个旧认证模板及其专用渲染分支。

### P3：课程和班级/账号管理

页面范围：课程列表、创建、概览、设置；班级列表/中心/编辑、成员增删转移、复制与导入、学生可见性；旧全局账号管理页面。

主要文件：`apps/accounts/course_views.py`、`course_resource_views.py`、`views.py`、对应 `api/`；Vue 的 `teacher-courses`、`teacher-classes`、`teacher-account-management`。

旧模板批次：`teacher/course_list.html`、`course_form.html`、`course_detail.html`、`course_settings.html`；`course_class_*.html` 中属于管理的页面、`course_student_*.html`、`class_list.html`、`student_list.html`、`student_import.html` 和专属 partial。班级分析页面归 P6，不在此提前删除。

- [x] 对管理页面执行统一退出流程，保留协作教师/管理教师权限差异及服务端 capability。
- [x] 补齐课程归档、清除预览/执行等 Vue 仍提交传统表单的路径；继续调用原授权、审计和清除 service，不扩大删除范围。
- [x] 覆盖停用班级规则、已有账号复用、姓名冲突确认、导入预览/确认、全局密码与课程身份分离。
- [x] 文件模板下载仍由 Django 提供；上传确认继续具备幂等及错误定位。
- [x] 按课程、班级管理、全局账号三个子批次验证后删除旧模板与旧开关分支，不删除整份混合业务 view 文件。

### P4：题库、题目、KC 和共享资源页面

主要文件：`apps/question_bank/{views,course_views}.py`、对应 API；`teacher-question-banks`、`teacher-bank-management` 的 Vue 组件与 routes。

旧模板批次：`teacher/{bank_list,bank_detail,question_edit,concept_list,import_questions,import_kcs,import_question_bank}.html`；`course_bank_*.html`、`course_question_edit.html`、`course_shared_*.html`、旧 dashboard 中题库管理入口。

- [x] 覆盖课程题库和旧独立题库地址的完整 Vue 路由及权限，不把相同显示名称误认为同一资源。
- [x] 验证 Excel/CSV/DOCX 解析、预览、选中确认、重复识别、完整题可用和不完整题有原因的草稿。
- [x] 验证 KC 新建、归档、恢复、题目独立映射和发布库存；保持已有内容及 KC 快照语义。
- [x] 验证题库归档、共享来源和允许的删除条件；继续使用后端引用检查。
- [x] 删除已完成闭环的模板/渲染分支和旧控件断言，保留业务服务与上传/下载端点。

### P5：实验配置与 A+ 发布

主要文件：`apps/experiments/{views,course_views}.py`、对应 API；`teacher-experiments` 下课程及独立实验组件。

旧模板批次：`teacher/experiment_{list,form,detail}.html`、`course_experiment_{list,form,detail,publish_wizard,class_tasks}.html`。结果/会话页面归 P6。

- [x] 对列表、草稿创建/编辑、详情、发布到班级和旧独立实验 URL 执行统一退出流程。
- [x] 保持草稿允许不完整、发布预检完整且确定、提交原子且幂等；不能将预检或随机化迁入 Vue。
- [x] 覆盖空班级拒绝、有成员班级发布、排除名单、排期版本、并发提交、Experiment × Class 根不重建。
- [x] 发布 E2E 显式选择 fixture，保存/预检错误应立即断言，不在业务拒绝后等待不存在的按钮。
- [x] 页面/接口与 PostgreSQL 发布专项通过后删除旧模板与渲染分支。

### P6：教师数据中心、班级分析与学生学习/结果

主要文件：`apps/accounts/{course_views,course_resource_views}.py`、`apps/experiments/course_views.py`、`apps/analytics/api/`、`apps/assessment/views.py` 及其 API；Vue 的 `teacher-data-center`、`teacher-teaching-data`、`teacher-analysis`、`student-courses`、`student-learning`、`student-results`。

旧模板批次：`teacher/data_center/`、`course_data.html`、`course_quality.html`、班级/学生/题目/过程分析页面、`experiment_results.html`、`course_experiment_results.html`、`course_experiment_session_detail.html`；`student/{dashboard,course,learning,learning_experiment,learning_question,learning_forbidden,complete}.html` 和其专属 partial。

- [x] 列出数据范围、版本选择、导出确认、导出任务与下载的所有实际消费者，补齐必要 JSON 写接口，保持受控导出和身份字段边界。
- [x] Vue 从权威 API 读取范围和状态；不从地址栏或本地选项自行推断权限、是否可看成绩及导出可用性。
- [x] 验证“学生作答预览”只展示正式提交对应事实，过程轨迹保留空状态、回退和重复内容；未提交记录不伪装为正式答案。
- [x] 验证原 URL 刷新、兼容地址、课程/班级权限、匿名/越权、反馈关闭时字段在服务端被移除。
- [x] 覆盖导出 manifest/行数与来源一致性；不能仅凭页面或包内自洽声称完整采集。
- [x] 分别完成教师数据中心、教师分析、学生学习结果三个子批次，才删除相应旧模板。

### P7：学生作答运行时最后退出旧页面

主要文件：`apps/assessment/views.py`、`frontend/src/assessment-runtime/StudentAssessment.vue`、Capture 服务/API、`apps/api/frontend_entry.py`、构建与 Worker 入口。

候选旧资源：`templates/student/question.html`、`static/js/assessment.js` 及经引用核对证明只服务旧作答页的资源。

- [x] 先核对 V1/V2 历史会话是否均有可用的 Vue 续答路径；不批量改写 contract，不丢弃已保存的草稿或本地队列。
- [x] 保留 `question_page` 的学生归属、assignment 检查及授权后的 `X-Capture-Contract`；不能让通用 HTML 入口绕过这些检查。
- [x] 验证五种题型、空状态/修改/返回、中文 IME、刷新、离线重载、断网重连、超时、提交前信心、响应丢失后恢复及正式提交一次性。
- [x] 验证无权限页面不能获取 Capture header，反馈关闭时正确答案及评分字段不会泄露。
- [x] V1/V2 后端合约、四组 E2E 相关专项和过程预览通过后，删除旧模板/专用运行时；共享 `process-event-collector.js` 与 Worker 不能因位于 `static/` 就删除。

### P8：收敛全局入口、旧开关、布局和静态资产

前置条件：P2–P7 逐模块退出完成。

主要文件：`apps/api/{frontend_entry,shell,rollout}.py`、相关 middleware/settings、`templates/base.html` 与共享 partial、专属 Bootstrap/CSS/JS、启动脚本、Nginx 配置、README。

- [x] 原业务 URL 仅提供独立 Vue HTML；删除仅用于选择旧业务 UI 的模块开关及 allowlist 分支。
- [x] 保留业务 feature gate、Capture scope、反馈策略等独立业务控制，不按 `VUE_`/`CAPTURE_` 字面批量删除。
- [x] 核对 `templates/spa/vue_shell.html`：它是迁移期 Vue bootstrap 壳，只有所有路径均使用独立前端入口且消费者为零时才删除。
- [x] `base.html`、共享导航、Bootstrap 与旧样式须逐个核查引用；Django admin、错误页、非业务文档模板等仍使用的内容保留。
- [x] 将旧开关 OFF/ON/allowlist E2E 转为独立前端路由、刷新、授权和错误边界测试，再清理不再需要的 8010–8018 多服务矩阵。
- [x] 保持明确的页面入口表，未知 API 不返回 HTML；保留 Nginx internal redirect 与资源存在性检查。
- [x] 验证启动/构建、旧资源保留与回滚路径；不清理服务端旧哈希资产卷和浏览器待上传数据。

### P9：最终本地回归、GitHub 验证与发布边界

- [x] 检查最终 diff；已删除的师生业务模板没有活动渲染引用，旧配置不会重新启用它们。
- [x] 完成以下阶段检查与全部 CI profile；只对最终阶段执行一次完整回归，具体失败修复后重跑相关项。
- [x] 记录本地通过项、合理 skip、未覆盖的真实浏览器缩放/IME或生产网关测试，禁止统一写成“全部测试通过”。
- [x] 用户明确授权提交/推送后，提交并推送精确 SHA；查看该 SHA 的全部 GitHub job，失败则按日志继续修复。
- [x] 历史 #15–#19 的失败记录不会消失；恢复标准是修复提交的新 CI 全部 required checks 通过。
- [ ] 生产部署只有在另行授权后执行，并按既有备份、迁移、资源保留、健康检查和师生 UAT 流程验收。

## 6. 本地测试环境和命令

在项目根目录的 PowerShell 使用下列环境。`kt_e2e` 仅用于隔离 E2E；Django 测试会创建其测试数据库。不得对生产或 `kt_platform` 运行 E2E flush。

```powershell
$env:PATH = 'C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64;C:\Users\31606\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin;' + $env:PATH
$env:PLAYWRIGHT_BROWSERS_PATH = 'C:\Users\31606\Downloads\ATOP-tooling\pw-fresh-browsers'
$env:PYTHON = 'E:\ATOP\kt-data-platform-complete\.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '55432'
$env:POSTGRES_USER = 'kt_user'
$env:POSTGRES_PASSWORD = 'kt_password'
$env:POSTGRES_DB = 'kt_e2e'
```

先执行对应模块的最窄测试。P1 及 P9 的后端检查：

```powershell
& $env:PYTHON manage.py check
& $env:PYTHON manage.py makemigrations --check --dry-run
& $env:PYTHON manage.py test apps.experiments.test_test_experiment_purge
& $env:PYTHON manage.py test
& $env:PYTHON manage.py collectstatic --noinput
```

现有 PostgreSQL 专项保持：`apps.experiments.test_a_plus_publication`、`test_a_plus_migration_contract`、`tests_postgresql_publish`、`apps.assessment.test_deadline_decisions`、`tests_postgresql_events`、`tests_postgresql_enrollment_race`、`apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_outer_read_committed_transaction_is_rejected_for_source_freeze`。模块修改影响哪一项就优先运行哪一项；P9 核对 CI 专项入口整体。

在 `frontend/` 执行前端检查：

```powershell
npm ci
npm run lint
npm run typecheck
npm run test:unit
npm run api:check
npm run build
```

依赖和 lockfile 未变时，不为每个模块重复 `npm ci`。构建完成后在根目录运行 `python manage.py check_vue_assets`。

P1 先对修改过的 spec 运行定向 E2E；随后运行四个完整 profile。每一组启动前，在没有其他 E2E 服务使用该数据库时执行 `node scripts/prepare-e2e.mjs --capture-v2`，然后分别运行：

```powershell
npx playwright test --config playwright.config.ts
npx playwright test --config playwright.capture-v2.config.ts
npx playwright test --config playwright.capture-initialization.config.ts
npx playwright test --config playwright.submission-repair.config.ts
```

这四行表示四次独立准备/运行，不能一次准备后盲目共用已被前组修改的数据。GitHub 使用独立矩阵 job；本地共享端口和数据库时顺序执行。

视觉验收：受影响师生路径检查 1440×900、390×844、720×450 的 200% 等效布局，无横向溢出、遮挡和不可操作控件；查看实际截图。等效视口不替代真实浏览器菜单缩放，自动化 IME 事件不替代真实输入法，两者在结果中分别报告；需要真实缩放证据的路径使用 Chromium 专项。

## 7. 每阶段完成标准与记录格式

每阶段只有同时满足下面条件才能标记完成：

- 替代页面和必要读写路径可用，原业务 URL 和服务端业务约束保持。
- 相关失败场景、权限、幂等、刷新持久化以及必要的视觉检查通过。
- 该阶段旧模板及其渲染分支实际删除；若有必须保留的项，列明具体文件与消费者，不声称整阶段退出。
- diff 只包含当前模块、直接依赖与必要测试；不夹带数据清理、依赖升级或未授权发布。

执行时在各阶段复选框记录进度，并追加精简验证记录：`阶段 / 提交基线或工作树状态 / 命令 / 结果 / 删除范围 / 仍保留项及原因`。本地通过、GitHub 通过和生产验收分别记录。

## 8. 执行验证记录

| 阶段 | 工作树状态 | 命令与结果 | 删除范围 | 仍保留项及原因 |
| --- | --- | --- | --- | --- |
| P0–P1 | `fix/vue-retirement-ci-recovery`，从 `a6e1463` 上承接已落盘草稿并逐项核对 | Playwright 归属：通用 153、Capture V2 2、初始化 2、提交修复 2；CI 四组独立 PostgreSQL 17 job 与独立产物名已配置 | 无 | 四组专用 profile 保留，分别锁定 Capture、初始化与提交恢复边界 |
| P2–P5 | 同一未提交工作树，按认证、课程/班级、题库、实验顺序退出 | 认证、账号、题库、实验模块定向后端与 Vue 测试通过；发布与并发使用 PostgreSQL 专项验证 | 认证、课程/班级、题库/KC、实验配置与发布的旧 Django 业务模板及仅供旧页面使用的分支 | 下载端点、业务 service、Session/CSRF、服务端 capability 与权限判断保留 |
| P6–P8 | 同一工作树，教师数据、学生学习/结果与作答运行时闭环后收敛入口 | 后端完整回归 1142 项通过；PostgreSQL 专项 45 项通过；前端 85 文件/259 项单测、类型检查、API 合约和 273 模块构建通过 | 教师数据中心/分析、学生学习/结果、旧作答页、迁移期 Vue 壳、`static/js/assessment.js` 与旧 UI rollout 分支 | Django admin、错误页、共享样式、`process-event-collector.js`、Worker、Capture/反馈等业务 gate 保留 |
| P9 本地 | `f992e65` 的 GitHub 运行因高级数据工具回归被取消，该提交已由当前修复工作树取代 | 恢复 6 组研究数据目录、服务端学生检索、学生—习题—事件/过程可读主预览及完整原始记录抽屉；后端数据中心/导出范围 46 项、前端数据中心 7 项及完整 259 项通过；完整后端 1142 项、PostgreSQL 专项 45 项通过；`check`、迁移漂移、collectstatic、`check_vue_assets`、lint、类型、API 合约及构建通过；通用 E2E 117 通过/36 合理跳过，三组专项各 2 项通过 | 清理 Playwright 报告、trace、截图与运行日志；修复后的桌面、390px 移动端和完整记录抽屉复核图复制到工作树外 | 真实浏览器菜单缩放与物理中文输入法未自动化；等效视口与自动化输入事件不冒称对应证据 |
| P9 GitHub | 产品与 CI 修复截至 `91d01eb3ec1a7cfb38ff538d4585129347bebe3f`，运行 `34312630589` | backend、frontend、PostgreSQL contracts、application E2E、Capture V2、Capture initialization、submission repair 共 7 个 job 全部通过 | 无新增删除 | 本行之后仅追加验收记录，不改变产品或 CI 行为 |

当前状态：**P0–P9 的实现、本地回归、提交推送和 GitHub required checks 已完成。生产发布未授权，也未执行。**
