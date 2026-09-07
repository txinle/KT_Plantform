# KT Research UI audit

## 2026-09-07 扩展交互状态布局复查

本轮只检查已有同 URL Vue 主路径中的题库与实验界面。题库、实验均由服务端开关选择 Vue shell，Django 模板仍保留为关闭开关时的功能性回退；未改动 URL、权限、导入/保存语义、采集器或后端 API。审查使用隔离 `kt_e2e` 的既有五题型夹具，教师账号为 `teacher`。

| 已复现问题 | 修复 | 验证证据 |
|---|---|---|
| 390 px 题目编辑页的“课程 KC 映射”表格把四列强行压缩；KC 选择框只剩单字符，父 KC 与“移除”逐字断行 | 为该表格及其容器增加特定类，设置可读列的最小宽度，并在容器内横向滚动；卡片和页面主轴可收缩 | 先加入局部滚动断言后失败（表格 `scrollWidth <= clientWidth`）；修复后 390 px 根页面无横向溢出，表格局部可滚动，最右侧“移除”操作可滑入容器可视范围 |

新增 `frontend/e2e/layout-expanded-audit.spec.ts` 与 `frontend/playwright.layout-expanded.config.ts`，沿用 8124 的隔离 PostgreSQL 服务配置，但使用独立输出目录，避免覆盖既有 `layout-audit` 证据。最终 4/4 浏览器检查通过：

- 390 px 导入题目预览的宽表在本地容器滚动；400 错误状态的警告区域无页面级横向溢出。
- 题目编辑页在 1440、1024、768、390 px 使用长连续名称与长输入值，检查文本框和根页面几何；KC 表格在 390 px 保留可访问的局部滚动。
- 题库的嵌套导航在 1440、390 px 使用长标签；1024 px 题库详情表格在局部滚动，390 px 切换为既有卡片视图。
- 实验编辑页的第二步题型选择器与“可选研究工具”展开，添加提示/规则后在 1440、1024、768、390 px 检查根页面几何和实际输入控件。

证据位于 `frontend/.e2e-results/layout-expanded/`；最终命令为 `npx playwright test --config playwright.layout-expanded.config.ts`，结果 4/4 通过。使用 Node 24.20.0 的 `npm run build` 也通过。导入预览和 400 错误的内容由浏览器路由拦截提供，只验证前端状态的布局，不把它等同于任意服务端字段校验文案的端到端验证。本轮不声称穷尽所有名称长度、浏览器、缩放级别、数据量或交互组合。

截图定位随后统一为回到页面顶部，再重跑长文本编辑场景（1/1）。可用于查阅的最终截图在 `frontend/.e2e-results/recheck-editor-screens/`；早期 fullPage 截图中的固定顶栏中段位置属于截图时的滚动状态，不作为产品布局缺陷。五题型作答的本轮截图与 2/2 浏览器结果另存 `frontend/.e2e-results/recheck-capture/`。

---

## 2026-09-07 文本与控件布局复查

本轮按用户提供的“私有”竖排截图复现后修复。原始回归覆盖 35 个教师 URL × 5 个宽度（1440、1280、1024、768、390），175 个组合中发现 20 个异常组合；随后增加课程 KC 页。最终教师 36 × 5 = 180 个组合全部通过，学生课程、作业、班级中心、结果、单题反馈另有 5 × 5 = 25 个组合通过。几何检查不能证明任意数据、浏览器与交互状态下都不存在视觉问题。

| 已复现问题 | 修改 | 验证证据 |
|---|---|---|
| 题库“私有”被挤成竖排；平板操作列重叠 | 状态不压缩/不换行，状态和操作列有确定最小宽度；右侧表单随有效空间转为纵向；表格在自己的容器滚动 | 原截图对应页面 1440/1280/1024/768/390；短标签断行检测通过 |
| “可用”“已冻结”及表头单字逐行 | 共用状态组件不换行；表头保持横向；课程统计表允许容器内滚动 | 班级、账号、实验、课程概览回归通过 |
| 768 px 题库详情同时隐藏桌面表格和手机列表 | 移除与共用 48rem 断点冲突的局部显隐规则 | 增加“两种视图不得同时隐藏”断言 |
| KC 列表撑开父网格、题库详情负边距造成页面溢出 | 网格/面板 min-width:0，主列 minmax(0,1fr)；去除课程信息负边距 | 768 px 整页无横向溢出 |
| 实验创建表单在 1024/1280 px 超出正文 | 在固定列最小宽度无法容纳前改为两列，说明独占一行 | 两个失败宽度重测通过 |
| 数据质量页 1024 px 固定五列溢出 | 计数面板按可用空间自动换列 | 1024 px 重测通过 |
| 长名称和连续英文编号撑开标题/手机卡片 | 文本所在 flex/grid 子项允许收缩和断行；顶栏姓名省略并保留完整 title | 390 px 长中文/不含空格英文压力例通过（仅 DOM 注入，不写数据库） |
| 手机题库/KC 卡片遗漏已有操作 | 补回同一权限条件下的归档、重命名绑定；状态颜色与桌面一致 | 手机入口/编辑框浏览器检查、桌面/手机重命名和归档相同 API 单元测试通过 |
| 课程选择框与文字间距失配 | 复选框去除从一般输入框继承的内边距，固定其实际图形尺寸 | 控件几何检查通过 |

