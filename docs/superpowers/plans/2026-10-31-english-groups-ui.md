# 英语组合题、导入评分与全平台视觉适配实施规格 R3

## 后续用户修订：正式流程与导出范围

以下为2026-10-31实施期间的最新明确指令，优先于下方原始R3规格的相冲突内容：

- 用户指出独立实验结果中的字段复选Excel界面是旧版本，要求使用现行导出。
- 用户明确：“所有的导出都复用之前的就行了……之前不也是采用sessionID下的每题一次记录为一行，题组复用之前的就行了”。
- 对正式流程边界，用户选择：“统一进入课程实验，复用现有导出”。
- 课程内与未绑定课程题库均保留题组上传、预览、确认和整组编辑。
- 新题组正式发布、学生作答、结果与导出统一使用课程实验、班级任务与现行标准ZIP流程，不新增无课程题组作答入口。
- 导出继续沿用现行research-export-v2及其文件/逐题记录结构；每个SessionID下每个小题一行。撤回本轮新增的v3分支、题组专用CSV、额外CSV字段及旧Excel界面扩展。
- 原有历史数据和既有下载接口保持兼容；不为本需求创建课程外导出系统、虚构课程或自动迁移KC。
- 验收改为：两个题库范围均真实导入/编辑18小题；课程实验完成发布、作答、提交、结果及现行标准ZIP导出18条逐题记录。

下方规格按上述修订执行；最初的“两套实验发布/作答入口”和“新增导出契约/专表”要求已撤回。实际实现与验收状态见同目录 progress 文件。

本版完整替代前面的计划，作为后续实施与交接依据。它包含视觉来源、代码职责、数据契约、实施顺序、验收条件和中断恢复规则。

**实施状态：用户已授权执行 R3。先完成 P0 归档；实际阶段结果见同目录 progress 文件。**

## 一、固定项目基线与视觉依据

### 1. 实施仓库

- 仓库：`E:\ATOP\kt-data-platform-complete`
- 本次核对的基线提交：`8f5ad282d201c14a4e6281e663a43b11abfcdd21`
- 架构：Django／DRF 提供业务权威，Vue／TypeScript 提供独立前端。
- 现有未跟踪研究目录不属于本任务，不删除、不覆盖。
- 不创建分支、提交、推送或部署；这些不属于本次本地实施范围。

基线提交只用于记录调查起点。实施前重新查看 `git status` 和实际差异，保留此后出现的用户修改。

### 2. Sites 原型是明确的视觉来源

已经核实的在线参考：

