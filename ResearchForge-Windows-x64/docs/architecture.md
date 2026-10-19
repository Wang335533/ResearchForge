# ResearchForge 架构说明

本文记录模块职责、进程与请求路径、数据存储、接口和前端分层，作为维护与界面设计的共同依据。面向使用者的说明见 README.md，开发入口见 docs/开发说明.md，界面规范见 docs/design-system.md。

## 1. 进程模型

```text
START-WINDOWS.cmd
└─ runtime/python  portable.py          便携启动：自检、实例锁、空闲端口、随包 Ollama
   ├─ ollama serve（子进程）             仅供摘要库向量检索，绑定 127.0.0.1 的随机端口
   └─ run.configure()                   读取 config.json，把路径与阶段配置写入环境变量
      └─ server.main()                  Flask 主服务（127.0.0.1:8331 起，占用时自动换端口）
         ├─ 研究构想（server.py）         /  /api/*  /ui/*  /setup  /architecture
         └─ DispatcherMiddleware
            └─ 论文精读（paper_reader）   /papers/*（精读、全文库、文献地图、提示词库）
```

- 只有一个 Python 进程对外服务。精读模块是独立的 Flask 应用，通过 `paper_reader_integration.install()` 以 WSGI 子应用挂载到 `/papers`，也可单独运行（此时没有研究构想入口）。
- 所有请求都只接受回环地址；修改类请求校验 `Origin`，精读模块另要求 `X-PaperLens: 1` 请求头。
- `run.py` 也能脱离便携包启动；`--workspace` 会把论文、语料与导入目录全部隔离到指定目录，界面验收和调试都应使用它，避免触碰真实数据。

## 2. 模块职责

