# CEAI - Cheat Engine AI 助手

通过命名管道让 AI 直接操控 Cheat Engine 修改本地单机游戏内存的助手工具。

## 文件说明

| 文件 | 作用 |
|---|---|
| `ce_bridge.lua` | 在 CE 中运行的命名管道服务器(监听 `\\.\pipe\CEAIBridge`),提供 `ping/exec/attach/read/write/scan` 命令 |
| `ce_ai.py` | AI 助手主程序:DeepSeek 对话 + 工具调用循环,ctypes 直连命名管道(零第三方依赖) |
| `test_mock_server.py` | 模拟 CE 服务器,用于验证 Python 端通信协议 |

## 环境要求

- Cheat Engine 7.x(Windows)
- Python 3.8+(标准库即可,无需 pip 安装任何包)
- DeepSeek API Key

## 使用步骤

### 1. 在 CE 中启动桥接服务器

打开 Cheat Engine,Lua 控制台(`Ctrl+Alt+L` 或菜单 `File > Execute Script`),粘贴 `ce_bridge.lua` 全部内容并执行。看到输出:

```
[CEAI] bridge listening on \\.\pipe\CEAIBridge
```

即成功。

### 2. 启动 AI 助手

设置 API Key 后运行(三选一):

```powershell
# 方式一:环境变量
$env:DEEPSEEK_API_KEY = "sk-xxxx"
py ce_ai.py

# 方式二:命令行参数
py ce_ai.py --key sk-xxxx

# 方式三:程序运行时手动输入(未设置时提示)
py ce_ai.py
```

### 3. 开始对话

直接输入自然语言指令,例如:

- 「帮我找到金币的内存地址,当前金币是 100」
- 「附加到 Game.exe 进程」
- 「把血量的值改成 9999」

AI 会通过 `run_lua` 工具在 CE 中自动扫描、读取、写入内存,并汇报结果。

## 内置命令

| 命令 | 说明 |
|---|---|
| `/status` | 检查 CE 连接状态和当前附加的进程 |
| `/attach <进程名>` | 附加到指定进程 |
| `/lua <代码>` | 手动在 CE 中执行一段 Lua 代码(调试用) |
| `/quit` `/exit` `/bye` | 退出 |

## 通信协议

命名管道 `\\.\pipe\CEAIBridge` 上的二进制帧格式:

```
[4 字节长度 LE] [请求/响应 JSON 体]
```

Python 端示例请求:

```json
{"cmd": "exec", "code": "return readInteger(0x00401000)"}
```

桥接服务器支持的 `cmd`:

| 命令 | 参数 | 说明 |
|---|---|---|
| `ping` | - | 检查连接,返回 CE 版本与当前进程信息 |
| `exec` | `code` | 执行任意 Lua 代码块并返回其 JSON 可编码的结果 |
| `attach` | `name` | 附加到指定进程 |
| `read` | `address`, `type` | 读取内存(`int/int64/byte/float/double/string`) |
| `write` | `address`, `type`, `value` | 写入内存 |
| `scan` | `value`, `vtype`, `maxResults` | 内存扫描(精确值) |

## 安全边界

- 仅协助修改**本地单机游戏**。
- 不协助在线多人游戏作弊、反作弊绕过或网络欺骗。
- 不确定的场景会先询问确认。

## 开发备注

- 通信协议基于 CE 官方源码 `luapipeserver.pas`(LuaPipeServer)与 `luapipe.pas`(TPipeConnection),方法名(`createPipe/readBytes/writeBytes/getAddressSafe` 等)均已与源码核对。
- `ce_ai.py` 通过 `ctypes` 直接调用 Win32 API(`CreateFileW/WriteFile/ReadFile`),无需 pywin32。
- 运行 `py test_mock_server.py` 可离线验证 Python 端与管道服务器之间的通信。
