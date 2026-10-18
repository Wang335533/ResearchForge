# ResearchForge · Windows 研究工作台

ResearchForge 把研究构想、论文精读、文献积累和提示词管理放在一个本地工作台中。本次发布同步 2026-10-19 的 Windows 版本，包括完整前端、Python 后端、内置提示词、界面素材与配置模板。

**支持 Windows 10 / 11 x64。当前仅提供 Windows 版本。**

## 下载与启动

下载 [Windows 完整便携包](https://github.com/Wang335533/ResearchForge/releases/download/v2026.10.19/ResearchForge-Windows-x64.zip)，或到 [Release 页面](https://github.com/Wang335533/ResearchForge/releases/tag/v2026.10.19) 查看发布说明与 SHA256。完整包带有 Python 3.12、应用依赖、Ollama CPU / Vulkan 检索运行时和 Qwen3-Embedding 0.6B，不需要另装 Python、pip 或 Ollama。

GitHub 的 **Code → Download ZIP / Source code** 是源码包，不含体积较大的运行环境和模型；需要解压即用时请下载名为 **ResearchForge-Windows-x64.zip** 的 Release 附件。

1. 将完整包解压到可写、路径较短的目录，例如 `D:\ResearchForge`。不要在压缩包预览中启动。
2. 双击 `START-WINDOWS.cmd`，浏览器会打开工作台。
3. 在「论文精读 → 共享 API 设置」配置自己的模型服务，或独立完成 ChatGPT OAuth 授权。

默认从本机端口 8331 开始尝试，端口被占用时自动选择其他端口。实际网址以启动提示和 `user_data/portable-state.json` 为准。再次双击启动器会打开同一实例；关闭网页或启动窗口不会停止后台服务。

自检与前台启动（在解压后的程序目录中执行）：

```powershell
.\runtime\python\python.exe -I -B -X utf8 portable.py --check
.\runtime\python\python.exe -I -B -X utf8 portable.py --no-browser
```

前台运行时按 Ctrl+C 关闭该实例。需要重启后台实例时，用 PowerShell 7 执行：

```powershell
pwsh -NoProfile -File .\tools\restart-workbench.ps1
```

重启脚本会核对进程归属，并在仍有研究、精读、地图整理或导出任务时拒绝中断。启动日志位于 `user_data/logs/`；重启日志位于 `work/`。若系统提示软件未签名，请先核实下载来源。

## 功能

|模块|用途|
|---|---|
|研究构想|围绕问题生成构想、评审筛选、检索自有摘要库，并形成研究提案；保存阶段结果与任务记录|
|论文精读|读取 PDF / Markdown / TXT，分段提取证据、生成结构化中文报告，支持继续分析、原文定位和导出|
|待读清单与已读目录|保存待读文件，将同一论文的多次精读归集，查看阅读积累|
|文献地图|组织论文卡片、分类、关系和历史版本，连接构想与已读文献|
|变量与术语|从报告整理变量测量与理论术语，按中英文论文浏览，编辑、拆分、比较和导出|
|学者统计|根据已读论文元数据查看作者分布|
|提示词库|管理个人提示词、版本与备份；内置运行提示词也随源码完整提供|
|组会填报|由用户连接自己的腾讯文档表格，按表头整理精读结果并写入；未配置时不连接任何表格|
|全文库连接|索引用户自有 PDF / MD / TXT，按 DOI、题名和作者查找、预览并加入精读|

界面包含浅色 / 深色外观、山水插画、宋体子集、命令面板，以及完整 / 简洁 / 关闭三档动效。本版合并跨页过渡、首屏布局和字体缓存优化，移除了按钮点击时的涟漪闪烁。

## 模型服务与登录

发布包没有预设的个人账号、密钥或可用额度。模型调用需要联网，并使用你自己的授权或 API 配置；是否可用、支持哪些模型和额度以服务商实际返回为准。

- **ChatGPT OAuth**：在共享 API 设置中点击 **Continue with ChatGPT**。程序保留独立登录、模型目录、思考强度选择和续期实现，不携带开发者的登录状态，也不会复制已有 Codex 登录令牌。未登录时显示的模型目录只是候选，不能作为账户权限证明。
- **兼容 API**：在共享 API 设置中填写自己的服务地址、密钥与模型。默认 DeepSeek 条目仅有公开接口地址，密钥为空。
- `chatgpt_config.py` 保存非机密连接参数和默认选择。程序可读取使用者本机 Codex 配置中的模型与思考强度；仓库和下载包不包含该配置文件。
- ChatGPT 路由开启时，研究构想的默认阶段配置来自该路由；需要以兼容 API 作为构想阶段默认值时，将 `chatgpt_config.py` 的 `ENABLED` 设为 `False`，再配置 `config.json` 中的 `stages` 并重启。不要将密钥写进源码或 `config.json`。

登录工具与实现说明见 [ChatGPT 接入说明](docs/ChatGPT套餐接入.md)。本次发布验证使用空白配置，没有替任何人登录或发送收费模型请求。

## 配置自己的文献

**本项目不附带论文库、摘要语料、全文索引、私人文献 API 或任何访问凭据。** 文献连接能力保留，资料和权限需要使用者自行提供。

### 上传论文精读

在论文精读页上传 PDF / MD / TXT 即可；无需先建立摘要库。扫描版 PDF 需要先做 OCR。文本提取并不保证完整识别复杂公式、图片和表格，重要内容应回看原文。

### 摘要检索

按 `examples/corpus.template.csv` 的表头填写自有文献元数据，保存为 `user_data/corpus.csv`，再点击「准备检索」。模板只有表头，不含示例论文。程序使用包内嵌入模型生成本地检索索引。

默认中英文等额检索，每种语言最多 25 篇；只使用一种语言时，将 `config.example.json` 复制为 `config.json`，设置 `languages` 为 `["en"]` 或 `["zh"]`。相对路径以程序目录为基准。

### 全文目录

将自有论文放入 `user_data/papers/`，然后运行：

```powershell
.\runtime\python\python.exe -I -B -X utf8 tools\index_fulltext.py
```

可用 `examples/fulltext-metadata.template.csv` 补充准确书目，并通过 `--metadata` 指定。多个资料目录使用 `config.json` 的 `fulltext_roots` 数组；已有文件定位索引可通过 `fulltext_locator` 指定。新增资料后重新建索引。

可选的 `literature_api_manifest` 默认为空。确有自己的文献接口时，才配置自己的 manifest；接口书目与本机全文定位分别处理。无接口记录或未匹配到本地文件，不代表没有相关研究。

## 完整源码与提示词

```text
ResearchForge-Windows-x64/
├── START-WINDOWS.cmd              Windows 启动入口
├── portable.py / run.py           便携运行、配置与数据隔离
├── chatgpt_*.py                   通用 OAuth、模型目录与连接配置
├── config.example.json           空白路径与功能配置示例
├── platform/
│   ├── 研究流水线Web/              研究构想后端与前端
│   │   └── prompts/              S1 / S2 / S4 系统和用户提示词 JSON
│   ├── paper_reader/             精读、地图、阅读目录、变量术语、组会填报
│   │   ├── prompts.py            精读、证据提取与地图整理的默认提示词
│   │   └── static/               页面、样式与浏览器脚本
│   ├── 论文检索/                  摘要检索与索引服务
│   └── shared_ui/                共用样式、动效、字体和插画
├── tools/                        建索引、登录、重启和界面素材构建工具
├── examples/                     空白 CSV 模板
├── docs/                         架构、设计系统、接入和开发说明
└── runtime/                      Release 内含完整运行环境；Git 中保留来源与许可
```

提示词还分布在 `platform/paper_reader/report_workflow.py`（分段报告与证据约束）、`reading_knowledge.py`（变量术语重整）、`reading_sync.py`（按表头提取）、`memory_jobs.py`（地图整理调用）中。这些实现随本版完整提供。运行后在界面保存的个人提示词、精读记录和 API 设置属于 `user_data`，不包含在公开版本中。

研究构想运行提示词的管理账号为 `admin`，随机密码首次运行后写入 `user_data/admin-password.txt`；没有固定共享密码。

开发者可阅读 [开发说明](docs/开发说明.md)、[模块与接口](docs/architecture.md)、[界面设计系统](docs/design-system.md)。历史架构 HTML / PPT 保留作概览，具体行为以当前源码和文档为准。`requirements.lock.txt` 记录本版 Python 依赖；插画和字体重建的额外依赖见各工具说明，不是日常启动所需。

## 数据与分享边界

- `user_data/` 保存本次安装产生的论文、索引、报告、阅读历史、地图、提示词、密钥、OAuth 状态和管理员密码。
- Windows OAuth 凭据由当前用户的 DPAPI 加密保存；其他 API 设置按程序实现存放在本地配置中，备份时仍应按敏感数据处理。
- 公开源码和完整下载包排除个人配置、文献原文与派生索引、登录凭据、API key、日志、缓存和开发会话。
- 分享时发送原始 Release 压缩包；不要直接转发已经使用过的目录。升级前备份自己的 `user_data` 和配置，避免覆盖。
- 工作台用于单人本机使用，默认监听回环地址。模型分析会把所需正文和研究说明发送至所选服务商；组会填报在配置后会写入自己的腾讯文档。

模型生成内容需要人工核对，摘要匹配不替代全文证据。第三方软件、字体、图标及模型的许可见 `THIRD_PARTY_NOTICES.txt` 和 `runtime/licenses/`，本次发布不为原应用另行声明开源许可证。