| 路径 | 职责 |
|---|---|
| `portable.py` | 自检随包运行时、单实例锁、端口选择、Ollama 子进程生命周期、`/api/portable/health` |
| `run.py` | 配置校验、数据目录与环境变量、管理员随机密码初始化 |
| `platform/研究流水线Web/server.py` | 主服务：研究阶段接口、任务接口、提示词管理鉴权、报告导出、共享界面资源、工作台概览 |
| `research_tasks.py` | 研究任务的持久化调度：SQLite 任务库、协调线程、并发额度、停止与断点继续 |
| `prompts.py` / `prompts/*.json` | 构想、评审、提案三阶段的运行提示词（页面修改需管理员登录） |
| `output_format.py` / `citations.py` | 模型 JSON 的严格解析、字段补全、引用与检索记录的对应 |
| `report_exports.py` / `original_report_exports.py` / `report_html.py` | 完整研究报告的本地 HTML 排版与导出（不调用模型） |
| `llm_config.py` / `cloud_transport.py` | 阶段模型配置、Chat Completions 调用、密钥脱敏 |
| `paper_reader_integration.py` | 挂载精读模块，并让研究构想复用精读的共享 API 配置 |
| `platform/论文检索/retrieval_service.py` | 摘要库导入、向量准备（Ollama 嵌入）、BM25 + 向量混合检索、中英文配额 |
| `platform/paper_reader/app.py` | 精读入口：上传与提取、精读任务、分段证据与分节续写报告、导出、页面模板。报告与卡片文本统一由服务端渲染：Python-Markdown（表格、`pymdownx.arithmatex` 公式）→ bleach 清理；渲染前给紧贴段落的表格补空行，每个表格包进可横向滚动的框（按列数给宽表最小宽度），公式在浏览器用 KaTeX 排版 |
| `paper_reader/report_workflow.py` | 报告目录解析、分节续写的完成判定、阅读版（隐藏页码/行号定位）与完整性检查 |
| `paper_reader/reading_queue.py` / `reading_catalog.py` | 待读清单（保存原文件副本、去重、状态与备注）；已读目录（按论文归并多次精读，只读摘要字段） |
| `paper_reader/reading_knowledge.py` / `scholar_stats.py` | 变量与术语：从报告的变量测量卡片与三层用词表积累记录（人工修订不被覆盖）；页面先按论文列出文件夹（英文文献 / 中文文献分架），点进文件夹才看这篇的变量、概念与理论；英文文献显示原文英文名、说明用中文；早期规则生成的记录可“按新规范重整”（每篇一次模型调用，只读已保存报告，旧记录标为已替代、人工卡片不动）；学者统计（按独立论文计数） |
| `paper_reader/reading_sync.py` / `tencent_docs.py` | 组会填报：按表头逐列整理已完成报告、核对依据后追加到腾讯文档在线表格（官方 OAuth PKCE，令牌经 DPAPI 加密） |
| `paper_reader/library.py` | 全文库检索：配置文献网站时在线检索书目再定位本机原文，否则检索本机索引；只读原文件 |
| `paper_reader/literature_api.py` | 文献网站接口客户端（HTTPS、不跟随跳转、管理员/成员模式、10 分钟缓存），密钥只从配置的 manifest 读取 |
| `paper_reader/report_digest.py` | 从精读报告读出文献卡片（不调用模型）：基本信息与 DOI、保留 Markdown（表格、公式）的各节要点、第2节末尾的“领域定位”、按“作者（年份）《题名》”及 APA 条目识别的引用文献（标注文献对话、理论来源、测量来源、贡献对照等作用） |
| `paper_reader/projects.py` | 研究课题：最上层的分组。每份精读、文献卡片、待读论文（以及卡片下的变量与术语）属于一个课题；卡片身份按课题区分（`<课题>|doi:…`），同一篇论文在两个课题里是两张独立卡片；关联与引用匹配只在课题内计算。旧数据归入“默认课题”。课题可保存研究说明，新建精读时自动填入“我的研究”。只有空课题可以删除，有内容的可归档 |
| `paper_reader/jel.py` | 文献地图的领域词表：JEL 一级类目（中文名）。模型、精读报告与人工编辑写的领域名都归一到这张表；每篇论文一个主定位，最多再加一个不同一级类目的跨领域定位 |
| `paper_reader/trash.py` | 删除时的原文件处理：Windows 用系统 Shell 移入回收站（可还原），其他系统或失败时移到数据目录的 trash 文件夹 |
| `paper_reader/memory.py` / `memory_jobs.py` | 文献地图：永久卡片、分类、版本、关联。卡片先由报告整理，引用文献与已读卡片按 DOI、题名或唯一的第一作者＋年份匹配，自动生成“引用”和“共同引用”建议连线；后台定位只用一次模型调用，输入为报告要点（约 1.2 万字），只负责与已有分类统一、写简短要点、标签和关联，额度不足或失败时卡片仍可用 |
| `paper_reader/prompt_library.py` | 个人提示词收藏：集合、版本、回收站、导入与备份 |
| `paper_reader/research_bridge.py` | 研究构想的参考文献 → 本地全文的匹配与载入精读 |
| `chatgpt_oauth.py` / `chatgpt_models.py` | ChatGPT 套餐 OAuth 授权、模型目录与思考强度校验 |
| `platform/shared_ui/` | 四个模块共用的界面层（设计令牌、外壳、动效、插画与字体），由主服务在 `/ui/` 提供 |
| `tools/index_fulltext.py` | 全文建索引工具：枚举文件名，合并摘要库、文件定位记录与目录结构中的书目信息，排除重复副本与非论文资料 |
| `tools/build_fonts.py` / `tools/paint_art.py` / `tools/refine_art.py` | 开发期工具：生成界面字体子集、程序化插画构图，以及用本机 SDXL 补笔墨质感与空状态小景，产物提交到 `shared_ui/` |

## 3. 数据存储（均在 `user_data/`）

