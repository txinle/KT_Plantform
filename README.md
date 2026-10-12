# KT 交互数据采集平台

KT 交互数据采集平台是一个以课程为中心的 Django 实验管理与作答数据采集系统。教师可在同一工作区管理课程、班级、题库、知识组件（KC）、实验发布与研究数据导出；学生通过课程选课关系参加实验，并在单题页面完成作答。

一键本地运行使用项目专用 PostgreSQL 17，由同源网关提供 Vue 构建产物并转发 Django API。Docker 部署使用 PostgreSQL、Gunicorn 与 Nginx，适合在校内服务器或云主机上运行。

上一轮验收结果见 [历史交付说明](docs/ops/school-final-delivery-20261011.md)。当前 `/server`—`/client` 统一重构的范围与验证进度见 [执行记录](docs/superpowers/plans/2026-10-11-unified-frontend-backend-architecture.md)；学校完整验收尚未通过，生产未切换。

## 核心能力

- 课程工作区：按课程隔离班级、学生选课、题库、实验、权限和导出数据。
- 班级与学生：支持班级创建、年级校验、单人添加、Excel 批量导入、跨班复制、转班和移出课程。
- 班级教学分析：按班级查看总体正确率、参与/完成、KC、学生与题目事实，并向下钻取本人实验和语义化作答过程。
- 题库与 KC：支持题目及选项编辑、CSV/XLSX 导入、主/次 KC 映射、稳定课程题号、共享复制、归档和受保护删除。
- 实验发布：支持参与者范围、题目来源、抽题与排序、计时、续答和反馈配置，并通过不可变快照保护已发布实验。
- 学生作答：支持单题显示、自由导航、草稿自动保存、跳题、超时处理、断点续答和幂等提交。
- 我的学习：在教师策略、发布反馈和最小样本量共同约束下显示学生本人的完成、正确率、得分率、趋势、KC 与逐题反馈。
- 数据导出：所有学生相关文件统一以学号和姓名标识学生，以题库唯一编号标识题目；提供实名结果、实名导航、实名 KT、实名导航、学生名册和单次会话报告等导出。
- 审计与安全：关键写操作记录审计日志；跨课程对象、归档课程和研究数据引用均受后端权限与完整性校验保护。

## 技术与运行环境

- Python 3.13 / Django 5.2.16
- Django 领域服务、DRF、Session 认证与权限控制
- Vue 3、TypeScript、Vite 8、Node.js 24 LTS
- 统一 Fetch API 客户端；业务接口位于 `/api/v1/`
- PostgreSQL 17（本地与 Docker 部署）
- Gunicorn、Nginx（Docker 部署）

前端开发需要 Node.js 与 npm；生产环境使用 Vite 预构建的静态资源，不需要运行 Vite 开发服务器，也不依赖运行时 CDN。

## 一键本地运行

启动脚本会发现 Python 3.13 与 Node 24、创建或复用项目内的 `server/.venv`、按需安装依赖、执行 PostgreSQL 数据库迁移，仅在数据库没有用户时写入演示数据，不重置已有题库。脚本先构建前端，再启动构建监听、Django 后端、同源 Node 网关，以及调度/导出、研究数据物化、实验发布和学生比较后台任务。页面就绪后自动打开 `http://127.0.0.1:8000/`。根目录启动器调用 `server/scripts/run_dev.py`，请保留 `server`、`client` 和 `contracts` 的并列结构。

### Windows

双击 `run_local.bat`，或在 PowerShell / 命令提示符中运行：

```bat
.\run_local.bat
```

本机工具默认位于 `E:\ATOP\ATOP-tooling`，可通过 `KT_TOOLING_ROOT` 指定其他位置；npm、pip 和临时文件缓存也写入该目录的 `cache` 子目录。PostgreSQL 使用已初始化的本机实例 `127.0.0.1:55432`、数据库 `kt_platform`。Git Bash 中运行 `./run_local.sh` 会调用同一 Windows 启动器。

在脚本窗口按 `Ctrl+C` 会同步关闭本次启动的 Django、构建监听、Node 网关、后台任务及其子进程；若出现 `Terminate batch job (Y/N)?`，输入 `Y` 关闭启动器。项目 PostgreSQL 实例保持运行，供下次启动复用。

### Linux / macOS

先安装 Python 3.13、Node 24 和 PostgreSQL 17，并将 PostgreSQL 工具加入 `PATH`。脚本要求已有可连接的数据库，默认连接 `127.0.0.1:5432`，账号 `kt_user`、密码 `kt_password`、数据库 `kt_platform`；可通过 `POSTGRES_HOST/PORT/USER/PASSWORD/DB` 覆盖。不使用 SQLite 回退。

