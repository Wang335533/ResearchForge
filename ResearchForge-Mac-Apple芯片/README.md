# ResearchForge 解压即用版

适用电脑：**苹果 M 系列芯片，macOS 14 或更新版本**。

## 打开方法

1. 完整解压到一个自己可以写入的文件夹，不要在压缩包预览中运行。
2. 双击 **START-MAC.command**。不需要安装 Python、pip、Ollama，也不需要管理员权限。
3. 浏览器会自动打开研究工作台。保留启动窗口，按 Ctrl+C 可以停止。

首次打开可能需要等待系统检查随包程序。再次双击同一文件夹的启动入口，会打开已经运行的工作台。默认尝试端口 8331；被占用时自动选择其他端口，实际网址以启动窗口为准。

这是未签名的个人分享软件。如果操作系统阻止启动，请按系统提示核实来源后由你自行决定是否允许；本包不会关闭系统安全检查。

## 包里已经装了什么

- Python 3.12 和全部应用依赖。
- Ollama 本地检索服务，以及 Qwen3-Embedding 0.6B 嵌入模型。
- 研究构想、论文精读、文献地图、提示词库，以及本地全文连接功能的源码。
- 架构网页、14 页 PPT、配置与 CSV 模板。

打开页面、管理提示词和检索自己的资料都可以在本机完成。分析论文和生成研究构想仍使用接收者自己配置的 API，需要联网和自己的额度。包里没有云端大语言模型，也没有任何人的 API 密钥。

## 第一次使用

进入「论文精读 → 共享 API 设置」，填写自己的服务地址、密钥和可用模型。随后上传 PDF / MD / TXT，就可以精读。也可以先使用提示词库。

研究构想需要摘要检索时，用 examples/corpus.template.csv 填写自己的文献，保存为 user_data/corpus.csv，再点击页面里的「准备检索」。内置模型已经就位，无需另装 Ollama 或下载模型。系统会为你提供的资料生成本地索引。

配置完整中英文语料时，默认各最多 25 篇；某一方不足会按两者共同可提供的数量检索。只有英文或中文语料时，将 config.example.json 复制为 config.json，把 languages 改成 ["en"] 或 ["zh"] 后重启。

## 连接自己的全文库

启动一次后，把自己的 PDF / MD / TXT 放到 user_data/papers。运行下面的命令建立全文索引，或者用 config.json 指定自己的资料路径：

```sh
./runtime/python/bin/python3 -I -B tools/index_fulltext.py
```

填写 examples/fulltext-metadata.template.csv 中的准确书目后，可加参数 `--metadata 你的全文元数据.csv`。原文件只读。新增文件后重新建索引。

## 数据在哪里

自己的 API、上传文件、报告、地图、提示词、管理员密码与日志，都保存在这个文件夹的 user_data 中。首次启动没有论文、摘要、阅读历史或个人提示词。

runtime 中只有公开软件、依赖和公开嵌入模型，不含论文库或原电脑的索引。启动器为本地检索分配独立端口，使用包内模型。

备份自己的 user_data。**分享给其他人时，发送最初的压缩包，不要把使用后的整个文件夹直接转发。**

## 配置与开发

- 架构说明：双击 架构说明.html，或打开 ResearchForge架构讲解.pptx。
- 更多功能、数据格式和开发路径：[开发说明](docs/开发说明.md)。
- 模型与资料路径：config.example.json；复制为 config.json 后修改，重启生效。
- 便携启动逻辑：portable.py；API 与资料仍由 run.py 配置。
- 依赖版本：requirements.lock.txt；构建来源：runtime/SOURCES.json。
- 自检：`./runtime/python/bin/python3 -I -B portable.py --check`。

保留所有第三方许可。该便携包面向个人本机使用，不提供多用户公共服务器部署。扫描 PDF 需要使用者先做 OCR。
