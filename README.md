# CareerRadar

CareerRadar 是一个本地单用户的简历驱动求职 Agent。它把简历拆成稳定证据块，由 PydanticAI Agent 推荐岗位方向；用户确认方向和城市后，自研抓取器读取 BOSS 直聘公开页面，再以固定权重排名并校验所有引用。LangGraph 负责编排 Agent 调用、审批状态和故障恢复。

## 产品流程

1. 上传 PDF、DOCX 或粘贴文本。
2. 查看候选人画像和 3 个岗位方向，修改并确认最多 2 个方向与 2 个城市。
3. Chrome 助手自动接单，在专用标签页搜索、翻页和读取详情；每个岗位方向与城市组合最多两页，最多保存 20 个唯一岗位。
4. 查看岗位排名、硬性风险、简历证据和岗位证据。
5. 获取排名前三岗位对应的 7 天准备计划。
6. 从排名中选择目标公司和具体岗位，回答或跳过事实追问，生成可编辑的定向简历并下载技术版、商务版的 DOCX/PDF。

页面右侧的求职 Agent 在整个流程中保持可用。提交简历前可进行通用职业咨询；提交后会自动关联当前画像、抓取任务和报告，可以追问排名原因、能力缺口、面试题和准备计划。修改城市、岗位、薪资或重新搜索时，Agent 只生成确认卡，点击确认后才修改数据或创建任务。

“06 定向简历”支持三种目标来源：本次排名中的岗位、用户粘贴的公司/岗位/JD，以及 BOSS 公开职位链接。Agent 最多追问 5 个能显著改善简历的事实；只有用户确认的回答才会成为长期补充证据。网页编辑每次创建新版本，原画像、旧草稿、旧岗位和旧报告均保留。联系方式单独存储，只在本地导出时合并，不进入 Agent 工具或模型请求。

扫描版 PDF 暂不支持 OCR。遇到登录页、验证码、白屏或未知页面结构时，任务进入 `NEEDS_MANUAL_INPUT`。用户在专用标签页完成登录或验证后，点击扩展中的“继续任务”，从原队列位置恢复。助手不调用 Cookie API；只提取任务标签页的页面内容，上传前移除输入框、内嵌框架和非结构化脚本。采集完成后后端自动启动分析，关闭扩展弹窗不影响采集。

## 快速启动

```bash
cp .env.example .env          # 在 .env 中填写 TOKEN_PLAN_API_KEY
PYTHONPATH=.deps:. python3 -m uvicorn career_radar.web:app --host 127.0.0.1 --port 8000
```

界面在 **http://127.0.0.1:8000/app/**（旧的 `/` 页面仍然保留）。也可以运行 `docker compose up --build`。

**`/app` 需要先构建过前端才存在。** 构建产物 `career_radar/static/app/` 与依赖一样被 Git 忽略，所以全新克隆后要先构建一次：

```bash
cd frontend
npm ci
npm run build          # 产物输出到 career_radar/static/app/
```

开发前端时改用 `npm run dev`，它的 `/api` 反向代理指向 `127.0.0.1:8000`，不需要配置 CORS。

**换到全新机器、或没有 `.deps/` 时**，用标准 Python 环境：

```bash
sudo apt install python3-venv      # 缺 ensurepip 时 `python3 -m venv` 会建出一个没有 pip 的空壳
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/playwright install chromium   # 仅 HTTP 模式需要；浏览器助手模式不需要
.venv/bin/uvicorn career_radar.web:app --host 127.0.0.1 --port 8000
```

在 Chrome 120+ 打开 `chrome://extensions`，开启“开发者模式”，选择“加载已解压的扩展程序”并选中本仓库的 `browser-extension/` 目录（也可下载助手 ZIP 后解压，选择包含 manifest.json 的目录）。已安装旧版的用户需要刷新扩展并确认新增的本机端口与 BOSS 站点权限。保持 CareerRadar 页面为当前标签页，打开扩展后依次点击“使用当前 CareerRadar 页面地址”和“启用自动接单”；这样即使主程序没有运行在 8000 端口，助手也会回传到正确实例。

