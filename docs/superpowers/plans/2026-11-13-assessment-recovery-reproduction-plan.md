# 答题保存与切题恢复修复计划

> 2026-11-13：已按用户指示在当前 worktree 实施修复。下文保留修复前的失败证据；最终验收见第 5 节。没有提交、合并或部署。

**目标：** 旧客户端答案恢复后不再先触发一次可避免的缺失记录错误；切题发送前失败后仍能继续操作，同时保留结果未知请求的原令牌、材料与作答轨迹。

**架构：** 保持 Django 的状态、权限、截止时间和回执权威。修复集中在前端会话控制器与答题页的恢复顺序，不改变数据库结构、采集协议或业务 URL。

**环境：** Vue 3 / TypeScript / Vitest；后续集成验证使用 Django 5.2 / PostgreSQL 17 / Playwright。

## 1. 隔离副本及修复前复现结果

- 路径：`E:/ATOP/kt-data-platform-complete/.worktree/assessment-recovery-repro`。
- 基线：`3922a99e0b1b4bfa63427edcdbc7cbc1fe1e5fea`，detached HEAD。
- 主工作区没有已跟踪文件改动。仅在 Git 本机 `info/exclude` 中加入 `/.worktree/`，避免副本进入主工作区的未跟踪文件清单。
- 在副本的 `client/` 执行 `npm ci --no-audit --no-fund`，使用独立的 node_modules；未更改依赖或锁文件。
- Node：`C:/Users/31606/AppData/Local/KT——platform/runtime/node-v24.20.0-win-x64/node.exe`，实测 v24.20.0。
- 初始复现阶段仅运行前端单元与组件测试，不使用生产数据，不连接学校服务器，不启动 Django/PostgreSQL。

| 检查 | 结果 | 证据（相对副本根目录） |
|---|---|---|
| 原有三个相关测试文件 | 193/193 通过 | `artifacts/assessment-recovery-repro/baseline.json` |
| 加入复现及对照后，四个测试文件 | 194 通过，2 个预期失败，共 196 项 | `artifacts/assessment-recovery-repro/reproduction.json` |
| TypeScript 类型检查 | 通过 | `artifacts/assessment-recovery-repro/typecheck.log` |
| 两个修改/新增测试文件的 ESLint | 通过 | `artifacts/assessment-recovery-repro/lint.log` |
| 已跟踪文件 diff 空白检查 | 通过 | `git diff --check` |

原有组件测试会输出两条 jsdom 的 `Not implemented: navigation to another Document` 提示；基线已有，测试结果通过。这不是浏览器实机导航验证。

## 2. 已复现的两个问题

### R1：写入凭证拒绝后，恢复的旧答案仍先触发缺失记录拒绝

测试：`client/src/assessment-runtime/recovery-reproduction.test.ts`，名称以 `REPRO: saves a restored old-client answer` 开头。

步骤：旧客户端生成并本地保存答案 → 冻结携带旧凭证的保存请求 → 新客户端恢复同一答案 → 重放旧请求并收到可核验的 `writer_lease_conflict` 拒绝 → 用新凭证重新保存。

期望：先确认旧答案事件已上传，或在合法请求中提交它，再完成保存。

修复前实际：新请求只打包当前客户端的事件，旧答案事件仍在旧客户端队列，触发 `response_target_not_persisted`。断言在真实 `SessionController.captureMutation` 调用处失败。

对照：再重试一次，会进入现有缺失记录恢复分支，补传旧事件并保存成功，恢复的答案仍保留。对照测试通过，说明不是永久丢失答案。

涉及实现：`client/src/assessment-runtime/session-controller.ts` 的 `captureMutation`，以及 `process-sender.ts` 的 `freezeCaptureMutation`。现有主动同步仅在已经收到缺失记录拒绝后触发；旧保存成功重放的路径已有同步处理，但拒绝后重建请求的路径没有等价保障。

### R2：切题尚未发送，准备失败却留下进入意图，拦住另一目标

测试：`client/src/assessment-runtime/StudentAssessment.test.ts`，名称以 `REPRO: allows another destination` 开头。

步骤：已加载第一题 → 点击第二题，真实控制器冻结进入意图 → 等待旧草稿结果时收到拒绝 → 确认尚未创建离题事件、尚未发送 saveAndLeave → 再点击第三题。

期望：安全结束未发送的第二题进入准备，允许第三题发起新切题；第一题答案保持不变。

修复前实际：页面清掉了内存中的点击记录，却未取消真实控制器持久化的 entry-intent。第三题被 `freezeEntry` 拦截，页面出现截图中的“上一次进入结果尚未确认，请先核验原题目。”。

