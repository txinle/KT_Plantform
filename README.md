# KT 交互数据采集平台

KT 交互数据采集平台是一个以课程为中心的 Django 实验管理与作答数据采集系统。教师可在同一工作区管理课程、班级、题库、知识组件（KC）、实验发布与研究数据导出；学生通过课程选课关系参加实验，并在单题页面完成作答。

一键本地运行使用项目专用 PostgreSQL 17，并由 Django 与 Vite 联合提供开发页面。Docker 部署使用 PostgreSQL、Gunicorn 与 Nginx，适合在校内服务器或云主机上运行。

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

- Python 3.10+
- Django 服务端模板、DRF 与权限控制
- Vue 3、TypeScript、Vite 8、Node.js 24 LTS
- 本地托管的 Bootstrap 5.3.8
- Fetch API
- PostgreSQL 17（本地与 Docker 部署）
- Gunicorn、Nginx（Docker 部署）

前端开发需要 Node.js 与 npm；生产环境使用 Vite 预构建的静态资源，不需要运行 Vite 开发服务器，也不依赖运行时 CDN。

## 一键本地运行

启动脚本会自动发现 Python 与 Node.js、创建或复用项目内的 `.venv`、按需安装依赖、执行数据库迁移，并在新建本地数据库时写入演示数据。脚本会同时启动 Django 后端和 Vite 前端，页面入口为 `http://127.0.0.1:8000/`。

### Windows

双击 `run_local.bat`，或在 PowerShell / 命令提示符中运行：

```bat
.\run_local.bat
```

在脚本窗口按一次 `Ctrl+C` 会同步关闭本次启动的 Django、npm、Vite 及其子进程；项目 PostgreSQL 实例保持运行，供下次启动复用。

### Linux / macOS

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

前后端最核心的启动命令就是：

```text
前端：npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
后端：python manage.py runserver 0.0.0.0:8000
```

本项目还需要指定 Node.js、PostgreSQL 和 Vue 功能开关。以下命令假定已经至少成功运行过一次 `run_local.bat`，在项目根目录打开两个普通 CMD 命令窗口后，可以直接逐行输入。

第一个 CMD 窗口启动前端：

```bat
set "NODE_DIR=%USERPROFILE%\Downloads\ATOP-tooling\node-v24.20.0-win-x64"
set "PATH=%NODE_DIR%;%PATH%"
cd frontend
"%NODE_DIR%\npm.cmd" run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

第二个 CMD 窗口启动后端：

```bat
set "POSTGRES_BIN=%USERPROFILE%\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin"
set "PATH=%POSTGRES_BIN%;%PATH%"
set "POSTGRES_HOST=127.0.0.1"
set "POSTGRES_PORT=55432"
set "POSTGRES_USER=kt_user"
set "POSTGRES_PASSWORD=kt_password"
set "POSTGRES_DB=kt_platform"
set "CLASS_CENTER_ENABLED=1"
set "STUDENT_LEARNING_ANALYTICS_ENABLED=1"
set "VUE_TEACHER_EXPERIMENTS_ENABLED=1"
set "VUE_TEACHER_QUESTION_BANKS_ENABLED=1"
set "VUE_TEACHER_COURSES_ENABLED=1"
set "VUE_TEACHER_CLASSES_ENABLED=1"
set "VUE_TEACHER_DATA_CENTER_ENABLED=1"
set "VUE_STUDENT_COURSES_ENABLED=1"
set "VUE_STUDENT_ASSESSMENT_ENABLED=1"
set "VITE_DEV_SERVER_URL=http://127.0.0.1:5173"
.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

如果使用 PowerShell，对应命令如下。

PowerShell 前端窗口：

```powershell
$nodeDir = "$env:USERPROFILE\Downloads\ATOP-tooling\node-v24.20.0-win-x64"
$env:PATH = "$nodeDir;$env:PATH"
Set-Location .\frontend
& "$nodeDir\npm.cmd" run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

PowerShell 后端窗口：

```powershell
$postgresBin = "$env:USERPROFILE\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin"
$env:PATH = "$postgresBin;$env:PATH"
$env:POSTGRES_HOST = "127.0.0.1"
$env:POSTGRES_PORT = "55432"
$env:POSTGRES_USER = "kt_user"
$env:POSTGRES_PASSWORD = "kt_password"
$env:POSTGRES_DB = "kt_platform"
$env:CLASS_CENTER_ENABLED = "1"
$env:STUDENT_LEARNING_ANALYTICS_ENABLED = "1"
$env:VUE_TEACHER_EXPERIMENTS_ENABLED = "1"
$env:VUE_TEACHER_QUESTION_BANKS_ENABLED = "1"
$env:VUE_TEACHER_COURSES_ENABLED = "1"
$env:VUE_TEACHER_CLASSES_ENABLED = "1"
$env:VUE_TEACHER_DATA_CENTER_ENABLED = "1"
$env:VUE_STUDENT_COURSES_ENABLED = "1"
$env:VUE_STUDENT_ASSESSMENT_ENABLED = "1"
$env:VITE_DEV_SERVER_URL = "http://127.0.0.1:5173"
.\.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

分别启动时，需要在两个窗口中分别按 `Ctrl+C`。停止后端不会自动停止另一个窗口中的 Vite。

如果尚未初始化项目，可先执行完整的手工准备流程：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