助手每 30 秒检查一次待处理任务；采集过程中由页面加载完成事件直接推进，30 秒检查仅作为 Chrome 休眠后的恢复兜底。BOSS 页面导航间隔至少 10 秒，智联与前程无忧至少 12 秒，并始终使用单个专用标签页串行采集。弹窗会显示已采集数量、运行时长和最近一分钟速度；切换主程序地址时自动清除旧实例的任务游标，主程序暂时断开时自动重试，找不到的旧任务会被丢弃。队列、阶段、下次导航时间和开关存放在扩展本地存储，可在后台工作线程重启后恢复；关闭浏览器期间不执行。取消任务后后端拒绝继续接收岗位。

### 采集性能与验证状态

- 旧版真实 BOSS 任务采集 20 条通常约需 23 分钟，主要原因是导航和读取分别等待一次 30 秒轮询。
- 当前版本在页面加载完成后立即读取，但仍严格执行 10/12 秒的导航间隔。无登录或验证阻断时，20 条岗位的验收目标是 **5–8 分钟**；这是按单标签页串行请求计算的目标，必须以刷新扩展后的真实任务数据为准，不能仅凭 fixture 测试宣称达成。
- 前程无忧详情规则匹配 URL 的 pathname，并支持 `/guangzhou/173657951.html` 和 `/guangzhou-thq/173657296.html` 这类城市、区县路径。遇到滑块验证时仍进入 `NEEDS_MANUAL_INPUT`，不会绕过验证或把列表页文本保存为岗位职责。
- 本次修改的自动验证结果为：后端 `137 passed, 2 skipped`，扩展 Node 测试通过，前端类型检查、20 项测试及生产构建通过。真实站点的成功数、总耗时、P50/P95、重复数和暂停原因需要按站点另行记录。

现有本机依赖启动命令（默认自动采集模式不需要 Playwright 浏览器）：

```bash
cd /home/com01/Documents/ChatGPT/github/career-radar
PYTHONPATH=.deps:. python3 -m uvicorn career_radar.web:app --host 127.0.0.1 --port 8000
```

默认模型配置：

```dotenv
MODEL_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
MODEL_NAME=deepseek-v4-pro-0813
TOKEN_PLAN_API_KEY=
```

应用只读取本仓库自己的 `.env`，不依赖任何同级目录。`.env`、数据库、Cookie 和模型请求转储均被 Git 忽略。

## 代码边界

- `career_radar/resume.py`：格式校验、文本提取、稳定证据块。
- `career_radar/sites/`：招聘站点适配器。`base.py` 定义 `SiteAdapter` 协议与统一的快照构造，`boss.py`／`zhaopin.py`／`job51.py` 各自负责一个站点，`transport.py` 是站点无关的 HTTP + 浏览器降级与调度，`registry.py` 是注册表。新增站点只需写一个适配器文件并登记。
- `browser-extension/`：持久化任务队列、专用标签页导航、暂停恢复和自动回传。
- `career_radar/scoring.py`：40/25/15/5/15 固定权重评分。
- `career_radar/agent.py`：定义岗位推荐、对话、比较和定向简历的 PydanticAI 类型化 Agent，并提供确定性降级结果。
- `career_radar/agent_runtime.py`：用 LangGraph 编排 PydanticAI 调用、最多三次工具调用、审批等待状态和故障重试。
- `career_radar/chat.py`：多轮上下文、引用解析、待确认操作和独立聊天任务队列。
- `career_radar/tailoring.py`：目标 JD 对齐、事实追问、证据校验、不可变版本及 DOCX/PDF 双模板导出。
- `career_radar/tools.py`：进程内 PydanticAI 工具与安全边界（联系方式脱敏、会话绑定）；对话模式只开放读取与提议工具，不允许模型直接修改业务状态。
- `career_radar/apply.py`：半自动辅助投递。生成打招呼语（同样受证据纪律约束，且**不得包含联系方式**——模型拿不到它们，出现即视为编造），并关联该岗位已导出的定向简历。**本仓库从不代替用户点击发送**，提交动作始终由用户完成。
- `career_radar/memory.py`：跨会话的偏好、约束与反馈。事实仍然只存在于 `profile.evidence`（带 provenance、经确认），记忆只是软信号，不构成"候选人会什么"的陈述。
- `career_radar/interview.py`：按岗位的面试准备。逐日计划与面试题，每道题都必须引用证据块，后端补齐逐字引文；无法归因的问题会让任务进入 `FAILED_VALIDATION`，而不是当作事实呈现。
- `career_radar/database.py`：SQLite 画像、任务、快照、变化、报告、会话、消息和操作存储。
- `frontend/`：React + Vite 单页应用。构建产物由 FastAPI 挂在 `/app`；旧的 `career_radar/templates/` 服务端页面仍在 `/` 提供服务。