证据位置（相对仓库根）：

- 修改前：`frontend/.e2e-results/layout-audit/`，含 `layout-findings.json` 与真实页面截图。
- 中间失败：`frontend/.e2e-results/layout-audit-after/`，保留剩余平板溢出与长名称失败证据。
- 修复后：`frontend/.e2e-results/layout-audit-final/`；`layout-audit-final.log` 为 3/3 浏览器测试通过（205 个页面/宽度组合＋长名称/手机操作）。
- `frontend/e2e/layout-audit.spec.ts` 为可重跑的审查用例；基于隔离 `kt_e2e` 演示夹具，不能作为真实题目或真实参与者内容的验证。
- 最终 PostgreSQL 全套 1,155/1,155 通过；Django check、迁移一致性、静态收集和 Vue 产物检查通过。导出与质量判定的详细边界见 `docs/changes/2026-09-07-export-audit.md`。
- 本轮 frontend lint 无错误（原有 2 个测试夹具警告）；全量 Vue 单元 176/176，后续新增手机分支专项 5/5；API check、typecheck/build 通过。API 保留原有 13 个枚举命名警告。
- 五类题型与工具浏览器回归 `layout-audit-capture-final.log` 2/2 通过：改答、清空、中文 composition、离线刷新重连、冻结提交、待评分及原有答案/解析展开。重建范围仅为脚本明确校验的隔离 `kt_e2e`；`kt_platform` 与真实题库未改动。
- 手机作答与反馈以 390×844 检查；720×450 仅验证等效窄布局，不冒充真实浏览器 200% 缩放或手机软键盘。

---

## 2026-09-07 独立前端整理（本轮）

本轮采用批准的紫色方案，结合本机 Taste Skill 与 Impeccable 的操作型产品规则。以下是代码与浏览器检查的具体结果，不是通用设计评分。历史审计保留在后文，不作为本轮截图证据。

| 问题 | 修改与边界 | 验证 |
|---|---|---|
| 成功／警告文字对比度不足 | 唯一前端 tokens 调整为 #16713E、#875B0A，错误为 #B52E37；保留文字状态 | 对应浅背景对比度 5.56、5.41、5.56；正文白底 15.45、辅助文字 6.03、主要操作白底 5.72、选中浅底 5.03 |
| 左栏视觉顺序与焦点顺序不一致 | 二级导航挂载点直接进入当前一级导航之后，不使用 CSS order 插入 | WorkspaceShell DOM 顺序、动作保持、抽屉焦点测试通过 |
| 主题与布局样式依赖 Django | tokens/foundations 随 Vite 构建，HTML 使用 API 获取会话与 CSRF | 独立 HTML 无 kt-bootstrap，入口、teacher 路由动态加载、登录/退出测试通过 |
| 重复容器、工具间距与详情层级 | 答案区使用分隔线、统一按钮与正文层级；修复研究工具样式选择器 | 五类题型桌面和手机实际截图；输入与暴露处理函数未替换 |
| 手机目录推迟正文 | 原生 details 目录在手机默认折叠，保持内容组件挂载 | 目录组件、反馈展开测试及浏览器检查通过 |
| 班级列表桌面／手机内容重复 | 将缺失的断点辅助类、表格滚动、分页与手机列表样式归入共用基础层 | 浏览器断点可见性断言 |
| 旧操作跳回 Django 页面 | 账号／班级、题库／KC、全局实验、教学分析与完成页迁入 Vue；薄 API 复用既有校验/服务 | 新接口权限、空 body 确认兼容、预览与确认、canonical 深链专项 PostgreSQL 测试 |

### 正式入口与保留边界