| 位置 | 内容 | 写入者 |
|---|---|---|
| `research/research_tasks.sqlite3` | 研究任务（主题、构想、评审、文献、判断、提案、事件） | ResearchTasks 协调线程 |
| `research/idea.csv` | 构想存档 | TaskStore |
| `research/report_exports/` | 完整报告 HTML 版本 | OriginalReportExports |
| `abstract-index/` | 摘要库导入快照与向量 | RetrievalService |
| `fulltext-index.sqlite3` | 全文文件索引 | tools/index_fulltext.py |
| `reader/settings.json` | 共享 API 配置、精读运行提示词（密钥只在服务端） | 精读设置接口 |
| `reader/documents/` | 上传或载入的原文快照与提取文本 | 精读上传/载入 |
| `reader/*.sqlite3` | 精读任务、文献记忆、待读清单、变量与术语、组会填报记录、提示词库 | storage / memory / reading_* / prompt_library |
| `reader/reading_queue_files/` | 待读清单保存的原文件副本 | reading_queue |
| `reader/reading_sync.json` · `reader/tencent_docs_auth.dpapi` | 组会填报目标表格设置；腾讯文档授权（DPAPI 加密） | reading_sync / tencent_docs |
| `chatgpt-oauth/` | 加密保存的 OAuth 令牌 | chatgpt_oauth |
| `admin-password.txt` | 提示词管理员随机密码 | run.py |

浏览器只保存外观、动效偏好、草稿与阅读位置等便利信息（localStorage / sessionStorage），不保存密钥或研究结果。

## 4. 接口总览

### 主服务（研究构想）

| 方法与路径 | 用途 | 前端节奏 |
|---|---|---|
| `GET /` · `/setup` · `/architecture` | 研究构想页、首次配置指南、架构讲解 | 页面 |
| `GET /ui/<资源>` | 共享界面资源（css/js/svg/woff2/webp/png/json），ETag 协商缓存 | 页面加载 |
| `GET /api/health` | 摘要库状态、阶段模型、Ollama 可用性 | 启动时、就绪变化时 |
| `GET /api/workspace/overview` | 工作台概览：研究、精读、地图、提示词数量与就绪状态（只读、轻量） | 首页与指南页 |
| `GET /api/retrieval/status` · `POST /api/retrieval/prepare` | 摘要库准备进度与启动 | 准备中 1.5 s，其余 5 s |
| `GET/POST /api/tasks` | 列出 / 创建研究任务（`request_id` 幂等）。`?q=` 在服务端按主题或编号前缀搜索全部历史，`?archived=1` 列出已移出的研究；响应带 `archived_count` | 历史 10 s；搜索输入停顿 260 ms 后 |
| `GET /api/tasks/<id>?after_revision=n` | 任务快照；修订号未变返回 204 | 运行中 1.5 s，空闲 4 s |
| `POST /api/tasks/<id>/stop` · `/resume` | 停止新请求 / 断点继续 | 用户操作 |
| `POST /api/tasks/<id>/delete` | 永久删除已结束或暂停的研究（构想、提案、文献判断），并重写 idea.csv；进行中的研究拒绝删除 | 用户操作 |
| `POST /api/tasks/<id>/archive` `{"archived": bool}` | 移出 / 恢复历史（可撤销，不删除任何结果；进行中的研究返回 409） | 用户操作 |
| `GET/POST /api/tasks/<id>/report` · `GET …/report/file` | 完整报告导出状态、启动、文件 | 导出中 2 s |
| `GET/POST /api/prompts` · `/api/admin/*` | 运行提示词读取与保存（需登录） | 对话框 |
| `POST /api/generate` · `/judge` · `/retrieve` · `/optimize` | 单阶段接口，供本地脚本使用；浏览器只用任务接口 | — |

### 精读模块（挂载于 `/papers`）

