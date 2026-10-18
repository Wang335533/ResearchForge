# ChatGPT 套餐 OAuth 接入

本安装已将研究构想（生成、评审、优化）、论文精读和文献地图调用接入官方 Responses API。

## 配置和登录

非机密连接参数和后备默认值显式写在根目录 `chatgpt_config.py`。新对话默认读取本机 Codex `config.toml` 的 `model` 与 `model_reasoning_effort`；本机文件不可用时采用 `gpt-6-astra` / `xhigh`。模型目录会检查这些值在当前账户上是否受支持。

网络默认沿用系统代理；Windows 中仅设置 `NO_PROXY` 时仍读取系统的 HTTP/HTTPS 代理，显式代理环境变量优先，`NO_PROXY=*` 和 `USE_SYSTEM_PROXY=False` 均保持直连。连接超时为 20 秒，流式读取等待上限为 1800 秒，以容纳高强度思考；网络错误区分连接、代理、证书与读取超时，保留已返回内容且不自动重发模型请求。

在「研究构想」与「论文精读」输入区下方，点击模型名或思考强度打开上拉菜单。两个入口分别记住选择；「使用 Codex 默认值」可恢复当前 Codex 配置。提交时将选择发送到后端，保存到任务快照；继续任务沿用原选择，重新分析可调整选择。文献地图整理继承原精读任务的模型和强度。共享 API 设置只负责授权、账户及接口管理，不再修改对话模型。

在「论文精读 → 共享 API 设置」选择「ChatGPT 套餐（OAuth）」，点击 **Continue with ChatGPT**，在系统浏览器完成登录并授权使用套餐额度。已有 Codex 登录不会被复制或当作本应用授权。授权后直接使用本账户的 `/v1/models` 目录，保持官方顺序与模型对应的思考档位；模型菜单中的「刷新列表」强制更新目录，切换账户会重新同步。未授权时仅显示本机 Codex 目录（无本机目录时使用随包快照），明确标记为待授权核验。

思考强度按模型提供 Low、Medium、High、Extra High、Max 等实际支持档位，后端使用 `reasoning={"effort": ...}`。Codex 的 Ultra 含多代理执行，不是公开 Responses API 的有效 effort，因此保留说明并禁用该选项，不会偷偷降为 Max。后台同时拒绝不在账户目录中的模型和不受支持的强度。

也可以在安装目录执行：

```powershell
runtime\python\python.exe -I -B -X utf8 tools\chatgpt_login.py
runtime\python\python.exe -I -B -X utf8 tools\chatgpt_login.py --status
runtime\python\python.exe -I -B -X utf8 tools\chatgpt_login.py --models
runtime\python\python.exe -I -B -X utf8 tools\chatgpt_login.py --test
```

`--test` 发送一次简短调用，会使用授权账户的额度；仅收到 `response.completed` 才算成功。

## Python 调用

`chatgpt_oauth.complete(system, user, model=..., reasoning_effort=..., max_tokens=...)` 使用 OpenAI Python SDK。内部从本应用授权存储读取 access token，作为 `OpenAI(api_key=...)` 的 Bearer 凭据，调用 `client.responses.create`，显式设置 `store=False`、`stream=True`、`max_retries=0`。

授权采用独立客户端注册、稳定 host ID、PKCE S256、state、nonce 和经 JWKS 验证的 ID token。只有获授 `chatgpt.tokens.use.direct` 才允许调用。续期在进程间加锁，成功后一起保存 access token、轮换 refresh token、期限和权限。临时错误保留凭据，终止续期错误要求重新授权。断流、`response.failed` 和 `response.incomplete` 都不能当作成功，也不会自动切换到其他计费渠道。

本机凭据在 `user_data/chatgpt-oauth/credentials.dpapi` 中由 Windows DPAPI 加密，仅原 Windows 用户可解密；不要分享使用后的 `user_data`。令牌不写入 Python 源码、共享 API 配置、任务快照或日志。退出登录尝试撤销远端会话，并清空该注册的本地令牌，保留注册身份供重新登录。

续期返回的新凭据会先加密暂存，完成身份签名验证后才启用；若验证公钥暂时无法下载，下次继续验证这份响应，避免再次使用已轮换的旧 refresh token。公钥下载与模型请求使用相同的代理配置，退出登录也会清除暂存凭据。

本次分发的环境与空白实例检查见 `BUILD_CHECKS.txt`。这些检查不证明真实账户已获授权；只有使用者独立登录并实际调用后，才能核验其账户可用性。

官方依据（2026-10-10 核对）：[注册与登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)、[Python SDK 示例与调用约定](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)、[账户与续期](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)、[错误恢复](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)。