- [英语组合题演示：首页](https://kt-english-exercise-demo-1031.mythicflea258.chatgpt.site)
- [阅读理解界面](https://kt-english-exercise-demo-1031.mythicflea258.chatgpt.site/#reading)
- [短文填空界面](https://kt-english-exercise-demo-1031.mythicflea258.chatgpt.site/#cloze)

参考元数据：

| 项目 | 固定记录 |
|---|---|
| Sites 名称 | KT Data Lab · 英语组合题作答演示 |
| Sites 项目标识 | `appgprj_6ae5c6080e048191b73f04292bf503c7` |
| 本次参考版本 | 第 1 版 |
| 版本标识 | `appgprj_6ae5c6080e048191b73f04292bf503c7~appgver_de7818d807ac81918c7397f6ea1ae5c9` |
| Sites 返回的源码提交 | `22ca1da5e778761eb11e3315cd6b82836ec16427` |

本地参考源码：

- [index.html：页面结构与静态材料](/C:/Users/31606/.codex/visualizations/2026/10/31/01a19187-55f6-7632-bb30-a6c526da3bdb/english-exercise-demo/dist/index.html)
- [styles.css：颜色、字体、字号、间距和响应式样式](/C:/Users/31606/.codex/visualizations/2026/10/31/01a19187-55f6-7632-bb30-a6c526da3bdb/english-exercise-demo/dist/styles.css)
- [app.js：演示交互与示例题目](/C:/Users/31606/.codex/visualizations/2026/10/31/01a19187-55f6-7632-bb30-a6c526da3bdb/english-exercise-demo/dist/app.js)

本次读取的本地 `styles.css` SHA256：

```text
6812258D2673D4865D0E67FBFC3FE354E74AA85A7B79F38D7EE85142D1F3848B
```

该指纹标识本地参考文件，不代替线上版本与源码一致性的验证。

用户批注截图也必须保存：

- [工作区比例批注](/C:/Users/31606/AppData/Local/Temp/codex-clipboard-8a43e406-ecec-4411-9a29-17f6332d0359.png)
- [顶部冗余内容批注](/C:/Users/31606/AppData/Local/Temp/codex-clipboard-f37d5d2d-c97a-4826-ad1a-1734acd808c6.png)

**这些截图目前位于临时目录，P0 必须复制到仓库参考目录，不能继续把临时路径作为唯一依据。**

### 3. 视觉依据的优先级

发生冲突时按以下顺序执行：

1. 用户后续明确提出的修改。
2. 本规格中已经明确的尺寸、字体、布局和交互要求。
3. 用户批注截图。
4. Sites 第 1 版及其归档源码。
5. 仓库现有设计规范和公共组件。

Sites 中的本地保存、前端判分、演示重置、示例统计不属于正式平台的业务实现依据。

### 4. 保留与修正的具体对应关系

| Sites 原型位置 | 保留内容 | 正式平台的修正 |
|---|---|---|
| `:root` | 深蓝、冷灰、白色配色 | 映射到现有 `--kt-*` 变量 |
| `.brand`、`.brand-mark` | 克制的品牌层级 | 复用平台品牌与导航，不复制演示账号 |
| `.passage`、文章标题 | Georgia 英文衬线风格 | 放大字号，调整行高 |
| `.option` | 浅蓝选中背景、细边框 | 放大文字与点击区域 |
| `.site-header` | 白色顶部栏 | 固定视口顶部 |
| `.page`、`.header-inner` | 对齐关系 | 取消 1512px 整体限宽和大侧边留白 |
| `.page-heading` | 无需保留宣传区 | 删除大标题及重复介绍，改为紧凑任务栏 |
| `.cloze-material` | 原文内填空 | 取消第二层居中窄容器 |
| `.section-kicker`、微型标签 | 必要的信息含义 | 删除装饰标签；必要文字不低于 14px |
| `.save-card` | 明确保存状态 | 改为服务器确认状态，失败时不得显示“已保存” |

## 二、必须保持的产品范围与行为

### 1. 本次交付范围

- 课程内题库、未绑定课程题库均支持新题组。
- 首版题组只有两种：
  - 阅读理解：共享文章＋多个单选小题。
  - 短文填空：共享文章＋多个文本输入空位。
- 原有六种独立题型继续保留。
- 所有题目支持设置分值，空值默认 1 分。
- 英语文本精确匹配，默认忽略大小写和多余空白，教师可设置大小写敏感。
- 完成上传、预览、确认、编辑、发布、作答、评分、结果和导出闭环。
- 全部注册 Vue 业务页面统一视觉；Django `/admin/` 和下载文档版式不在视觉替换范围。

首版不加入普通试卷自动识别、OCR、图片题组、共享词库、模糊判分或语义判分。

### 2. 题组发布支持范围

| 配置 | 首版处理 |
|---|---|
| 全量题池 | 支持 |
| 题组之间固定排序 | 支持 |
| 题组之间随机排序 | 支持，复用现有确定性随机根 |
| 组内顺序 | 固定 |
| 无时限／整卷限时 | 支持 |
| 整卷提交 | 支持 |
| 配额抽题 | 题组发布时拒绝 |
| KC 分层排序 | 题组发布时拒绝 |
| 逐小题限时 | 题组发布时拒绝 |
| 筛选只留下部分组员 | 明确报错，不自动拆组 |

上述限制只作用于新题组配置，不取消已有独立题能力。

## 三、P0：先建立可恢复的项目资料

### 1. 拟创建的持久文件

| 拟创建路径 | 内容 |
|---|---|
| `E:\ATOP\kt-data-platform-complete\docs\superpowers\plans\2026-10-31-english-groups-ui.md` | 本完整规格，包括所有已确定决策 |
| `E:\ATOP\kt-data-platform-complete\docs\superpowers\plans\2026-10-31-english-groups-ui-progress.md` | 阶段状态、已改文件、验证结果、阻塞项、下一步 |
| `E:\ATOP\kt-data-platform-complete\docs\design\english-groups-ui-20261031\visual-spec.md` | 视觉规则、原型选择器对应关系、字号表、布局图 |
| `E:\ATOP\kt-data-platform-complete\docs\design\english-groups-ui-20261031\reference\manifest.json` | Sites URL、项目／版本标识、原始路径、文件指纹、来源说明 |
| `E:\ATOP\kt-data-platform-complete\docs\design\english-groups-ui-20261031\reference\site-v1\` | 原样保存 `index.html`、`styles.css`、`app.js` |
| `E:\ATOP\kt-data-platform-complete\docs\design\english-groups-ui-20261031\reference\annotations\` | 两张批注截图，改为稳定文件名 |
| `E:\ATOP\kt-data-platform-complete\docs\design\english-groups-ui-20261031\route-checklist.md` | 从注册路由生成的逐页面验收清单 |
| `E:\ATOP\kt-data-platform-complete\artifacts\english-groups-ui-20261031\` | 实际测试日志、截图、失败追踪和验收结果 |

参考副本保存原貌。修订样式写入平台代码与 `visual-spec.md`，不覆盖原始参考，确保以后还能判断哪些变化来自用户要求。

### 2. 进度记录格式

每个阶段固定记录：

```text
阶段：
状态：未开始 / 进行中 / 已实现未验证 / 已验证 / 阻塞
对应规格版本：
实际修改文件及职责：
已完成行为：
已运行命令、时间、退出码：
结果和截图路径：
未完成事项：
具体下一步：
影响后续阶段的决定：
```

不能仅写“P3 完成”。没有命令和结果证据的阶段，不能标记为“已验证”。

### 3. 恢复顺序

发生上下文切换、会话中断或人员交接时：

1. 阅读主计划、进度文件和视觉规格。
2. 检查参考清单及归档文件是否完整。
3. 查看当前 `git status`、相关 `git diff`。
4. 对照最后一次验证，确认哪些代码已经落盘。
5. 从进度文件的“具体下一步”继续。
6. 只有相关代码变化、测试失败或证据缺失时，才重跑对应检查。

每阶段结束、出现阻塞、准备交接时更新进度。**P0 未完成，不进入数据库或运行契约修改。**

## 四、视觉规格与前端代码责任

### 1. 固定颜色和字体

| 用途 | 目标 |
|---|---|
| 品牌深蓝／主要按钮 | `#182d49` |
| 交互蓝 | `#285dbb` |
| 选中浅蓝背景 | `#edf3ff` |
| 页面背景 | `#f5f7fa` |
| 内容背景 | `#ffffff` |
| 正文 | `#26364c` |
| 次要文字 | 以 `#69788a` 为参考，按实际背景校验对比度 |
| 分隔线 | `#e3e9f0` |
| 英语文章 | `Georgia, "Times New Roman", serif` |
| 中文与界面文字 | 现有 Segoe UI／微软雅黑／苹方／系统无衬线字体栈 |
| 数字 | `font-variant-numeric: tabular-nums` |

无需下载字体或增加字体服务。成功、警告、错误继续使用语义颜色，并同时保留文字或图形状态。

### 2. 字号规格

| 内容 | 桌面 | 手机 | 行高 |
|---|---:|---:|---:|
| 页面标题 | 26px | 22px | 1.3 |
| 英文文章标题 | 28px | 24px | 1.3 |
| 阅读文章正文 | 20px | 19px | 1.8 |
| 短文填空正文 | 20px | 19px | 2.3，按控件高度校验 |
| 小题题干 | 18px | 18px | 1.6 |
| 选项 | 17px | 16px | 1.6 |
| 填空输入 | 19px | 18px | 控件内垂直居中 |
| 导航、按钮、表单 | 16px | 16px | 1.5 |
| 管理表格正文 | 15px | 16px | 1.5 |
| 说明、分值、状态、题号 | ≥14px | ≥14px | ≥1.5 |

实际采用 `rem`。不通过缩小根字号或整体 `transform: scale()` 适配手机。

英文按单词换行，长串提供溢出保护。正文不采用全局逐字符断词，不自动改写大小写、标点或撇号。

### 3. 工作区布局

桌面学生页面：

```text
固定全局栏：品牌／现有全局操作                         64px

紧凑任务栏：任务名称／题组切换／必要时间信息             约48–56px

┌──────────────────────────────────┬──────────────┐
│ 阅读：文章约55% │ 问题约45%       │ 答题卡       │
│                                  │ 264px        │
│ 或：短文填空完整主区              │ 保存状态     │
│                                  │ 提交操作     │
└──────────────────────────────────┴──────────────┘
```

明确规则：

- 工作区占可用宽度至少 90%，左右常规边距 24–32px。
- 手机边距 16px。
- 取消作答、导入和数据列表的整体窄容器。
- 阅读文章行长控制在约 70–80 个字符；填空正文约 90–100 个字符，约束仅作用于文字阅读区域。
- 内容面板常规内边距 20–24px；手机 16px。
- 桌面作答采用视口高度工作区，阅读左右区域分别滚动，短文填空主区滚动。
- 宽度不足 1280px 时，答题卡进入抽屉，保留显式入口。
- 宽度不足 1024px 时，阅读改为上下排列，使用页面滚动，避免多层滚动容器。
- 200% 缩放按实际可用宽度进入对应响应式布局。
- 顶部栏常规 64px／手机 56px；需要换行时自然增高，内容偏移同步实际高度。
- 答题卡、错误定位和键盘焦点不得被顶部栏遮挡。

删除宣传性大标题、重复题型标签和重复说明；保留真实任务名称、文章标题、必要作答规则、截止时间、保存失败及提交风险提示。

### 4. 修改哪些前端代码

| 现有代码 | 当前职责 | 本次修改 |
|---|---|---|
| [tokens.css](/E:/ATOP/kt-data-platform-complete/client/src/design-system/tokens.css) | 全局颜色、字号、间距、层级变量 | 映射 Sites 配色；建立文章、题干、选项和辅助信息字号变量 |
| [foundations.css](/E:/ATOP/kt-data-platform-complete/client/src/design-system/foundations.css) | 基础排版、按钮、表格、全局栏 | 调整基础字号、控件尺寸、对比度和顶部栏布局 |
| [workspace.css](/E:/ATOP/kt-data-platform-complete/client/src/design-system/workspace.css) | 教师／学生工作区、侧栏及主区 | 统一顶部偏移，压缩无效留白，扩大内容宽度 |
| [AppHeader.vue](/E:/ATOP/kt-data-platform-complete/client/src/design-system/components/AppHeader.vue) | 品牌、账号、退出等全局操作 | 固定顶部；保留现有行为；实际高度变化时更新布局偏移 |
| [WorkspaceShell.vue](/E:/ATOP/kt-data-platform-complete/client/src/design-system/components/WorkspaceShell.vue) | 工作区骨架、菜单、移动导航 | 适配固定顶部和主区偏移，保留菜单焦点与关闭恢复 |
| [FocusAssessmentHeader.vue](/E:/ATOP/kt-data-platform-complete/client/src/design-system/components/FocusAssessmentHeader.vue) | 测评进度栏 | 调整为紧凑任务信息区，避免与全局栏重复 |
| [student-assessment.css](/E:/ATOP/kt-data-platform-complete/client/src/assessment-runtime/student-assessment.css) | 正式作答页面样式 | 实现题组布局、字号、答题卡和滚动规则 |
| [question-import.css](/E:/ATOP/kt-data-platform-complete/client/src/features/teacher-bank-management/question-import.css) | 导入预览布局 | 宽幅预览、紧凑工具栏、错误定位和移动布局 |

全站推广时逐模块处理局部硬编码样式，不能只修改全局颜色后宣布完成。验收范围由 [frontend-routes.json](/E:/ATOP/kt-data-platform-complete/contracts/frontend-routes.json) 生成，逐路由登记。

## 五、业务实施阶段及代码职责

### P1：题组模型、完整性和所有题目分值

**目标：数据库能够表达共享材料及独立小题，且不会产生跨题库关联或不完整可用题组。**

| 代码位置 | 当前职责 | 计划修改 |
|---|---|---|
| [question_bank/models.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/models.py:305) | Question、选项、题库、预览批次 | 新增题组模型；Question 增加组关系、顺序、空位、分值、语言及文本匹配设置 |
| [question_readiness.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/question_readiness.py) | `content_issues()`、题目可用性校验 | 增加题组上下文；独立题仍要求题干，短文空位允许由文章提供上下文 |
| [forms.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/forms.py:78) | 题目编辑数据校验 | 接受分值和大小写规则；沿用服务校验结果 |
| [lifecycle.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/lifecycle.py) | 删除题目与题库的引用保护 | 删除、归档操作理解完整题组 |
| [import_service.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/import_service.py:225) | `copy_shared_bank()` 等复制导入 | 复制完整题组及成员关系，不复用源题组外键 |

新增服务文件：

`E:\ATOP\kt-data-platform-complete\server\apps\question_bank\services\question_groups.py`

职责限定为：题组结构校验、材料空位解析、成员一致性、整组保存与修订控制。导入、编辑和发布共同调用，避免三套校验。

数据决定：

| 实体 | 字段／规则 |
|---|---|
| QuestionGroup | `bank`、`kind`、`title`、`instructions`、`material`、`content_language`、`revision` |
| Question | 可空 `group`、`group_order`、`blank_id` |
| 所有 Question | `max_score`，默认 `1.00`，范围 `0.01–999.99` |
| 文本题 | `text_match_case_sensitive`，默认 false；`text_match_policy_version` |
| QuestionGroup.kind | `reading` 或 `cloze` |
| 语言 | 新英语模板默认 `en`；旧题没有声明时为 `und`，不按字符比例猜测 |

完整性要求：

- 成员与题组必须属于同一题库。
- 组内顺序唯一；发布前必须形成完整顺序。
- 阅读组首版成员必须是单选题。
- 填空组首版成员必须是文本填空，空位编号唯一。
- 材料中的空位与成员一一对应。
- 题组可用性由结构及成员可用性统一计算，不提供可绕过成员校验的独立“启用”开关。
- 整组编辑采用 `expected_revision`；修订冲突返回 409。
- 已被发布引用的历史内容仍通过快照保留。
- 本地未发布结构按仓库开发库重建规则处理，不为中间演示状态连续增加补丁迁移；不得对真实或部署数据应用开发库清理。

**阶段验收：** 能建立阅读三小题和短文十五空；跨库、重复顺序、重复空位、缺失空位均被拒绝。

---

### P2：三种文件解析及两套导入入口

**目标：从真实文件得到明确题组，而不是仅在前端把若干行看起来放在一起。**

| 代码位置 | 当前职责 | 计划修改 |
|---|---|---|
| [unified_reader.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/unified_reader.py) | `HEADERS`、`read_unified_rows()`；严格表头与单工作表读取 | 支持旧模板和明确的新模板版本，保留文件类型、公式和不支持对象检查 |
| [word_import.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/word_import.py) | Word 标签解析 | 增加题组开始／结束、材料、小题和空位标签 |
| [import_parsing.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/import_parsing.py) | `ParsedQuestion`、`parse_question_rows()` | 承载分值、匹配规则和题组元数据，保留原题型语义 |
| [unified_import.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/unified_import.py:212) | `preview()`、`confirm()`、逐行分类及去重 | 改为先解析行、再组装题组、再校验和去重；确认时按整组写入 |
| [preview_batch.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/preview_batch.py) | 预览会话绑定、过期、锁定及消费 | 支持合法的未绑定课程题库批次，保持课程批次原有绑定强度 |
| [import_templates.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/import_templates.py) | Excel／CSV／Word 下载模板 | 增加新模板和完整阅读、填空样例 |

**必须处理的现有差异：**

课程导入已经使用 `UnifiedQuestionImportService` 和数据库批次；未绑定课程导入在 [management_views.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/api/management_views.py:1365) 中仍通过 Session 暂存解析结果。

统一方案：

- 新题组导入共用持久化预览服务。
- `ImportPreviewBatch.course` 允许在“未绑定课程题库的题目内容导入”场景为空。
- 课程 KC、课程题目 KC 批次仍必须绑定课程。
- 未绑定课程批次校验题库、教师、会话和过期时间，不伪造课程。
- 课程批次继续检查课程题号身份指纹。
- 旧未绑定课程模板保留原序号冲突校验；新题组模板采用服务器分配题号。
- 不改变两套入口各自的权限和 KC 范围。

新模板增加：

```text
题组编号、题组形式、材料标题、作答说明、共享材料、
组内顺序、空位编号、内容语言、分值、大小写敏感
```

解析决定：

- 同组每行填写编号；共享材料可只填写一次。
- 重复的材料、标题、说明必须一致。
- 分值空白为 1；大小写敏感空白为“否”。
- 文件题组编号仅在本次文件内关联。
- Word 使用明确标签，不通过缩进、字号和相邻位置猜测。
- 结构错误为 `invalid`；结构完整但答案或必要 KC 不全为 `draft`。
- 去重包含材料、形式、语言、成员顺序、空位、答案、分值、匹配规则和 KC；不包含本次文件题组编号。

确认接口保留 `preview_id`、`selected_row_ids`，新增 `selected_group_ids`：

- `selected_row_ids` 用于独立题。
- 题组通过 `selected_group_ids` 选择，成员不得单独确认。
- 旧普通题确认行为保留。
- 同一批次的重复确认返回原结果，不重复创建。
- 新题组确认记录选择内容；消费后更换选择内容返回冲突。
- 重新校验权限和批次状态后，在事务中写入；失败不留下残缺题组。

**阶段验收：** 新旧 Excel／CSV／Word、混合独立题与题组、整组草稿、过期批次、跨用户确认、并发重复确认及事务回滚通过。

---

### P3：教师导入预览、题组编辑与共享呈现

**目标：教师能看到文件解析后的真实结构和学生最终呈现。**

| 代码位置 | 当前职责 | 计划修改 |
|---|---|---|
| [TeacherQuestionImport.vue](/E:/ATOP/kt-data-platform-complete/client/src/features/teacher-bank-management/TeacherQuestionImport.vue) | 上传、筛选、行选择、详情、确认 | 增加题组行、整组选择、组级错误和学生效果 |
| [TeacherStandaloneQuestionImport.vue](/E:/ATOP/kt-data-platform-complete/client/src/features/teacher-bank-management/TeacherStandaloneQuestionImport.vue) | 未绑定课程导入 | 使用同一题组预览能力，保留独立入口 |
| [api.ts](/E:/ATOP/kt-data-platform-complete/client/src/features/teacher-bank-management/api.ts) 与 [standalone-api.ts](/E:/ATOP/kt-data-platform-complete/client/src/features/teacher-bank-management/standalone-api.ts) | 两套教师接口类型和请求 | 增加题组、分值、规则、选择和修订字段 |
| [management_serializers.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/api/management_serializers.py) | 教师请求／响应契约 | 扩展预览组、确认结果和编辑契约 |
| [management_views.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/api/management_views.py) | 权限、服务调用和响应 | 两套入口调用共享服务；不在视图另写判定规则 |
| [management_urls.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/api/management_urls.py) | 管理 API 路由 | 增加整组编辑 API，保留现有 URL |

新增组件：

- `E:\ATOP\kt-data-platform-complete\client\src\question-content\QuestionGroupContent.vue`
- `E:\ATOP\kt-data-platform-complete\client\src\question-content\question-group-content.ts`
- `E:\ATOP\kt-data-platform-complete\client\src\question-content\question-group-content.css`
- `E:\ATOP\kt-data-platform-complete\client\src\features\teacher-bank-management\QuestionGroupEditor.vue`

职责：

- `QuestionGroupContent.vue` 只呈现文章、问题、选项和空位，并发出激活／答案变化事件。
- 它不判分、不决定反馈权限、不直接保存服务器数据。
- 教师预览和正式学生端共同使用它。
- `QuestionGroupEditor.vue` 负责整组文章和成员编辑；服务器负责验证、修订冲突和原子提交。

预览结构：

```text
工具栏：文件／重新上传／筛选／题组数／小题数／总分／确认

题组列表                          当前题组详情
阅读 R001 · 3小题 · 可用           教师核对 | 学生效果
填空 C001 · 15空 · 需修正          材料、成员、分值、规则、KC
                                  或完整学生呈现
```

具体行为：

- 上传后收起冗长模板说明，保留重新上传入口。
- 列表按组展开，普通题维持单行。
- 错误能定位到文件行、题组、小题和空位。
- “教师核对”显示答案及校验信息；“学生效果”不显示答案、解析或正确性。
- 学生效果中的试填只保留在当前预览内，不进入正式作答记录。
- 首版不增加预览表内任意编辑；修改源文件重新上传，或导入后进入题组编辑。
- 修改题组结构、删除成员时先展示影响，明确确认后按修订号提交。
- 原单题编辑入口发现成员属于题组时，引导进入整组编辑，避免破坏完整性。

**阶段验收：** 两个入口均能按组核对、定位错误、选择导入、重新打开编辑；预览与正式呈现使用同一组件。

---

### P4：英语匹配与分值贯通

**目标：分值真正进入自动评分、人工评分和汇总，不只出现在题目表单。**

| 代码位置 | 当前职责 | 计划修改 |
|---|---|---|
| [scoring.py](/E:/ATOP/kt-data-platform-complete/server/apps/question_bank/services/scoring.py:46) | `score_payload()` 返回正误与 0／1 得分 | 接受冻结满分及大小写规则，正确时返回满分 |
| [assessment/services.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/services.py) | 单题提交、评分调用、GradeRecord 写入 | 所有评分入口从快照传入分值与规则 |
| [session_submission_services.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/session_submission_services.py) | 整卷提交 | 逐小题应用冻结规则，保留整卷幂等 |
| [feedback_services.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/feedback_services.py:290) | `initial_grading_result()`、反馈状态 | 移除新题固定满分为 1 的假设，保留待评语义 |
| [manual_grading_services.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/manual_grading_services.py:160) | 人工简答评分 | 既有二元人工评分按题目满分给分 |
| [TeacherShortAnswerGrading.vue](/E:/ATOP/kt-data-platform-complete/client/src/features/teacher-experiments/TeacherShortAnswerGrading.vue) | 人工评分界面 | 显示冻结满分及实际得分 |

匹配策略固定为：

```text
text-exact-v1
1. 去除首尾空白。
2. 连续空白折叠为一个空格。
3. 大小写不敏感时执行 casefold。
4. 与标准答案及显式可接受答案匹配。
```

大小写开关只影响文本答案，不改变选项编号、判断题和数值容差规则。

不自动等价化撇号、连字符、缩写、词形和同义词；确需接受的形式由教师列入可接受答案。

评分汇总：

- 单题使用 Decimal，避免浮点累计误差。
- 卷面满分取全部冻结小题满分之和。
- 已评分得分、已评分满分、待评数量分别表达。
- 正确率依据正误；得分率依据得分和满分。
- 不把待评当零分已判错。
- 旧快照缺少字段时按旧规则解释，不改写既有 GradeRecord。

**固定验收样例：**

阅读三题，每题 2 分；填空十五空，每空 1 分：

- 卷面满分为 21 分。
- 阅读答对 2 题、填空答对 12 空，且全部作答：得分为 16／21。
- 正确小题为 14／18，不能把 16／21 写成正确率。

---

### P5：冻结发布、整组排序与预备内容

**目标：已发布试卷不受题库后续编辑影响，随机化也不会拆组。**

| 代码位置 | 当前职责 | 计划修改 |
|---|---|---|
| [experiments/models.py](/E:/ATOP/kt-data-platform-complete/server/apps/experiments/models.py:1347) | QuestionSnapshot、ItemInstance 等冻结实体 | 新增题组快照；小题快照冻结组关系、分值和规则 |
| [publication_services.py](/E:/ATOP/kt-data-platform-complete/server/apps/experiments/publication_services.py) | `generate_preview()`、指纹、创建快照、`commit_publish_plan()` | 纳入题组内容、配置限制、整组顺序和过期预览判定 |
| [experiments/services.py](/E:/ATOP/kt-data-platform-complete/server/apps/experiments/services.py:769) | 历史独立实验发布、`select_snapshots()`、`ordered_snapshots()` | 保留既有独立题行为；新题组正式发布由中心校验限制为课程实验 |
| [sampling.py](/E:/ATOP/kt-data-platform-complete/server/apps/experiments/sampling.py) | 候选筛选与抽样 | 保留原算法；题组首版在入口拒绝不支持配置 |
| [prepared_services.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/prepared_services.py) | `_projection()`、预备试卷和清单 | 增加授权题组呈现和成员导航 |
| [prepared_content.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/prepared_content.py) | 学生可见内容的持久化与去重 | 持久化题组展示投影，不包含标准答案 |

新增 `QuestionGroupSnapshot` 保存共享材料、形式、说明、语言及冻结成员关联；小题仍保留各自 QuestionSnapshot 和身份。

发布指纹必须包含题组内容。教师修改文章、空位、分值或匹配规则后，尚未提交的旧发布预览失效。

排序规则：

1. 把完整题组和独立题构成呈现单位。
2. 使用现有派生排序种子排列单位。
3. 题组内部按冻结顺序展开。
4. 仍生成每小题的 assignment。
5. 没有题组的旧流程继续使用原算法，不改变旧随机结果。

新题组采用新的运行能力版本和预备清单版本；旧测评、已准备会话及历史资源保留原契约，不在升级时切换。

**阶段验收：** 两种题库均能导入和编辑；课程题组通过 A+ 预览与提交；无课程题组正式发布明确拒绝；组不拆散；编辑题库不改变已发布材料；学生内容包中没有答案或判分规则机密。

---

### P6：学生题组作答与逐小题可靠保存

**目标：同时看到多个问题，但每次输入仍准确归属于对应 Attempt。**

| 代码位置 | 当前职责 | 计划修改 |
|---|---|---|
| [StudentAssessment.vue](/E:/ATOP/kt-data-platform-complete/client/src/assessment-runtime/StudentAssessment.vue) | 当前题展示、输入、保存、导航、提交 | 接入共享题组组件，维护按小题索引的响应视图 |
| [session-controller.ts](/E:/ATOP/kt-data-platform-complete/client/src/assessment-runtime/session-controller.ts:103) | SessionController、进入与活动运行实例 | 串行处理题组内激活，保持单一有效写入目标 |
| [prepared-paper.ts](/E:/ATOP/kt-data-platform-complete/client/src/assessment-runtime/prepared-paper.ts) | 清单、内容解密、缓存和提交版本 | 支持新版题组清单，保留旧版本读取 |
| [api.ts](/E:/ATOP/kt-data-platform-complete/client/src/assessment-runtime/api.ts) | 学生运行请求及类型 | 承载授权题组投影和成员身份 |
| [saved_navigation_services.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/saved_navigation_services.py) | 保存离开、释放租约、导航回执 | 用于组内小题切换，保留持久化与幂等 |
| [event_schema_registry.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/event_schema_registry.py) | 过程事件类型及模式登记 | 注册新版题组展示事件，保持旧事件模式不变 |

**组内切换必须遵循以下顺序：**

1. 记录用户准备进入的小题及原始点击意图。
2. 将原小题待确认输入按既有边界规则落盘。
3. 完成原小题保存离开及租约释放。
4. 服务器确认进入新小题，返回正确的 assignment、Attempt、响应版本及租约。
5. 激活新控件并应用用户的点击／输入意图。
6. 失败时保留原输入和可恢复状态，不能显示已切换成功或已保存。

实现要求：

- 不为十五个空同时建立十五个互相竞争的活动控制器。
- Tab 切换、鼠标选择和答题卡定位走同一激活流程。
- 单选点击等待激活时保留选择意图，确认后应用一次。
- IME 组合输入、清空和返回旧答案遵循原轨迹规则。
- 组内切换保留文章滚动位置，不重新挂载整篇文章。
- 保存状态按服务器回执展示。
- 断网、超时、租约冲突和恢复沿用既有持久化路径。
- 正式提交核对所有小题最新响应版本，并沿用整卷幂等与截止时间判定。

过程数据：

- 题组显示不生成所有小题的访问记录。
- 新事件记录“共享材料被呈现”，不能命名或解释为“已经阅读”。
- 共享材料展示区间与活动小题区间分别投影，不把整段时间重复分配给每个小题。
- 新事件模式放在 `E:\ATOP\kt-data-platform-complete\server\apps\assessment\event_schemas\`，随新契约登记和测试。
- 不修改旧会话冻结的事件模式、IndexedDB 数据和未确认材料。

**阶段验收：** 快速连续切换、首个字符输入、选择题点击、IME、清空、刷新恢复、断网、冲突、重复提交和截止时间场景均正确。

---

### P7：结果、教师评分和现行标准导出

| 代码位置 | 当前职责 | 计划修改 |
|---|---|---|
| [student_results.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/student_results.py) | 得分、正误和状态汇总 | 按冻结满分汇总，提供题组与小题层级 |
| [session_summary.py](/E:/ATOP/kt-data-platform-complete/server/apps/assessment/api/session_summary.py) | 会话摘要 API | 返回授权的组级汇总和逐小题信息 |
| [StudentResult.vue](/E:/ATOP/kt-data-platform-complete/client/src/features/student-results/StudentResult.vue) | 学生结果展示 | 呈现题组、小题、得分和待评状态 |
| [TeacherStandaloneExperimentResults.vue](/E:/ATOP/kt-data-platform-complete/client/src/features/teacher-experiments/TeacherStandaloneExperimentResults.vue) | 历史独立实验结果 | 移除旧字段复选 Excel 界面；新正式测评引导进入课程工作区 |
| [student_result_snapshots.py](/E:/ATOP/kt-data-platform-complete/server/apps/analytics/student_result_snapshots.py) | 学生结果快照及可见性 | 保留服务器策略，处理新版得分字段 |
| [research_export_v2.py](/E:/ATOP/kt-data-platform-complete/server/apps/analytics/research_export_v2.py) | 现行标准研究导出 | 复用 v2 文件与逐题记录结构；共享材料仅随既有题库 JSON 提供 |

导出直接复用现行 `research-export-v2`：7 个 CSV 与 1 个题库 JSON，每个 SessionID 下每个小题一条逐题记录。不新增 v3、题组专用 CSV、额外 CSV 列或旧 Excel 字段。历史冻结产物与既有下载接口保持兼容。

既有题库 JSON 中补充共享材料、题组形式、组内顺序和空位编号，原小题身份保持可追溯。每小题一行是记录粒度，不能据此把共享材料下的小题视为统计独立测量。

---

### P8：全站视觉推广

以 P3 和 P6 的已验收页面为样板，分模块推广：

| 模块 | 主要处理 |
|---|---|
| 登录和账号 | 字号、表单、错误提示、按钮 |
| 课程和班级 | 页面标题、工具栏、列表与侧栏 |
| 课程题库和独立题库 | 题目列表、编辑器、导入及详情 |
| 实验与发布 | 表单、步骤、预览、状态、确认操作 |
| 人工评分 | 题目阅读、满分、答案和评分操作 |
| 数据中心 | 表格密度、筛选、统计层级、导出状态 |
| 学生任务与学习 | 任务卡、时间信息、材料和结果 |
| 全局状态 | 空白、加载、错误、弹窗、移动菜单 |

每页在路由清单中记录：实际 Vue 入口、主要样式文件、检查视口、发现问题、修复状态和截图路径。

只改视觉的页面不调整后端业务规则，不改变菜单层级或用隐藏控件解决布局问题。

## 六、公共接口与共享呈现约定

### 1. 教师预览接口

保持原有 `preview.rows`，新增 `preview.groups`。

题组至少包含：

```text
group_id：预览内稳定身份
source_group_code：文件内编号
kind：reading / cloze
title、instructions、content_language
material、material_segments
member_row_ids：按组内顺序排列
classification：ready / draft / duplicate / invalid
errors：包含来源位置
item_count、total_max_score
```

`material_segments` 由服务器解析为文本段与空位引用，前端不再自行推断哪个空属于哪个小题。

### 2. 学生接口

增加授权的 `question_group` 和呈现单位清单：

- 共享材料、语言、显示顺序。
- 小题 assignment／Attempt 身份。
- 题干、选项及允许显示的分值。
- 服务器提供的状态和操作能力。
- 不包含禁止展示的答案、解析、正确性或教师校验信息。

### 3. 共享前端组件

`QuestionGroupContent.vue` 接收：

```text
group
responsesByItemKey
activeItemKey
pendingItemKey
readonly
```

输出：

```text
request-activate
answer-change
```

教师预览的 item key 使用预览行或题目身份；学生端使用服务器提供的作答身份。组件不把题号文本当作数据库身份。

材料按纯文本和受控空位节点渲染，不直接执行上传 HTML。

### 4. API 生成链

服务／序列化器改变后：

1. 更新 [openapi.yaml](/E:/ATOP/kt-data-platform-complete/contracts/openapi.yaml)。
2. 通过已有命令生成 [schema.d.ts](/E:/ATOP/kt-data-platform-complete/client/src/api/generated/schema.d.ts)。
3. 更新相关前端封装及契约测试。
4. 不手改生成类型以掩盖后端契约不一致。

## 七、测试、命令与阶段通过条件

### 1. 固定测试材料

建立两套正向样例：

- 阅读理解：1 篇文章、3 道单选题，每题 A–D，分值分别明确设置。
- 短文填空：1 篇文章、15 个空，每空独立答案、可接受答案及规则。

两套样例同时覆盖课程题库与未绑定课程题库。课程样例使用有效现有 KC；导入不自动创建 KC。

负向样例包含：材料冲突、重复空位、遗漏成员、跨库关联、缺失答案、非法分值、无效 KC、未知表头、重复确认和过期批次。

### 2. 测试责任表

| 阶段 | 现有测试入口／拟新增测试 | 核心断言 |
|---|---|---|
| P1 | `apps.question_bank.test_question_readiness`；新增 `apps.question_bank.test_question_groups` | 完整性、同库约束、修订冲突 |
| P2 | `test_unified_question_import`、`test_word_question_import`、`test_unified_import_templates` | 新旧格式、分组、去重、原子性 |
| P3 | `TeacherBankManagement.test.ts`、`TeacherStandaloneQuestionImport.test.ts`；新增共享组件测试 | 整组选择、错误定位、预览无答案泄露 |
| P4 | `apps.question_bank.test_question_types`、`apps.assessment.test_question_types`、`test_manual_short_answer_grading` | 大小写、空白、分值、待评、历史兼容 |
| P5 | `test_group_snapshots`、`test_a_plus_publication`、`test_sampling_v2`、`test_prepared_content` | 课程正式流程、不拆组、快照冻结、随机一致；无课程题组发布拒绝 |
| P6 | `test_saved_navigation`、`test_session_submission`、`test_capture_v2_leases`；SessionController 与 StudentAssessment 单测 | 输入归属、保存、恢复、冲突、截止时间 |
| P7 | 学生结果快照与研究导出测试 | 分值汇总、权限、现行 v2 文件及逐题记录、历史不变 |
| P8 | `layout-audit.spec.ts`、`layout-expanded-audit.spec.ts` | 全站布局、键盘、缩放与状态 |

拟新增端到端测试：

`E:\ATOP\kt-data-platform-complete\client\e2e\english-question-groups.spec.ts`

流程必须从真实文件上传开始，经过确认、发布、学生作答、提交、教师结果和导出，不能全部使用模拟响应替代。

### 3. Windows 执行环境

执行阶段统一设置：

```powershell
$ktRoot = 'E:\ATOP\kt-data-platform-complete'
$ktPython = Join-Path $ktRoot '.venv\Scripts\python.exe'

$env:PATH = 'C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64;C:\Users\31606\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin;' + $env:PATH
$env:PLAYWRIGHT_BROWSERS_PATH = 'C:\Users\31606\Downloads\ATOP-tooling\pw-fresh-browsers'

$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '55432'
$env:POSTGRES_USER = 'kt_user'
$env:POSTGRES_PASSWORD = 'kt_password'
$env:POSTGRES_DB = 'kt_platform'
```

不使用其他 checkout 的 Python 环境，不替换成 SQLite，不重复尝试当前不可用的 Docker／WSL。

### 4. 检查命令

先运行对应阶段的窄测试。全部阶段集成后运行一次完整后端检查：

```powershell
Set-Location (Join-Path $ktRoot 'server')
& $ktPython manage.py check
& $ktPython manage.py makemigrations --check --dry-run
& $ktPython manage.py test
& $ktPython manage.py collectstatic --noinput
& $ktPython manage.py export_api_schema --check
```

接口发生变化的实施步骤使用：

```powershell
Set-Location (Join-Path $ktRoot 'server')
& $ktPython manage.py export_api_schema

Set-Location (Join-Path $ktRoot 'client')
npm.cmd run api:generate
```

前端集成检查：

```powershell
Set-Location (Join-Path $ktRoot 'client')
npm.cmd ci
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test:unit
npm.cmd run api:check
npm.cmd run build
```

E2E 必须切换到隔离库：

```powershell
$env:POSTGRES_DB = 'kt_e2e'
Set-Location (Join-Path $ktRoot 'client')

node scripts/prepare-e2e.mjs --capture-v2
npx.cmd playwright test e2e/english-question-groups.spec.ts --project=chromium
npx.cmd playwright test e2e/layout-audit.spec.ts e2e/layout-expanded-audit.spec.ts --project=chromium
```

现有准备脚本会重置 E2E 数据，必须保留其 `kt_e2e` 校验，不可在 `kt_platform` 上运行。Firefox／WebKit 补验题组输入、导航和提交关键路径。

每条命令失败后先处理具体错误；相关代码未变且已有有效通过证据时，不重复跑完整回归。

### 5. 视觉验收

固定检查：

- 1366×768。
- 1440×900。
- 1920×1080。
- 2560×1229。
- 390×844。
- 200% 浏览器缩放。
- 键盘操作、长英文、中英混排、长选项、错误与加载状态。

通过条件：

- 1440×900、100% 缩放时，作答工作区使用至少 90% 可用宽度，顶部起点距离视口顶端不超过约 144px。
- 全局栏滚动后仍可见。
- 文章、题干、选项达到字号规格。
- 页面无整体横向溢出，必要表格在自身容器内滚动。
- 填空编号、文字和输入框不重叠。
- 手机键盘打开后能看到当前输入。
- 状态不只靠颜色表达。
- 宣传标题及重复说明已移除，必要业务信息完整保留。

## 八、完成定义与交接内容

每个阶段只有在“代码实现、相关差异检查、对应测试和必要视觉证据”齐备后才能关闭。

最终交接必须提供：

1. 主计划与进度文件的实际路径。
2. Sites 参考归档及视觉规格路径。
3. 实际修改文件及各自承担的功能。
4. 两种题库导入/编辑与课程正式发布/作答/结果/标准导出的验收证据。
5. 新旧模板、评分、发布与运行契约兼容性结果。
6. 全站路由验收清单。
7. 已执行检查及具体未解决问题。
8. 下一步是否仍有工作，明确到代码位置和动作。

本轮已进入本地实现与验收，P0 归档、业务代码及检查证据均已落盘。**实际阶段状态和未完成项以 [progress 文件](2026-10-31-english-groups-ui-progress.md) 为准；不以本规格替代验收结果。**