| 方法与路径 | 用途 |
|---|---|
| `GET /papers/` · `/papers/memory` · `/papers/knowledge` · `/papers/prompt-library` · `/papers/reading-sync` · `/papers/scholars` | 页面，由 `workspace_page()` 注入挂载前缀与模块导航；`/papers/#queue`、`#directory` 是精读页内的待读清单与已读目录 |
| `POST /api/documents` · `GET /api/documents/<id>[/source]` | 上传提取、读取提取正文、下载原文件 |
| `GET/POST /api/jobs` · `/api/jobs/<id>` · `…/stop` · `…/resume` · `…/export/<md|html>` | 精读任务（`request_id` 幂等，失败可续跑）。列表只返回摘要字段，不含报告正文；`?archived=1` 列出已移出的记录。详情的 `html` 是阅读版，结束后另带 `audit_html`（含原文定位）；导出为阅读版 |
| `POST /api/jobs/<id>/archive` `{"archived": bool}` | 移出 / 恢复精读记录（文献地图卡片保留；进行中的分析拒绝） |
| `GET /api/settings` · `POST /api/providers` · `…/test` · `/api/chatgpt/*` | 共享 API 配置与 ChatGPT 套餐授权 |
| `POST /api/prompts` · `GET /api/prompts/defaults` | 精读运行提示词 |
| `POST /api/library/reveal` · `GET /api/library/download?uid=` | 在资源管理器中选中原文件；下载原文件（可超过 40 MB 的精读上限） |
| `GET/POST /api/reading-queue` · `/upload` · `PATCH /api/reading-queue/<id>` · `…/source` · `…/read` · `GET /api/reading-catalog` | 待读清单（加入、上传、编辑、补全文、载入精读）与已读目录 |
| `GET /api/knowledge/papers`（按论文的文件夹） · `/api/knowledge`（`paper=` 为文件夹内记录） · `/api/knowledge/<id>` · `POST …/<id>`、`…/split`、`/api/knowledge/rebuild` · `/api/knowledge-export/<csv|json>` · `/api/scholars` | 变量与术语、学者统计 |
| `/api/reading-sync/*` | 组会填报：设置、腾讯文档授权与回调、读取表头、生成预览、上传与核对 |
| `GET /api/library/status` · `/search` · `/papers/<uid>` · `POST /api/library/import` | 全文库检索、预览、载入。`/search` 参数 `scope=all|title|author|doi`、`local_only`（只看本机有全文）、`source=auto|online|local`；结果带完整书目、`local.status` 与 `match_kind/match_label`；本机检索第一页在没有完全一致的题名时，依次补充主标题一致、文件名一致和相似题名（只扫描题名索引，相似度只作候选）。`uid` 为 `api:<文献编号>`（网站书目，打开时按附件文件名精确定位）或 `file:<哈希>`（本机文件） |
| `GET /api/memory` · `/api/memory/papers/<id>` · `/api/memory/versions/<job>` 等 | 文献地图读取、编辑、关联、分类、备份恢复 |
| `GET /api/jobs/<id>/delete-plan` · `DELETE /api/jobs/<id>` · `GET /api/memory/papers/<id>/delete-plan` · `DELETE /api/memory/papers/<id>` · `GET /api/projects/<id>/delete-plan` | 永久删除精读、文献卡片或整个课题（默认课题只能清空）：先返回将删除的内容供确认，再删除记录及只属于它们的变量与术语、关联；没有其他记录使用的原文件移入系统回收站；进行中或正在定位的内容拒绝删除 |
| `GET/POST /api/projects` · `POST/DELETE /api/projects/<id>` · `POST /api/jobs/<id>/project` · `POST /api/memory/papers/<id>/project` | 研究课题的列表、新建、重命名与说明、归档、删除（仅空课题）；把精读或卡片连同它的全部精读移到另一课题。精读、地图、变量与术语、待读清单、已读目录、学者统计的读取接口都接受 `project=<id>`（空为全部课题） |
| `GET /api/bridge/references` · `POST /api/bridge/import` | 研究参考文献与本地全文的连接 |
| `/api/prompt-library/*` | 提示词库集合、版本、导入、备份 |

## 5. 后台执行

- **研究任务**：`ResearchTasks._loop` 是唯一的协调线程，按阶段拆成单元（生成每批 10 个、评审每批 20 个、每个入选构想一次检索、一次判断与优化），在并发额度内提交到 API 线程池；结果只由协调线程写回 SQLite，修订号随之递增。停止只阻止新单元，已发出的请求返回后照常保存。
- **精读任务**：`ThreadPoolExecutor(2)`。短文一次生成报告；长文先按 18,000 字符分段提取证据（按内容哈希缓存，可续跑；某段达到输出上限时自动对半拆小，最多三层），必要时分层压缩。报告按提示词目录撰写：达到输出上限时保存已完成的小节（`report_cursor`），下一次请求只写剩余小节，一轮最多 4 次；网络中断或停止后点“继续”也只重写未完成的小节。完成后写入文献地图、积累变量与术语，并排队一次后台定位；开启自动填报时排入组会填报。
- **组会填报**：单线程，每 5 秒检查待整理与待上传记录；读取可重试 2 次，写入结果不确定时只回读核对原行，不重复发送。
- **文献地图定位**：单线程队列，启动时检查遗留的待定位卡片；解析规则更新（`report_digest.VERSION`）后，启动时按最新报告重读卡片，人工修改和模型写的要点不被覆盖。“重新定位”先重读报告再调用模型；模型失败不自动重试。
- **摘要库准备**：导入 CSV → 检查/下载嵌入模型 → 计算向量 → 生成检索快照，状态通过 `/api/retrieval/status` 暴露。
- **报告导出**：本地排版线程，不调用模型。

