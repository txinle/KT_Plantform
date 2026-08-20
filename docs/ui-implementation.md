# UI 实现说明

## 技术选择

- **Django Templates**：页面渲染、权限边界和表单校验与后端保持同一套状态。
- **Bootstrap 5.3**：实现响应式网格、表单、卡片、表格和移动端布局。
- **原生 JavaScript Fetch API**：仅用于草稿自动保存、单题提交、跳题和倒计时。
- **不使用 Vue/React/Node.js 构建链**：首版减少前后端状态重复、Token 登录、跨域和部署复杂度。

Word 会话报告依赖 `python-docx==1.2.0`。执行 `python -m pip install -r requirements.txt` 会安装该依赖及其锁定版本。

## 教师端

教师端采用管理台布局，主要页面包括班级、学生、题库、KC、实验配置、发布和导出。高风险操作采用 POST 与确认提示；发布后页面转为只读。

| 流程 | 路由与方法 | 界面行为 |
|---|---|---|
| 班级详情 → 编辑班级 | GET /teacher/courses/{course_pk}/classes/{class_pk}/edit/ | 展示名称、学年和启停状态；停用影响必须以文字说明，历史名单仍可读。 |
| 保存班级设置 | POST /teacher/courses/{course_pk}/classes/{class_pk}/update/ | 使用 CSRF 与隐藏 state_token；并发冲突进入错误摘要，不静默覆盖。 |
| 题库详情 → 编辑题库 | GET /teacher/courses/{course_pk}/banks/{bank_pk}/edit/ | 仅显示名称、说明和可见性编辑，不暗示会改动题目、选项或 KC 映射。 |
| 保存题库元数据 | POST /teacher/courses/{course_pk}/banks/{bank_pk}/update/ | 使用 CSRF；归档题库页面保持题目内容和 KC 映射只读。 |
| 草稿实验 → 保存参与规则 | POST /teacher/courses/{course_pk}/experiments/{experiment_pk}/assign/ | 班级、显式纳入和从班级排除可在无 JavaScript 时直接提交；“当前预览”必须和发布后冻结名单区分。 |
| 结果页 → 作答详情 | GET /teacher/courses/{course_pk}/experiments/{experiment_pk}/sessions/{session_pk}/ | 展示该学生实际分配的题目顺序，并用文字区分草稿、未作答和正式提交。 |
| 下载 Word 报告 | POST /teacher/courses/{course_pk}/experiments/{experiment_pk}/sessions/{session_pk}/word/ | 使用 CSRF；只读下载具有审计记录。 |

参与规则界面按班级使用原生 `details/summary` 披露学生名单，并让“额外纳入”和“从本班排除”各自有明确标签。JavaScript 仅增强不合法组合的禁用状态，不能成为提交规则的前提。发布后隐藏规则表单并显示冻结名单。

## 学生端

学生端采用“左侧题号导航 + 右侧单题内容”的桌面布局，窄屏自动改为上下布局。题号明确区分未访问、草稿、跳过、提交和超时状态。

题号导航必须按 `StudentQuestionAssignment.assigned_sequence_index`，即该学生实际获得题目的顺序排列；课程永久编号只用于追溯，不能替代学生导航顺序。会话详情与 Word 也按此顺序展示。Word 仅面向单会话作答核对，禁止显示 KC、教师或经验难度、用时、访问/导航事件和解析；不同个体化题单下的学生裸分不能被界面表述为可直接比较。

答案变更后 800ms 自动保存到服务器。切换题目与标记跳过前会强制保存当前草稿。正式提交使用一次性 UUID，服务器事务完成后锁定题目。

## 响应式与可访问性约束

- 宽屏保留左侧题号导航；窄屏将导航折叠为可展开控件，当前题、提交数量和结束入口仍可达。
- 当前面包屑使用 `aria-current="page"`，当前题号使用 `aria-current="step"`；保存状态和参与者预览使用适当的 `aria-live` 区域。
- 所有表单控件必须有可见标签、关联错误和页首错误摘要。表格仅在自身容器横向滚动，页面根节点不得因宽表溢出。
- 可点击选项保持整行点击目标和可见焦点；关键教师与学生流程应在 200% 缩放、390×844 窄屏与 1440×900 宽屏下保持可达。
