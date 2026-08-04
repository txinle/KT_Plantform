# Verification status

最后更新：2026-08-04。

## 已验证

- Django 系统检查通过：`python manage.py check`。
- Django 测试套件通过：28 项测试，覆盖评分、导入、实验发布、权限、登录呈现、教师/学生身份信息、层级目录和关键 UI 契约。
- 前端脚本通过语法检查：`node --check static/js/app.js` 与 `node --check static/js/assessment.js`。
- Django 模板加载检查通过：26 个模板均可由模板引擎解析。
- 教师端与学生端主要路由已使用 Django 测试客户端完成登录态冒烟检查。
- 本地 Bootstrap CSS 与 JavaScript 资源通过 HTTP 200 响应验证，页面运行不再依赖前端 CDN。
- UI 源码检查未发现原生 `alert()` / `confirm()`、内联样式、占位链接或生产模板中的 CDN 引用。

## 可视验证边界

- 优化前的桌面端与移动端截图已保存在 `docs/ui/screenshots/before/`。
- 优化后的自动截图未完成：应用内浏览器在开发服务器重启后阻止了 localhost 重新导航。该限制不影响 Django、JavaScript、静态资源或 HTTP 冒烟验证，但上线前仍应在真实浏览器中补做桌面端与移动端视觉回归。

## 正式采集前仍需完成

- 使用目标浏览器和目标屏幕尺寸执行人工视觉回归与键盘可达性检查。
- 替换演示弱密码、关闭调试模式并启用 HTTPS。
- 使用独立测试数据完成一次导入、发布、作答、超时和三类导出的端到端验收。