- `apps/api/frontend_entry.py` 显式登记 HTML GET/HEAD；原 URL 保留。旧 POST、API、下载及 `/admin` 仍由 Django 处理。未知 API 不进入 Vue catch-all。
- 课程、班级、题库、实验、数据和学生入口以原 URL 进入 Vue。全局旧入口由服务端提供等价 canonical 目标，导入与编辑路径不丢失后缀。
- `frontend/index.html`、`src/app/bootstrap.ts`、`src/main.ts` 独立启动；Session Cookie 与 CSRF 沿用 Django。
- collector 与 Worker 从前端资源构建加载；保留原 Worker URL/scope、离线队列和采集协议。缓存题页的离线身份 envelope 绑定具体 session、assignment、页面路径。
- Nginx 哈希资源目录使用持久卷；启动脚本只追加当前构建资源，保留旧页面仍可能请求的文件。两个版本发布及回滚的文件保留测试通过，实际容器联调尚未执行。
- 默认开发启动及新安装 compose 使用独立前端；开关和 Course/User 白名单可分批切换。旧模板仍物理保留供退出验收期间回退，本轮不宣称已经删除全部旧模板。

### 验证证据与限制

- 前端单元：76 文件、174 测试通过；新确认空 body 专项随后通过；最后文案与共用组件专项 22/22 通过。lint 无错误（测试夹具组件规则有警告）。typecheck/build、API generate/check 均通过，OpenAPI Errors 0，13 项枚举/组件命名警告仍保留。
- Django check、迁移一致性、静态收集与 check_vue_assets 通过。PostgreSQL 全套 1,137/1,137 通过（822.478 秒）；后续班级创建入口修复另跑 PostgreSQL 12/12、Vue 4/4 与实际浏览器 1/1，保留原创建成功路径和表单失败行为；不重复未改动的全套。
- 五类题型浏览器回归 2/2：快速变化、清空、多选增删、CDP composition、离线刷新重连、正式提交与原有反馈展开通过。
- 独立前端浏览器主要流程 3/3 通过，班级 JSON 创建另项 1/1 通过，另覆盖无课程教师的题库、实验创建与编辑。35 个教师业务深链按 1440×900 和 390×844 检查；学生另外覆盖 768×1024 与 720×450。截图位于 `frontend/.e2e-results/standalone-final/`；作答截图位于 `frontend/.e2e-results/capture-v2/`。
- 修改前截图：`frontend/.e2e-results/separation-before/`。每批构建/测试日志保存在同一 `.e2e-results` 下，以 `separation-final-` 为最终检查前缀；旧失败日志保留作为诊断历史。
- 使用 checkout `.venv`、Node 24.20.0、便携 PostgreSQL 17.11，测试限 `kt_e2e`/`test_kt_e2e`。真实题库与开发 `kt_platform` 未重建。
- 未完成生产 Nginx 容器运行验证（Docker daemon 不可用）、真实硬件中文输入法、手机软键盘与真实浏览器 200% 缩放。CDP composition 和等效窄视口不冒充这些验收；旧模板最终删除与全量发布需达到这些退出条件。

---

Audit date: 2026-08-04

Baseline commands and runtime:

- `python manage.py check`: passed.
- `python manage.py test`: 15 tests discovered; 14 passed and 1 errored because the assessment test reused a stale pre-publication `Experiment` instance after publishing.
- Local server: `http://127.0.0.1:8000/` with the documented teacher and student demo accounts.
- Rendered checks: 1440 x 900 and 390 x 844.

## Page inventory and key paths

- Login: `/login/`
- Teacher: dashboard, question banks, bank detail, question edit, question/KC imports, KC library, classes, students, experiments, experiment detail, results/exports.
- Student: experiment list, start/resume, single-question assessment, finish, completion.
- Admin: `/admin/`

Teacher path: login -> bank list -> bank detail -> question edit/import -> experiment list -> configuration -> student assignment -> publish -> results/export.

Student path: login -> experiment list -> start/resume -> visit question -> autosave -> navigate -> submit/lock -> skip -> finish.

## Findings