## 6. 前端分层

```text
platform/shared_ui/            /ui/ —— 所有页面共用
  tokens.css                   设计令牌：色彩（宣纸、墨、松石、赭石等）、字体、间距、圆角、阴影、动效曲线；浅色 / 深色
  fonts.css + fonts/           界面宋体子集（思源宋体 SemiBold 的改名子集，OFL），按 unicode-range 分片
  base.css                     重置、排版、滚动条、选区、焦点
  components.css               按钮、输入、卡片、徽标、对话框、提示、菜单、命令面板、骨架、进度
  shell.css                    侧栏、顶栏、纸面工作区、模块导航、山水题图、页内过渡
  motion.css                   入场、揭示、印章、墨迹等关键帧与减少动态时的降级
  boot.js                      首帧前应用外观与动效偏好，标记页面间导航以跳过重复入场
  shell.js                     外观切换、移动端侧栏、页内过渡、Ctrl/⌘+K 命令面板
  motion.js                    动效引擎：分字入场、滚动揭示、数字滚动、磁吸、墨点涟漪、指针光、滑动指示器、动态内容自动增强
  ambient.js                   背景水彩（WebGL，按墙钟时间连续，跨页不跳变；不可用时降级为静态渐变）
  art/                         程序化山水插画（灰度遮罩，由 CSS 按主题着色）与航线、梅花点数据
  icons.svg                    Lucide 图标集合
  model-picker.css/.js         模型与思考强度选择器
  setup.css / setup.js         首次配置指南页（docs/setup.html，由 /setup 提供）
platform/研究流水线Web/static/   研究构想页：app / views / tasks / reports / exports / prompts / reading-bridge / overview + workspace.css
platform/paper_reader/static/   精读、文献地图、提示词库页面与各自样式；`memory-network.js` 是文献网络：总览按主领域分岛只连已读论文，点一篇进入局部网络（中心论文、内圈已读关联、外圈按作用分组的引用文献，右侧面板同步列表）
```

约定：

1. 页面脚本只负责数据与状态，ID 是脚本契约；视觉与动效只通过类名、`data-*` 属性和设计令牌表达。
2. 页面之间是普通的多页应用。模块切换关闭跨文档视图过渡（`navigation: none`），避免不同布局的整屏快照叠加造成重影和闪动。页面间导航仍跳过重复入场；页内过渡需要的名字（`rf-main`、`rf-panel`）只在该过渡进行时才挂上，详见 docs/design-system.md 的动效编排一节。
3. 轮询会频繁重绘，局部更新先比较签名（`dataset.signature`）；动效引擎只为新插入的节点播放入场，不会在每次轮询时重播。
4. 系统“减少动态效果”或工作台的“动效：关闭”会停用全部动画与背景渲染；“简洁”保留过渡、关闭持续运动。
5. 精读模块的 CSP 只允许同源脚本，界面层不能使用内联脚本或外部 CDN；全部资源随包离线提供。

## 7. 设计取舍

- **单进程、两个 Flask 应用**：保持精读模块可独立运行，又让研究构想直接复用共享 API 配置与全文库；代价是两边各自维护一份同构的 `/ui/` 资源路由。
- **任务持久化优先于长连接**：浏览器提交后立即返回，进度靠轮询带修订号的快照；关闭页面不影响后台运行，刷新可恢复。
- **不截断、不自动重试**：上下文不足或输出不完整时停止并说明，避免重复计费和静默丢证据。
- **插画为遮罩而非彩色位图**：一套资源同时适配浅色与深色主题，色彩由令牌控制，体积小且可以分层视差。