涉及实现：`StudentAssessment.vue` 的 `saveAndLeave` 失败分支，以及 `session-controller.ts` 的 `freezeEntry` / `cancelEntry`。

**证据边界：** R1 使用真实轨迹生成、内存 outbox、发送器、控制器及回执核验，服务器传输层为按目标事件存在性决定接受/拒绝的替身；R2 使用真实组件和进入控制器，在草稿等待边界注入拒绝。二者证明了可发生的失败路径，并与线上现象一致；初始复现未覆盖 IndexedDB、真实网络和 PostgreSQL；后续合成场景验收见第 5 节。仍不能据此断言学生当时的完整浏览器操作序列。

## 3. 实施顺序与完成记录

### 第一步：旧客户端答案同步

- [x] 在 `session-controller.ts` 重建保存请求前识别目标答案仍属于旧客户端待上传队列的情况，复用现有有界确认上传；不要给所有普通保存增加全量同步。
- [x] 同步未确认时保留原始事件及操作材料，不发送依赖未确认目标的保存请求。
- [x] 已发送且结果未知的旧请求继续按原令牌和原材料核验；不能为绕过冲突直接更换令牌。
- [x] 让 R1 转绿。将当前“再次点击才能恢复”的对照测试调整为修复后的安全性断言，持续验证旧事件保留、依赖先确认、相同请求不重复生效，而不永久要求出现缺失记录错误。
- [x] 运行 `session-controller.test.ts`、`save-recovery.test.ts` 和新增复现文件；保留已有回包丢失、错误回执不清理材料等断言。

### 第二步：未发送切题的状态清理

- [x] 在 `StudentAssessment.vue` 确认当前切题尚未发送、未超时、没有结果未知的持久化导航时，调用既有 `cancelEntry(sessionId, navigationId)` 结束对应进入意图，并同步清理内存点击记录。
- [x] 只处理匹配的本次导航；不清理草稿、答案事件、旧保存请求或其他目标的进入意图。
- [x] 超时、已发送请求丢失回包、已确认离题等情况，继续保留原令牌并使用现有核验入口。
- [x] 让 R2 转绿，并覆盖原目标重试和另一目标重试。运行 `StudentAssessment.test.ts` 中已有未知切题结果及 60 秒等待超时测试。
- [x] 如修改恢复提示，按仓库 UI 技能检查，将答案保存状态与切题状态分别说明；仅在复现证据要求时修改提示，不扩大为页面重设计。

### 第三步：本地集成验收

- [x] 为本副本建立自己的 `.venv`，使用 Python 3.13；不得借用主工作区的虚拟环境。
- [x] 使用便携 PostgreSQL 17 的本地隔离 E2E 数据库；检查没有其他测试占用后再准备演示数据，不操作生产数据库。遵循仓库既有 `kt_e2e` 环境设置。
- [x] 在 PostgreSQL 上执行 `server/apps/assessment/test_capture_bundle.py` 的相关目标存在性、拒绝回滚和回执重放测试，验证传输替身采用的假设与后端契约一致。
- [x] 在 `client/e2e/capture-v2.spec.ts` 增加对应浏览器流程，通过实际 IndexedDB 和后端验证：离线答题后重连、新文档恢复旧记录、保存已成功但响应丢失、切题准备拒绝后切换目标。
- [x] 用已有 Playwright 配置运行这些目标场景；验收答案一致、旧记录保留、原请求重放不重复生效、页面能够继续操作。若涉及提示布局，补桌面、手机和 200% 缩放检查。
- [x] 运行 typecheck、相关文件 lint、前端 build 和上述定向回归；仅在具体失败显示更广影响时扩展测试范围。

原先的小时估计已撤回；以已复现问题、最小修复及实际验收结果安排工作。WebKit 验收进一步揭示跨文档父记录被旧请求占用时的后台上传竞争，因此在同一修复范围内增加 fact-store 的父记录顺序约束。

## 4. 复现命令与停止条件

在 PowerShell 中：

```powershell
Set-Location 'E:\ATOP\kt-data-platform-complete\.worktree\assessment-recovery-repro\client'
$env:PATH='C:\Users\31606\AppData\Local\KT——platform\runtime\node-v24.20.0-win-x64;C:\Users\31606\AppData\Local\KT——platform\runtime\PostgreSQL-17.11-portable\pgsql\bin;'+$env:PATH
$env:PLAYWRIGHT_BROWSERS_PATH='C:\Users\31606\AppData\Local\KT——platform\runtime\pw-fresh-browsers'
npm run test:unit -- src/assessment-runtime/session-controller.test.ts src/assessment-runtime/save-recovery.test.ts src/assessment-runtime/StudentAssessment.test.ts src/assessment-runtime/recovery-reproduction.test.ts
npm run typecheck
node node_modules/eslint/bin/eslint.js src/assessment-runtime/recovery-reproduction.test.ts src/assessment-runtime/StudentAssessment.test.ts
```

