# KT 交互数据采集平台（本地首版）

Django 单体应用，用于多实验、单题级自由导航和题目交互数据采集。默认本地使用 SQLite；Docker 部署使用 PostgreSQL + Nginx。

## 界面技术

- Django Templates：服务端页面与权限控制
- Bootstrap 5.3：响应式教师工作台和学生答题页
- 原生 JavaScript Fetch API：800ms 草稿自动保存、提交幂等、倒计时
- 无 Node.js、npm 或前端打包步骤

## 一键本地运行

### Windows

双击或在命令行运行：

```bat
run_local.bat
```

### Linux / macOS

```bash
chmod +x run_local.sh
./run_local.sh
```

打开 `http://127.0.0.1:8000/`。

演示账号：

| 身份 | 账号 | 密码 |
|---|---|---|
| 管理员 | admin | admin123 |
| 教师 | teacher | teacher123 |
| 学生 | S001 / S002 / S003 | student123 |

首次运行会安装依赖、生成数据库迁移、创建 SQLite 数据库并写入演示题库和实验。首次安装依赖需要能够访问 Python 包索引。

## 手工启动

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python manage.py makemigrations accounts question_bank experiments assessment
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

## 主要功能

- 管理员：Django Admin 管理教师、学生、班级和公共 KC
- 教师：题库创建、题目 CSV/XLSX 导入、KC 对应表导入、题目编辑、实验配置、学生分配、发布快照、结果导出
- 学生：学号登录、单题显示、自由导航、草稿自动保存、每题提交锁定、跳题和断点续答
- 数据：题目访问事件、首次/最后访问、访问次数、分配/首次访问/提交顺序、最终答案、判分与三类 CSV 导出

## 导入模板

题目表：`序号 | 题目 | 选项A | ... | 选项F | 答案`

KC 对应表：`题目序号 | 主知识点 | 次知识点`，多个次知识点用分号分隔。

## Docker 本地/服务器启动

```bash
cp .env.example .env
# 修改 .env 中的密钥和数据库密码
docker compose up --build -d
```

访问服务器 IP。正式公网部署时应在 Nginx 前配置域名与 HTTPS，并关闭演示弱密码。

## 当前首版边界

- 题目发布后使用不可变快照；原题修改不影响历史实验。
- `last_visit_to_submission_ms` 是“最后一次进入该题至提交”的时长，不是完整认知作答时长。
- 未提交、跳过和超时默认 `is_correct` 为空。
- 分层随机首版对全部题目按主 KC 与难度区间分组后交错排序，不做自适应推荐或动态抽题。
- Bootstrap 通过带完整性校验的 CDN 加载；首次版后端与数据功能不依赖 Node.js。离线网络环境下页面仍可提交，但样式会退化。