```bash
chmod +x run_local.sh
./run_local.sh
```

演示账号：

| 身份 | 账号 | 密码 |
|---|---|---|
| 管理员 | `admin` | `admin123` |
| 教师 | `teacher` | `teacher123` |
| 学生 | `S001` / `S002` / `S003` | `student123` |

首次安装依赖需要能够访问 Python 包索引。演示账号仅供本地验证，禁止直接用于公网环境。

## 手工分别启动前后端

先用当前 checkout 的 `server/.venv` 安装 `server/requirements.txt`，在 `client` 执行 `npm ci`。前端构建与 API 类型检查只读取 `contracts`，不需要 Python 或 Django。数据库迁移与演示造数在 `server` 目录执行；已有数据库不必重复执行 `seed_demo`。

以下 PowerShell 命令在项目根目录分别打开三个终端执行。所有业务页面统一访问 `http://127.0.0.1:8000/`；内部 Django 端口只提供 API、授权下载和运维页面。

后端终端：

```powershell
$postgresBin = "E:\ATOP\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin"
$env:PATH = "$postgresBin;$env:PATH"
$env:POSTGRES_HOST = "127.0.0.1"
$env:POSTGRES_PORT = "55432"
$env:POSTGRES_USER = "kt_user"
$env:POSTGRES_PASSWORD = "kt_password"
$env:POSTGRES_DB = "kt_platform"
$env:CLASS_CENTER_ENABLED = "1"
$env:STUDENT_LEARNING_ANALYTICS_ENABLED = "1"
Set-Location .\server
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

前端构建与监听终端：

```powershell
$nodeDir = "E:\ATOP\ATOP-tooling\node-v24.20.0-win-x64"
$env:PATH = "$nodeDir;$env:PATH"
Set-Location .\client
& "$nodeDir\npm.cmd" run build
& "$nodeDir\npm.cmd" run dev:watch
```

同源网关终端：

```powershell
$nodeDir = "E:\ATOP\ATOP-tooling\node-v24.20.0-win-x64"
$env:PATH = "$nodeDir;$env:PATH"
$env:KT_GATEWAY_PORT = "8000"
$env:KT_BACKEND_ORIGIN = "http://127.0.0.1:8001"
Set-Location .\client
& "$nodeDir\npm.cmd" run dev
```

分别启动时，在三个终端分别按 `Ctrl+C`。`CLASS_CENTER_ENABLED` 与 `STUDENT_LEARNING_ANALYTICS_ENABLED` 控制领域功能开放，不切换前端实现。升级前可在后端运行 `python manage.py audit_class_experiment_tasks` 只读检查历史任务；该命令不自动修复数据。

## Docker 部署

### 启动

```bash
cp server/.env.example server/.env
# 修改 server/.env 中的密钥、数据库密码和允许访问的主机
docker compose --env-file server/.env -f deploy/docker-compose.yml up --build -d
```

部署前至少应检查：

- 将 `DJANGO_SECRET_KEY` 和 `POSTGRES_PASSWORD` 改为强随机值。
- 将 `DJANGO_DEBUG` 设为 `0`。
- 正确设置 `DJANGO_ALLOWED_HOSTS` 和 `DJANGO_CSRF_TRUSTED_ORIGINS`。
- 正式环境不需要演示数据时，将 `SEED_DEMO` 设为 `0`。
- 在 Nginx 前配置域名与 HTTPS，并建立数据库和媒体文件备份。

### 查看生产日志

```bash
docker compose --env-file server/.env -f deploy/docker-compose.yml ps
docker compose --env-file server/.env -f deploy/docker-compose.yml logs web --tail=200
docker compose --env-file server/.env -f deploy/docker-compose.yml logs nginx --tail=100
```

`django.request` 的错误会写入 Gunicorn 控制台，因此 Django 请求异常可直接通过 `web` 服务日志查看。

## 功能指南

### 课程与班级

- 课程所有者、协作教师和管理员按现有权限管理课程资源；归档课程保持只读。
- 班级的用户可见字段为“年级”。该字段可以留空；填写时必须是四位数字，例如 `2025`。
- 创建或编辑班级时可设置班级名称、年级和可用状态。停用班级不能接收新增、导入、复制或转入学生，但仍可查看历史名单并移出学生。
- 学生账号属于整个平台，同一个全局账号可以参加多门课程。
- 教师可单独创建学生，也可通过 Excel 批量导入。新账号的初始密码为 `S + 学号`，必须先改密才能进入学生端；已有账号重新入班时保留密码和强制改密状态。
- 班级管理的“编辑信息”可更正当前成员的学号、姓名，修改同一全局账号；内部 ID、历史作答及密码状态不变。学号变化时同步登录用户名，已有密码不会随学号变化。
- 曾属于当前课程的账号再次添加时，姓名冲突可通过“确认更正并加入”处理；从未属于本课程的账号发生姓名冲突时，需由管理员更正。涉及其他课程时会提示全局影响。
- 教师“重置密码”恢复为 `S + 当前学号`，使原登录会话失效，并要求学生重新登录后改密。
- 当前班级的单班导入允许文件填写页面显示的班级名称。系统在校验预览时将其解析为规范班级名称，并在确认导入时沿用同一结果。
- 旧版多班级导入继续严格匹配班级名称，避免学生进入错误班级。
- “移出课程”只删除学生在当前课程中的选课关系，不删除全局账号，也不影响其他课程。
- 移出后，学生立即失去当前课程及未完成实验的访问权限；系统同时清理草稿实验中的临时名单和显式覆盖规则。已发布快照、会话、作答、导出和审计记录保持不变。
- 开启班级中心后，班级页分为概览、学生表现、实验分析和班级管理；成员管理使用 Vue 页面与统一 API，权限、CSRF、事务及审计仍由 Django 控制。
- 教师可在“学生端显示设置”中控制成绩、正确率、逐题结果、班级平均、本人位次、KC 和趋势。完整排行榜、学生过程轨迹和 KT/CD 推断不属于学生端 v1。

已有默认密码识别与数据库迁移分开：迁移只新增默认值为 `false` 的强制改密字段。在已配置目标数据库的运行环境中，可运行 `python manage.py mark_student_default_passwords --dry-run` 查看扫描、匹配、跳过和异常统计。确认结果后，显式运行 `python manage.py mark_student_default_passwords --apply` 才会标记仍使用 `S + 学号` 的账号；命令不修改密码哈希，默认仅预演，也不会随启动或迁移自动运行。

### 题库与知识组件

- 每个课程题库可维护题目、选项、答案、解析、难度和主/次 KC 映射。
- 题目可通过 CSV/XLSX/DOCX 文件导入，KC 对应关系可单独批量导入。
- 导入或保存后，系统自动判定题目状态：题干、答案及课程 KC 映射完整有效的题目为“可用”；可补全的题目保存为“草稿”并显示原因。解析、教师设定难度为选填项，不会因留空而变成草稿。独立题库只检查题目内容。
- 题库详情中的“复核草稿”可批量启用符合要求的历史草稿；未通过的保留草稿并列出原因。复核不恢复已归档题目，也不修改已发布题目快照。
- 课程题目使用稳定题号。删除题目后不会重排其他题目，也不会复用已产生的稳定代码。
- 教师题库清单和编辑页只显示题库内序号；课程题号、稳定代码仍保留在内部数据与导出关联中，用于追溯。
- 题库可设为私有或共享。共享题库通过“来源课程 → 创建者 → 题库列表”三级页面浏览。
- 复制共享题库后，目标课程中的题库保持源名称，不添加“（副本）”后缀。
- 复制结果使用新的题库、题目、选项、课程题号和 KC 映射记录；复制后修改源题库或目标题库不会相互影响。
- 题库归档后不再出现在课程题库主列表，而是进入独立归档页面；恢复时统一转为私有。
- 永久删除题库或单题必须经过二次确认。题库或题目被实验候选关系、发布快照或受保护研究数据引用时，系统会拒绝删除。
- 允许删除单题时，其选项和 KC 关联会随题目清理，但稳定课程题号记录保留。

### 实验配置与发布

- 教师可配置实验参与班级或学生、题目来源、抽题数量、排序策略、计时方式、续答方式和反馈方式。
- 锁定并发布前，系统会重新校验题目、参与者、课程状态和配置完整性。
- 所有待发布题目都必须设置主 KC。缺少主 KC 时，页面会列出对应题号，不创建实验版本。
- 发布后生成不可变题目快照和版本配置。后续编辑源题目、选项或 KC 不会改写历史实验。
- PostgreSQL 发布查询只锁定基础题目行，避免对包含可空外连接的查询执行无范围 `FOR UPDATE` 所导致的兼容性错误。
- 发布失败的业务校验会返回实验详情页并显示可理解的提示，不进入通用 500 页面。

### 学生作答

- 学生登录后只看到自己已加入课程中可参加的已发布实验。
- 实验按配置生成个人题目分配、抽题种子和排序结果。
- 作答页一次显示一道题，支持题号导航、草稿自动保存、跳题和断点续答。
- 每题正式提交使用幂等令牌；提交成功后答案锁定，重复请求不会产生重复提交。
- 系统支持总时长或单题时长限制，并记录访问、离开、跳过、超时和提交状态。
- 是否显示正确答案、解析和反馈由实验发布配置决定。

### 数据与导出

系统记录并可按权限导出：

- 题目访问事件、进入原因和服务器接收时间。
- 首次访问、最后访问、访问次数和累计活动时长。
- 题目分配顺序、首次访问顺序和正式提交顺序。
- 草稿、最终答案、提交状态、正确性和得分信息。
- 实验版本、题目快照、学生会话、单题作答和审计记录。

实验结果界面按权限提供：

- 实名结果 XLSX。
- 实名导航事件 CSV。
- 实名 KT CSV 和实名导航 CSV（通过授权的 `/api/v1/` 下载入口）。
- 学生名册 CSV。
- 学生单次作答会话报告。

所有导出均包含直接身份数据；课程负责人、协作教师和管理员可在课程授权范围内生成、下载，所有生成和下载操作会记录审计日志。

## 导入模板与规则

### 班级学生名单

```text
学号 | 姓名 | 班级
```

- 每行学号必须唯一。
- 班级必须匹配当前导入目标；单班导入支持页面显示名称的规范化匹配。
- 已有学号的姓名必须与平台账号一致。
- 新账号初始密码为 `S + 学号`，已有账号密码不变。

### 题目文件

```text
序号 | 题目 | 选项A | ... | 选项F | 答案
```

支持 CSV/XLSX/DOCX。导入预览区分“可用”“草稿”“已存在将跳过”和“校验失败”，并显示问题所在；可用题目和草稿均可确认导入。无法识别的题型等记录不创建题目。补全草稿并保存后，系统自动重新判定是否可用。

### KC 对应表

```text
题目序号 | 主知识点 | 次知识点
```

多个次知识点使用英文或中文分号分隔。正式发布前，每道题必须具有且只能具有一个主 KC。

## 数据保护与操作边界

- 归档用于隐藏和冻结日常操作，不等于删除；归档题库和归档课程的数据仍然保留。
- 永久删除不可恢复，但后端会在事务和行锁内检查实验、快照和研究数据引用，不会通过级联删除破坏历史实验。
- 移出学生只影响当前课程选课和草稿参与者配置，不删除全局账号或已发布研究数据。
- 题目发布后使用不可变快照；原题修改或允许的源数据清理不影响历史实验。
- `last_visit_to_submission_ms` 表示最后一次进入该题至提交的时间，不代表完整认知作答时长。
- 未提交、跳过或超时的作答默认将 `is_correct` 保持为空。
- 分层随机按主 KC 与难度区间组织题目并生成可复现的个人抽题和顺序，不属于自适应推荐。
- 生产部署、备份恢复、域名、HTTPS 和服务器安全加固需由部署环境单独完成。

## 独立前端运行

师生业务页面使用独立构建的 Vue HTML；Django 保留 Session 认证、DRF API、文件下载和 `/admin/`。业务 URL 沿用原路径，未知 API 不会返回前端 HTML。

- 本地启动：当前 checkout 的 `server/.venv` 运行 `server/scripts/run_dev.py`。启动器先构建前端，再启动构建监听、内部 Django API 服务与同源 Node 网关。Node 网关按 `contracts/frontend-routes.json` 提供登记页面，API 和受限下载交给 Django。
- 前端与后端可分别构建。生产网关使用 `deploy/nginx/kt-platform.conf` 和 `frontend-pages.conf`；只有登记页面的 GET/HEAD 请求获得前端 HTML。Django 保留页面反向 URL 名称，不读取客户端构建产物；旧表单入口已退役。
- 采集器及离线 Worker 随前端构建，Worker 继续使用原 URL 和 scope。Nginx 的 `frontend_assets` 持久卷保留旧哈希资源及版本化作答壳，启动脚本追加新版本文件；发布及回滚不删除该卷，不清理 IndexedDB 或待上传队列。
- 本轮便携 PostgreSQL 与本地浏览器验证不替代生产 Nginx 容器联调、真实中文输入法或真实浏览器 200% 缩放检查。当前验证范围及未通过门槛见 [执行记录](docs/superpowers/plans/2026-10-11-unified-frontend-backend-architecture.md)。