如果不需要演示数据，可跳过 `python manage.py seed_demo`。

班级中心采用分级旗标，默认均关闭。验证完成后先开启教师端，再开启学生端：

```text
CLASS_CENTER_ENABLED=1
STUDENT_LEARNING_ANALYTICS_ENABLED=1
```

学生旗标只有在教师旗标开启时生效。应用回滚只需关闭旗标，不删除任务、发布快照、策略或历史事实。升级后可用 `python manage.py audit_class_experiment_tasks` 检查无法无歧义回填的历史班级实验；命令只读，不会自动合并或修复历史。

## Docker 部署

### 启动

```bash
cp .env.example .env
# 修改 .env 中的密钥、数据库密码和允许访问的主机
docker compose up --build -d
```

部署前至少应检查：

- 将 `DJANGO_SECRET_KEY` 和 `POSTGRES_PASSWORD` 改为强随机值。
- 将 `DJANGO_DEBUG` 设为 `0`。
- 正确设置 `DJANGO_ALLOWED_HOSTS` 和 `DJANGO_CSRF_TRUSTED_ORIGINS`。
- 正式环境不需要演示数据时，将 `SEED_DEMO` 设为 `0`。
- 在 Nginx 前配置域名与 HTTPS，并建立数据库和媒体文件备份。

### 查看生产日志

```bash
docker compose ps
docker compose logs web --tail=200
docker compose logs nginx --tail=100
```

`django.request` 的错误会写入 Gunicorn 控制台，因此 Django 请求异常可直接通过 `web` 服务日志查看。

## 功能指南

### 课程与班级

- 课程所有者、协作教师和管理员按现有权限管理课程资源；归档课程保持只读。
- 班级的用户可见字段为“年级”。该字段可以留空；填写时必须是四位数字，例如 `2025`。
- 创建或编辑班级时可设置班级名称、年级和可用状态。停用班级不能接收新增、导入、复制或转入学生，但仍可查看历史名单并移出学生。
- 学生账号属于整个平台，同一个全局账号可以参加多门课程。
- 学生可单独创建，也可通过 Excel 批量导入。新账号的初始密码为 `S + 学号`；已有账号保持原密码不变。
- 当前班级的单班导入允许文件填写页面显示的班级名称。系统在校验预览时将其解析为规范班级名称，并在确认导入时沿用同一结果。
- 旧版多班级导入继续严格匹配班级名称，避免学生进入错误班级。
- “移出课程”只删除学生在当前课程中的选课关系，不删除全局账号，也不影响其他课程。
- 移出后，学生立即失去当前课程及未完成实验的访问权限；系统同时清理草稿实验中的临时名单和显式覆盖规则。已发布快照、会话、作答、导出和审计记录保持不变。
- 开启班级中心后，班级页分为概览、学生表现、实验分析和班级管理；成员管理仍使用原有表单、权限、CSRF 和审计路径。
- 教师可在“学生端显示设置”中控制成绩、正确率、逐题结果、班级平均、本人位次、KC 和趋势。完整排行榜、学生过程轨迹和 KT/CD 推断不属于学生端 v1。

### 题库与知识组件

- 每个课程题库可维护题目、选项、答案、解析、难度和主/次 KC 映射。
- 题目可通过 CSV/XLSX 文件导入，KC 对应关系可单独批量导入。
- 课程题目使用稳定题号。删除题目后不会重排其他题目，也不会复用已产生的稳定代码。
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
- 实名 KT CSV 和实名导航 CSV（保留既有 URL 路径）。
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

支持 CSV/XLSX。题型、选项和答案须满足导入页面给出的校验规则。

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

## 独立前端运行与切换

师生业务页面使用独立构建的 Vue HTML；Django 保留 Session 认证、DRF API、文件下载和 `/admin/`。业务 URL 沿用原路径，未知 API 不会返回前端 HTML。

- 本地启动：当前 checkout 的 `.venv` 运行 `scripts/run_dev.py`。启动器先构建前端，再启动构建监听与同域 Django 服务。手工启动时先在 `frontend` 执行 `npm ci`、`npm run build`，设置 `VUE_STANDALONE_ENABLED=1` 后运行 Django。
- 前端与后端可分别构建。生产网关使用 `nginx/kt-platform.conf`；Django 设置 `FRONTEND_INTERNAL_REDIRECT=/__frontend/index.html`，由 Nginx 内部提供前端 HTML。此变量不可用于没有对应 Nginx 配置的本地服务。
- 分批切换使用 `VUE_STANDALONE_COURSE_IDS`、`VUE_STANDALONE_USER_IDS`；空名单表示全量。旧模块开关和模板暂留以支持退出条件验收，正常启动默认使用独立前端。代码中保留的旧模板不代表本轮已经完成物理删除。
- 采集器及离线 Worker 随前端构建，Worker 继续使用原 URL 和 scope。Nginx 的 `frontend_assets` 持久卷保留旧哈希资源，启动脚本追加新版本文件；发布及回滚不删除该卷，不清理 IndexedDB 或待上传队列。
- 本轮便携 PostgreSQL 与本地浏览器验证不替代生产 Nginx 容器联调、真实中文输入法或真实浏览器 200% 缩放检查。验证范围与截图见 `docs/ui/UI-AUDIT.md`。
