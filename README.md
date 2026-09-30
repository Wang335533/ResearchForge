# ResearchForge

本地优先的研究工作台：从研究主题生成构想、评审排序、检索文献并形成提案；精读论文并与自己的研究对照；把读过的文献沉淀为文献地图；收藏和复用提示词。

## 下载

到 [Releases](https://github.com/Wang335533/ResearchForge/releases/latest) 下载与电脑匹配的解压即用包，完整解压后双击启动入口即可，无需安装 Python、依赖或 Ollama。

| 版本 | 适用电脑 | 启动入口 |
|---|---|---|
| `ResearchForge-Mac-Apple芯片.zip` | 苹果 M 系列芯片，macOS 14 或更新 | `START-MAC.command` |
| `ResearchForge-Windows-x64.zip` | 64 位 Windows 10 / 11 | `START-WINDOWS.cmd` |

分析论文和生成构想使用你自己配置的 API（兼容 Chat Completions），包里没有任何人的密钥。嵌入检索、文献地图和提示词库都在本机完成。

## 功能

- **研究构想**：构想 → 七维评审 → 中英文文献检索 → 文献判断与提案 → 离线 HTML 报告。
- **论文精读**：上传 PDF / MD / TXT 或从本地全文库选择，生成完整精读报告与研究对比。
- **文献地图**：精读后的论文按领域、主题与研究问题自动归位，可建立文献关联。
- **提示词库**：收藏、分类、版本记录，可一键载入到精读的运行提示词。
- 全局命令面板（Ctrl/⌘ + K）、浅色 / 深色外观。

## 仓库结构

两个文件夹分别对应两个发行包，应用代码完全相同，只有启动脚本和文档按平台不同。

```
ResearchForge-Mac-Apple芯片/   Mac 版（不含 runtime 二进制）
ResearchForge-Windows-x64/     Windows 版（不含 runtime 二进制）
  platform/                    应用源码：研究流水线、论文精读、文献检索、共享界面层
  docs/ examples/ tools/       配置指南、CSV 模板、全文索引工具
  runtime/SOURCES.json         随包 Python、Ollama 与嵌入模型的官方来源和 SHA256
```

Python 运行时、Ollama 引擎和 Qwen3-Embedding 模型体积较大，只放在 Release 压缩包里，来源见各版本的 `runtime/SOURCES.json`。开发说明见各版本的 `docs/开发说明.md`。

## 许可

应用代码未声明开源许可。随包第三方组件的许可见各版本的 `THIRD_PARTY_NOTICES.txt` 与 `runtime/licenses`。