修复前预期：测试命令退出码 1，恰好两个 REPRO 测试失败；原有 193 项和新增对照项通过。修复后预期为全部通过。不要通过删除断言、标记跳过或直接清空持久化材料使测试通过。

验收条件：失败用例转绿、相关回归通过、实际浏览器及 PostgreSQL 验收完成。提交、合并和学校部署另行按用户指示执行。


## 5. 实施结果与验收证据

业务代码仅修改三个文件：

- `client/src/assessment-runtime/session-controller.ts`：恢复的旧答案尚未获服务器确认时，先确认旧文档事件上传，再创建依赖它的保存请求。上传失败保留原材料并阻止新请求；普通保存不增加此同步。
- `client/src/assessment-runtime/fact-store.ts`：内存和 IndexedDB 队列均等待其他文档的本地父记录确认后，再上传依赖它的后续答案，避免后台上传先冻结一个缺父记录的批次。只调整可发送顺序，不删除或改写事件。
- `client/src/assessment-runtime/StudentAssessment.vue`：仅在导航准备尚未开始、没有超时和持久化导航结果待核验时，取消匹配的进入意图。结果未知的导航继续原请求核验。

测试覆盖位于 `recovery-reproduction.test.ts`、`StudentAssessment.test.ts`、`client/e2e/capture-v2.spec.ts`。跨文档场景验证两条答案事件的父子关系和不同文档 ID；切题场景验证最终答案及恰好一条导航回执。

独立运行环境：

- Python 3.13.5：本副本 `.venv/Scripts/python.exe`，依赖按仓库 requirements 安装。
- Node：`C:/Users/31606/AppData/Local/KT——platform/runtime/node-v24.20.0-win-x64/node.exe`。
- PostgreSQL 17.11：`C:/Users/31606/AppData/Local/KT——platform/runtime/PostgreSQL-17.11-portable/pgsql/bin`；数据目录 `C:/Users/31606/AppData/Local/KT——platform/runtime/PostgreSQL-17.11-data`；监听 `127.0.0.1:55432`。
- E2E 使用 `kt_e2e` 合成演示数据，Django 单元/集成测试使用 `test_kt_e2e`。没有操作生产数据。
- 浏览器：`C:/Users/31606/AppData/Local/KT——platform/runtime/pw-fresh-browsers`。

以下日志均在 `artifacts/assessment-recovery-repro/`：

| 验收 | 结果 | 证据 |
|---|---|---|
| 前端全部单元/组件测试 | 1164/1164 通过 | `frontend-unit-final.json` |
| PostgreSQL capture bundle 契约测试 | 47/47 通过，含拒绝回滚和回执重放 | `postgres-contract-tests.log` |
| 类型检查 / 修改文件 ESLint / 构建 | 全部通过 | `typecheck-final.log`、`lint-final.log`、`build-final.log` |
| 全前端 ESLint | 0 错误，14 条既有非本次修改文件警告 | `frontend-lint.log` |
| API 契约 / Django check / collectstatic | 通过 | `api-check.log`、`backend-check.log`、`collectstatic.log` |

浏览器最终结果：Chromium 4/4、WebKit 4/4 全部通过（`e2e-chromium-final.log`、`e2e-webkit-final.log`）。四个场景为：130 条离线答案积压后重连及丢失回包重放；历史题目链接恢复原导航；新文档补传旧答案及其后续答案；草稿重放被拒绝后改选另一题。历史失败日志保留，不能将修复前或测试注入不准确的失败报告当作最终结果。

历史恢复测试在两种浏览器均生成桌面 1440×900、手机 390×844、页面 200% 缩放截图，并断言桌面/手机无横向页面溢出；已人工查看 WebKit 对应三张截图，答案、导航和保存状态可辨认。最终截图在 `client/.e2e-results/recovery-webkit-final/` 与 `client/.e2e-results/recovery-chromium-final/`。

验收完成，`git diff --check` 通过。主工作区无已跟踪文件改动；其既有未跟踪资料未清理。修复保留在本隔离副本。

范围边界：未修改后端权威规则、数据库结构、依赖或锁文件；未提交、合并、推送或部署。此处的 200% 检查为 Playwright 页面 CSS zoom，不等同于原生 Windows 显示缩放或手机真机验收。
