# R3 visual specification

Source: [Sites v1](https://kt-english-exercise-demo-1031.mythicflea258.chatgpt.site). Immutable source and user annotations are under reference/. Read manifest.json before changing this specification. Latest explicit user instructions override the archived prototype.

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


## Acceptance evidence

Capture at 1366x768, 1440x900, 1920x1080, 2560x1229, 390x844, plus 200% zoom. Evidence is recorded in route-checklist.md and artifacts/english-groups-ui-20261031. An unchecked row is not a passing result.