PydanticAI 是单 Agent 内核，LangGraph 是工作流编排层。抓取队列、网络传输、解析、去重、快照、评分、引用校验和确认动作全部由本仓库实现，没有使用 Scrapy、Crawl4AI 或其他爬虫框架。Playwright 只负责公开页面的浏览器降级；登录流程使用用户自己的普通 Chrome。Agent 工具在进程内直接调用，不再经由 stdio MCP 子进程；本仓库可以接入外部 MCP 服务器，但自身不作为 MCP 服务器发布。

## 测试

```bash
# 后端与扩展测试必须在仓库根目录执行（tests/ui_chat.test.cjs 用的是相对路径）
PYTHONPATH=.deps:. python3 -m pytest -q
node --test tests/*.test.cjs
```

前端（`cd frontend` 后执行）：

```bash
npm run dev                  # Vite dev server，/api 反向代理到 127.0.0.1:8000
npm run check                # typecheck + vitest
npm run build                # 输出到 career_radar/static/app/，随后访问 /app/
```

`.github/workflows/ci.yml` 有两个 job：`backend` 跑 pytest 与扩展的 node 测试，`frontend` 跑 `npm ci` + `npm run check` + `npm run build`。**两者都必须通过**——前端类型错误或构建失败不会体现在后端 job 里。

`/app` 在未构建时不存在（挂载条件为 `static/app/index.html` 存在），此时 `/` 的旧页面仍是唯一界面。

对话接口包括 `POST /api/conversations`、`POST /api/conversations/{id}/messages`、`GET /api/chat-turns/{task_id}` 和待确认操作的确认/取消接口。每条个性化回复只接受当前会话画像和抓取任务中的稳定证据 ID，后端补齐逐字引文；失效引用不会作为已证实回复展示。

**面试准备为什么让模型写散文而不是 JSON（实测）**：当前配置的端点（`deepseek-v4-pro-0813`）无法可靠填充结构化字段。同一份提示词下实测：

| 要求的形状 | 结果 |
|---|---|
| 每题 5 个字段的对象数组 | `category` 空、`evidence_ids` 空、`days` 空，输出里出现 `{{placeholder}}` 残片 |
| 压平到 3 个字段 | 数量对了（6 条），但 `evidence_ids` 全空，问题文本混进推理过程 |
| 只保留 2 个字段 | 只剩 1 条，仍无 `evidence_ids` |
| **`类别\|问题` 的纯文本行** | **6 条全对**，类别正确、紧扣证据、无推理泄露 |

所以现在是**模型写散文、后端负责结构化与绑定证据**。这个分工本身也更合理：引用是否成立本就该由后端判定，不该依赖模型给出。因此 `InterviewQuestionOutput` 不复存在，模型也不再被要求输出 block id。

后端绑定证据时会同时校验技能词**和**问题里出现的数字——只匹配技能会让"结合你 2 年 Python 经验"被绑到只含 `python` 的块上，随后因"未经证实的数字：2"被拒。绑定不到任何证据的问题会被丢弃。

模型路径仍可能返回不可用内容。系统的处理是**降级并说明**，不是失败也不是静默替换：

- 模型返回**空**内容 → 用确定性版本，`note` 记录原因
- 模型返回**无法证实**的内容 → 同样替换，`note` 写明被拒的原因
- **未经证实的内容永远不会展示给用户**——这是不变量，比"任务是否失败"更重要