| Page | Severity | Current issue | User impact | Planned repair | Files | Research risk |
|---|---|---|---|---|---|---|
| All pages | P0 | Bootstrap is loaded only from a CDN. When the request fails, containers, navigation, forms, tables, and the question layout collapse. The mobile question baseline became nearly unusable. | Local/offline data collection cannot be operated reliably. | Vendor Bootstrap 5.3 runtime and license; load local assets. | `templates/base.html`, `static/vendor/bootstrap/*` | Low, but operationally critical. |
| Student question | P0 | Submission, skip, navigation-save failure, timeout, and experiment finish rely on native `confirm()` or `alert()`. | Inconsistent focus behavior, inaccessible errors, and blocked recovery; mobile dialogs interrupt the task. | Add an accessible Bootstrap confirmation dialog, inline error region, loading state, and focus recovery while preserving Fetch contracts. | `templates/student/question.html`, `static/js/assessment.js` | High. Draft-before-navigation, idempotency, and lock semantics must remain unchanged. |
| Student question | P1 | Question status legend relies mainly on color; current question lacks `aria-current`; save state lacks an ARIA live region. | Keyboard and screen-reader users cannot reliably distinguish state or saving outcome. | Pair every state with text/symbol, add `aria-current="step"`, an accessible legend, and `aria-live="polite"`. | `templates/student/question.html`, `static/css/app.css` | Medium. Do not create extra visit events. |
| All authenticated pages | P1 | Navigation has no active-page state, weak mobile behavior, no skip link, and minimal landmark/ARIA structure. | Orientation and keyboard traversal are inefficient. | Add semantic header/nav/main, skip link, active nav state, collapsed mobile navigation, and alert live regions. | `templates/base.html`, `static/css/app.css`, `static/js/app.js` | Low. Logout remains POST with CSRF. |
| Teacher forms | P1 | Several forms use `form.as_div` or an undifferentiated field loop. Errors lack a linked page summary and fields are not grouped by business meaning. | Teachers miss validation errors and configuration dependencies. | Add reusable field and error-summary components; group question and experiment fields; preserve field names and validation. | teacher form templates, `templates/components/*` | Low. No backend validation changes. |
| Experiment detail | P1 | Synchronize and publish actions appear beside ordinary navigation; publish uses native confirmation and has no readiness checklist. | Accidental irreversible publication and unclear lifecycle. | Add lifecycle status, pre-publication checklist, differentiated actions, and accessible confirmation dialog. | `templates/teacher/experiment_detail.html`, `static/js/app.js` | High. Publication snapshot behavior must stay unchanged. |
| Results/export | P1 | Anonymous KT, navigation events, and identity mapping are presented as near-equal download buttons. | Users can misunderstand privacy scope and export identity data casually. | Explain each export, isolate the identity export in a warning surface, and retain exact endpoints/semantics. | `templates/teacher/experiment_results.html` | High. Export columns and authorization must not change. |
| Login | P1 | Demo credentials are always visible; labels are not explicitly associated with inputs; hierarchy resembles a generic card. | Credentials can be exposed outside debug mode and keyboard/screen-reader labeling is weak. | Show demo credentials only in debug context, add explicit labels/errors and an institutional product layout. | `templates/registration/login.html`, login context | Medium. Authentication behavior remains Django-native. |
| Teacher dashboard | P2 | Three equal cards include a decorative “implementation principle” instead of actionable work state. | Space is consumed without helping the next decision. | Use restrained metrics and direct workflow entry points; emphasize recent banks and experiments. | `templates/teacher/dashboard.html` | Low. |
| Bank list/detail | P2 | Owner, updated time, visibility, import distinction, long stems, and empty-state next steps are weak or absent. | Teachers scan slowly and may choose the wrong import action. | Improve table hierarchy, status labels, responsive shell, and explicit empty-state actions. | bank templates | Low. |
| Import pages | P2 | Upload and preview lack a visible three-stage model, file/error summary, and clear irreversible confirmation hierarchy. | Teachers cannot see where they are or what confirmation means. | Add upload -> preview -> import step indicator and structured preview/errors. | import templates | Medium. Transaction logic remains unchanged. |
| KC, class, student lists | P2 | Scope/status is plain text and tables do not adapt cleanly to small screens. Password reset is dense inside each row. | Slow scanning and poor mobile usability. | Add semantic badges, responsive table shells, direct helper text, and stronger focus states. | list templates | Low. |
| Student dashboard/completion | P2 | Status and action language are under-specified; completion uses a decorative check character and generic statistic cards. | Students may not know whether to start, continue, or only view completion. | Make state/action explicit and show only factual completion counts with a clear return path. | student dashboard and completion templates | Low. |
| Error pages/Admin | P2 | No branded 403/404/500 templates; Admin has default branding. | Dead ends and inconsistent institutional identity. | Add direct error pages and lightweight Admin title/style customization. | error templates, admin site config/static CSS | Low. |

## Baseline design diagnosis

- Existing design dials: variance 2, motion 1, density 6.
- Preserve: navy identity, compact teacher information, single-question assessment, server-rendered navigation, current Chinese copy register.
- Retire: CDN-only runtime, identical rounded cards everywhere, weak focus/state semantics, inline styles, native dialogs, color-only status, and decorative metrics.
- Information architecture, routes, field names, and core workflows are sound and must remain stable.
