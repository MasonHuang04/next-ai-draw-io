# 本地 Codex 接入说明

这个分支提供两种可选入口，原版 Next AI Draw.io 的默认模型接入流程保持原样。

## 1. 在原版 Next AI Draw.io 中调用本机 Codex

在本目录运行：

```sh
python compatibility_bridge.py
```

另开终端运行 `python compatibility_bridge.py --connection-info`，查看当前电脑生成的本地连接信息。然后在原版软件 **Settings → Models → OpenAI** 中填写：

- Base URL：`http://127.0.0.1:61428/v1`
- Model：`codex-current`
- API key：上一步显示的 `local_connection_key`。这是本地服务连接密钥，不是 OpenAI API Key，不应提交到 Git 或贴到 issue。

这一路是一个兼容接口：原版界面调用 localhost，适配器运行 `codex exec`，再把文字或图形工具调用结果返回。它不共享 Desktop 会话历史；每个补全请求是临时调用，兼容 SSE 也不是逐字镜像。

## 2. 使用最近对话与绘图协作工作台

先安装 Python 3.11+、Node.js 和已正常登录的 Codex CLI。本地需要 draw.io 网页资源，可使用 Windows 已安装的 Next AI Draw.io 自带资源，也可在仓库根目录正常安装依赖后运行 `node scripts/fetch-drawio.mjs`，或设置 `DRAWIO_ASSETS`。

```sh
cd integrations/codex-workbench
npm ci
npm start
```

左侧“最近对话”读取真实的本机 Codex 会话，支持搜索、筛选、直接切换和收起；每段对话的草稿独立保存。中间画布可手动修改，右侧可继续对话。附加图纸时，工作台先保存文件，再把图纸路径放入请求；Codex 修改同一文件后，画布自动刷新。

本机已验证 Windows；启动器兼容解析不同平台的 Electron 路径，但没有宣称完成 macOS/Linux 桌面验证。

## 接入原理

- `compatibility_bridge.py`：给原版界面使用的 localhost Chat Completions 服务。
- `codex_rpc.py`：启动官方 `codex app-server`，通过 JSON-RPC 调用本地会话接口。
- `server.py`：提供本机界面接口、共享队列、实时状态、审批、停止与图纸同步。
- `approvals.py`：按请求类型校验审批响应，不增加未请求的权限。
- `ui/` 与 Electron 文件：最近对话、画布、聊天栏及本地文件选择。

两种入口都使用安装在用户电脑上的 Codex 和其正常登录状态，代码中没有共享的 GPT 密钥，也不提取或复制登录凭据。模型请求仍由 Codex 正常处理，正常网络行为和用量限制仍然适用。

当 Desktop 已持有会话时，工作台通过官方队列提交消息并同步公开历史，不争抢写入者。工作台自己执行时可实时显示、审批和停止；Desktop 独立连接内部尚未保存的逐字流及审批回调，无法由另一个进程直接接管。

## 个人信息检查

不能把“调用本机服务”理解为运行时完全不处理个人信息：运行时会读取本地会话、项目路径和选定图纸，用户发送的请求也会交给已登录 Codex 处理。

但 GitHub 上传内容只包含源码、通用测试和说明。开发时的真实会话 ID、标题、聊天记录、截图、本机绝对路径、登录信息、生成密钥及运行日志均不包含在本次源码添加中。运行目录由 `.gitignore` 排除，提交前还有自动检查。详见 [PRIVACY.md](PRIVACY.md)。

## 检查

```sh
python -m unittest discover -s tests -p "test_*.py"
npm run check
python tests/check_publishable.py
```

这些通用测试不会向真实 Codex 提交请求。完整的英文启动、配置与开发说明见 [README.md](README.md)。
