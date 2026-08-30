# Skill trigger evaluations

## Should trigger

- “按照六张参考图 1:1 复刻实验页面的 UI 风格，但保留真实班级和数据。”
- “优化 Vue 教师工作台和题库列表界面。”
- “改善学生单题答题页的移动端和无障碍体验。”
- “统一实验管理页的表单、状态和视觉层次。”
- “审计当前 Django 或 Vue 页面是否存在通用 AI UI 和文字溢出。”

## Should not trigger or should refuse scope expansion

- “为了对齐截图，把 seed_demo 改成 3 个班 120 人。”
- “让前端自行判断班级能否发布和随机化是否通过。”
- “修复数值题判分错误。”
- “修改 PostgreSQL 数据库索引。”
- “增加新的 KT 模型。”
- “重写随机化、评分或数据导出算法。”

## Expected behavior

- Select the correct persisted reference by page state.
- Treat reference imagery as visual authority only, never as a data specification.
- Preserve Django authority and real API projections.
- Scope Vue feature styles and test structure/behavior before pixel calibration.