用户始终能在界面上看到"生成方式：本地确定性降级"以及具体原因。

面试准备接口从 `POST /api/interview-preps` 开始，异步生成后由 `GET /api/interview-preps/{id}` 读取，也可用 `GET /api/jobs/{snapshot_id}/interview-prep?profile_id=…` 取某个岗位最近一次的准备。与 `Comparison.action_plan` 不同，它是**按岗位**而非针对前三名的统一计划。校验规则有一条刻意的例外：针对岗位缺失技能提出的问题，其证据来自岗位原文而非候选人简历，否则任何关于能力差距的提问都会因为"简历里没有"而被判为无据。

定向简历接口从 `POST /api/target-jobs` 和 `POST /api/resume-tailorings` 开始，追问确认后由异步任务生成草稿。`POST /api/resume-drafts/{id}/versions` 保存网页编辑的新版本，`POST /api/resume-drafts/{id}/exports` 生成两套 DOCX/PDF，下载文件仅从本地 `data/exports` 返回。模型生成的每条内容都必须引用当前候选人的证据；未经证实的技能、数字或机构会使任务进入 `FAILED_VALIDATION`。

真实任务可通过 `PYTHONPATH=.deps:. python3 scripts/audit_live_run.py task_…` 只读检查：采集数量、去重结果、字段存在情况、快照证据一致性，以及报告实际使用 LangGraph 还是备用逻辑。字段存在不等于语义准确，仍需抽查原始岗位。

`scripts/repair_job_fields.py` 用于修复早期解析器把推荐列表混入职责的历史快照，仅处理指定任务。运行前自动备份 SQLite；保留原抓取时间与快照 ID，不将历史文本重解析冒充重新抓取。应在生成分析报告前运行。

CI 只读取 `tests/fixtures`，不会访问真实站点。真实站点冒烟测试必须显式设置 `CAREER_RADAR_LIVE_TEST=1`。评测数据目录预留给 10 份脱敏简历和至少 50 个岗位页面；简历中只能写实际跑出的接受率、成功率、字段完整率、引用覆盖率、P50/P95 耗时和 Token 成本。

## 当前限制

支持 BOSS 直聘、智联招聘、前程无忧，城市为北京、上海、深圳、广州、杭州、成都、武汉、南京、西安、苏州。不包含自动投递、自动联系招聘者、多用户权限、OCR、代理池或验证码处理。浏览器助手需要 Chrome 的开发者模式加载，站点页面结构变化时可能需要手动粘贴岗位描述。fixture 测试通过并不保证真实站点始终可访问。

各站点的适配情况并不相同，以下是**实测结论**而非推测：

- **BOSS 直聘**：搜索页与详情页均可解析。
- **智联招聘**：可完整抓取。搜索页对普通 HTTP 请求只返回 SEO 外壳，需要浏览器降级才能拿到职位 ID；详情页可解析。公司名取 `.company-info__name`（`.company-info` 会把公司简介整段吞进去），职责区里的"岗位职责"等裸标签会被剔除。
- **前程无忧**：搜索页**间歇性**可渲染——同一 URL 有时返回 20 条带元数据的卡片，有时直接返回验证页；详情页则稳定地要求滑块验证。因此它经常走 `NEEDS_MANUAL_INPUT` 的人工粘贴路径，这是预期行为而非故障。
- **猎聘**：未接入。它不向无头浏览器渲染页面，选择器无从核实，因此没有登记，而不是用一个猜测的解析器占位。

两处踩过的坑值得记下：**无头浏览器必须伪装成普通 Chrome**（默认 UA 会被智联回以 16KB 的"正在验证连接安全性"页面，而真实 UA 拿到完整的 1.7MB 结果页）；**渲染等待时间必须按站点配置**（前程无忧在 1.2 秒和 2.5 秒时结果列表里一个元数据属性都没有，约 4 秒才出现全部 20 条）。

智联与前程无忧的抓取间隔下限为 12 秒（比 BOSS 更保守），实际节奏取所选站点中最慢的一个。
