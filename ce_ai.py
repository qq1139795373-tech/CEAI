#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CEAI - Cheat Engine AI Assistant
一个通过命名管道与 Cheat Engine 通信的 AI 助手。

要求:
  - Python 3.8+ (标准库即可,无需第三方依赖)
  - DeepSeek API Key (OpenAI 兼容接口)

用法:
  1. 先在 Cheat Engine 中执行 ce_bridge.lua 开启管道服务器
  2. 运行本程序:  python ce_ai.py
     或者指定 API Key: python ce_ai.py --key sk-xxxx
     或设置环境变量 DEEPSEEK_API_KEY

输入命令:
  - 普通文本:与 AI 对话
  - /status   查看 CE 连接状态
  - /attach <进程名>  附加到进程
  - /quit     退出
"""

import argparse
import ctypes
import json
import os
import struct
import sys
import urllib.request
import urllib.error

# ====================== 常量 ======================

PIPE_NAME = r"\\.\pipe\CEAIBridge"
PIPE_TIMEOUT_MS = 10000  # 等待连接超时 (ms)

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
MAX_TOOL_ROUNDS = 12  # 防止模型无限循环调用工具

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
ERROR_PIPE_BUSY = 231
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_SEM_TIMEOUT = 121

# ====================== 命名管道客户端 (ctypes) ======================

# ====================== 命名管道客户端 (ctypes) ======================

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CreateFileW = _kernel32.CreateFileW
CreateFileW.restype = ctypes.c_void_p
CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                        ctypes.c_void_p]
WaitNamedPipeW = _kernel32.WaitNamedPipeW
WaitNamedPipeW.restype = ctypes.c_int
WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]

WriteFile = _kernel32.WriteFile
WriteFile.restype = ctypes.c_int
WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                      ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
ReadFile = _kernel32.ReadFile
ReadFile.restype = ctypes.c_int
ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                     ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
CloseHandle = _kernel32.CloseHandle
CloseHandle.restype = ctypes.c_int
CloseHandle.argtypes = [ctypes.c_void_p]

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def connect_pipe():
    """连接命名管道,返回句柄。失败抛出 RuntimeError。"""
    handle = CreateFileW(
        PIPE_NAME,
        GENERIC_READ | GENERIC_WRITE,
        0,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle not in (None, 0, INVALID_HANDLE_VALUE):
        return handle

    err = ctypes.get_last_error()
    if err == ERROR_PIPE_BUSY:
        # 服务器忙,等待可用
        if not WaitNamedPipeW(PIPE_NAME, PIPE_TIMEOUT_MS):
            raise RuntimeError("CE 管道服务器无响应,请确认 ce_bridge.lua 正在运行")
        handle = CreateFileW(
            PIPE_NAME,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle in (None, 0, INVALID_HANDLE_VALUE):
            raise RuntimeError("连接 CE 管道失败,错误码: %d" % ctypes.get_last_error())
        return handle
    raise RuntimeError("无法打开管道 %s (错误码 %d)" % (PIPE_NAME, err))


def write_pipe(handle, data: bytes):
    total = 0
    n = ctypes.c_ulong(0)
    buf = ctypes.create_string_buffer(data)
    while total < len(data):
        ok = WriteFile(
            handle,
            ctypes.byref(buf, total),
            len(data) - total,
            ctypes.byref(n),
            None,
        )
        if not ok:
            raise RuntimeError("写入管道失败,错误码: %d" % ctypes.get_last_error())
        if n.value == 0:
            raise RuntimeError("CE 端已关闭连接")
        total += n.value


def read_pipe_exact(handle, size: int) -> bytes:
    data = bytearray()
    n = ctypes.c_ulong(0)
    while len(data) < size:
        buf = ctypes.create_string_buffer(size - len(data))
        ok = ReadFile(handle, buf, size - len(data), ctypes.byref(n), None)
        if not ok:
            raise RuntimeError("读取管道失败,错误码: %d" % ctypes.get_last_error())
        if n.value == 0:
            raise RuntimeError("CE 端已关闭连接")
        data += buf.raw[:n.value]
    return bytes(data)


def close_pipe(handle):
    CloseHandle(handle)


def send_request(request: dict) -> dict:
    """向 CE 发送 JSON 请求并返回响应。"""
    body = json.dumps(request, ensure_ascii=False).encode("utf-8")
    header = struct.pack("<I", len(body))

    handle = None
    try:
        handle = connect_pipe()
        write_pipe(handle, header + body)

        resp_len_bytes = read_pipe_exact(handle, 4)
        resp_len = struct.unpack("<I", resp_len_bytes)[0]
        if resp_len <= 0 or resp_len > 64 * 1024 * 1024:
            raise RuntimeError("CE 返回了异常的长度: %d" % resp_len)
        resp_body = read_pipe_exact(handle, resp_len)
        return json.loads(resp_body.decode("utf-8"))
    finally:
        if handle:
            close_pipe(handle)


def ce_ping() -> dict:
    return send_request({"cmd": "ping"})


def ce_exec(code: str) -> dict:
    return send_request({"cmd": "exec", "code": code})


def ce_attach(name: str) -> dict:
    return send_request({"cmd": "attach", "name": name})


# ====================== DeepSeek API 客户端 ======================

class DeepSeekClient:
    def __init__(self, api_key: str, model: str = DEEPSEEK_MODEL):
        self.api_key = api_key
        self.model = model

    def chat(self, messages: list, tools: list = None, temperature: float = 0.3) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DEEPSEEK_API_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError("DeepSeek API 错误 %d: %s" % (e.code, detail))
        except urllib.error.URLError as e:
            raise RuntimeError("无法连接 DeepSeek API: %s" % e.reason)


# ====================== 工具定义 ======================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_lua",
            "description": (
                "在已附加的 Cheat Engine 中执行一段 Lua 脚本并返回结果。"
                "可用于读写游戏内存(如 readInteger/writeInteger/readFloat/writeDouble)、"
                "内存扫描(AOBScan)、获取模块基址(getAddress)、调用 CE 内置函数等。"
                "脚本必须能独立完整地完成一步操作,并返回可被 JSON 编码的值"
                "(数字/字符串/布尔/表)。脚本内可用 print 输出调试信息(会一并返回)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Lua 代码(CE Lua 5.3 语法)",
                    }
                },
                "required": ["code"],
            },
        },
    }
]

SYSTEM_PROMPT = """你是 Cheat Engine 的 AI 助手,负责协助用户在本地单机游戏中查找与修改内存数值。

能力与约定:
1. 通过 run_lua 工具在 Cheat Engine 中执行 Lua 脚本。脚本基于 CE Lua 5.3 环境,可用以下常用 API:
   - getAddress(string), getAddressSafe(string): 解析地址(支持 "module+offset"、"0x"、"符号")
   - readInteger/writeInteger(addr, val), readFloat/writeFloat, readDouble/writeDouble,
     readQword/writeQword, readBytes(addr, len)/writeBytes(addr, {b1,b2,...})
   - AOBScan(pattern[, start, stop]): 返回匹配地址列表(AddressList),用 [i] 或 .Element[i].Address 取地址
   - getOpenedProcessID(), getModuleList(), getProcessList(), openProcess(name)
   - print(...): 输出调试信息,会随结果一起返回给用户
2. 注意地址解析:优先使用 getAddressSafe 避免出错;若用户给出十六进制地址,直接用。
3. 写内存前应确认目标进程已附加且地址有效。
4. 涉及多步骤任务(如"先扫描再改值"),分多次调用 run_lua,每一步都告知用户进度。

安全边界:
- 仅允许协助修改本地单机游戏。不协助任何在线多人游戏作弊、反作弊绕过、网络欺骗。
- 对于无法判断的场景,询问用户游戏是否单机/本地。

沟通风格:简洁、步骤清晰,用中文回答。每次执行脚本后简要说明结果。"""


# ====================== 主逻辑 ======================

def run_lua_tool(client, args_str: str) -> dict:
    """执行 run_lua 工具,返回给模型的工具结果。"""
    try:
        args = json.loads(args_str)
        code = args.get("code", "")
    except Exception as e:
        return {"success": False, "error": "参数解析失败: %s" % e}

    if not code.strip():
        return {"success": False, "error": "未提供 Lua 代码"}

    # 包装脚本:捕获 print 输出并强制返回 JSON 可编码结果
    wrapper = (
        "local __out = {}\n"
        "local __old_print = print\n"
        "print = function(...)\n"
        "  local t = {}\n"
        "  for i = 1, select('#', ...) do t[i] = tostring(select(i, ...)) end\n"
        "  __out[#__out + 1] = table.concat(t, '\\t')\n"
        "end\n"
        "local __ok, __res = pcall(function()\n" + code + "\nend)\n"
        "print = __old_print\n"
        "if not __ok then return { success = false, error = tostring(__res) } end\n"
        "return { success = true, output = table.concat(__out, '\\n'), result = __res }\n"
    )

    try:
        resp = ce_exec(wrapper)
    except RuntimeError as e:
        return {"success": False, "error": "CE 通信失败: %s" % str(e)}

    if not resp.get("success"):
        return resp

    res = resp.get("result") or {}
    if isinstance(res, dict) and "success" in res:
        return res
    return {"success": True, "result": res}


def process_tool_call(client, messages, tool_call):
    """执行模型要求的工具调用,返回 (消息数组) 供下一轮对话。"""
    fn = tool_call.get("function", {})
    name = fn.get("name")
    args = fn.get("arguments", "{}")

    assistant_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [tool_call],
    }
    messages.append(assistant_msg)

    if name == "run_lua":
        print("\n[CE] 执行 Lua 脚本...")
        result = run_lua_tool(client, args)
        print("[CE] 完成" if result.get("success") else "[CE] 失败: %s" % result.get("error"))
        tool_msg = {
            "role": "tool",
            "tool_call_id": tool_call.get("id"),
            "content": json.dumps(result, ensure_ascii=False),
        }
        messages.append(tool_msg)
    else:
        tool_msg = {
            "role": "tool",
            "tool_call_id": tool_call.get("id"),
            "content": json.dumps({"success": False, "error": "未知工具: %s" % name}),
        }
        messages.append(tool_msg)

    return messages


def chat_once(client, messages):
    """单轮对话循环,处理工具调用直到模型给出最终答复。"""
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat(messages, tools=TOOLS)
        choice = response["choices"][0]
        msg = choice["message"]

        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                process_tool_call(client, messages, tc)
            continue

        final_text = msg.get("content") or ""
        messages.append({"role": "assistant", "content": final_text})
        return final_text

    return "(已达到最大工具调用轮数,对话终止。请重试或简化请求。)"


def show_status():
    try:
        info = ce_ping()
        if info.get("success"):
            pid = info.get("processID") or 0
            pname = info.get("processName") or "(未附加)"
            print("CE 已连接 | 附加进程: %s (PID %d)" % (pname, pid))
        else:
            print("CE 响应异常: %s" % info.get("error"))
    except RuntimeError as e:
        print("CE 未连接: %s" % e)


def load_api_key(args):
    key = args.key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        key = input("请输入 DeepSeek API Key (或设置环境变量 DEEPSEEK_API_KEY): ").strip()
    if not key:
        print("未提供 API Key,退出。")
        sys.exit(1)
    return key


def main():
    parser = argparse.ArgumentParser(description="Cheat Engine AI 助手")
    parser.add_argument("--key", help="DeepSeek API Key (或设置环境变量 DEEPSEEK_API_KEY)")
    parser.add_argument("--model", default=DEEPSEEK_MODEL, help="模型名,默认 deepseek-chat")
    args = parser.parse_args()

    api_key = load_api_key(args)
    client = DeepSeekClient(api_key, args.model)

    print("=" * 50)
    print("CEAI - Cheat Engine AI 助手")
    print("模型: %s | 管道: %s" % (args.model, PIPE_NAME))
    print("输入 /status 检查连接,/attach <进程名> 附加,/quit 退出")
    print("=" * 50)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit", "/bye", "退出"):
            print("再见!")
            break
        if user_input.lower() == "/status":
            show_status()
            continue
        if user_input.startswith("/attach "):
            name = user_input.split(" ", 1)[1].strip()
            try:
                res = ce_attach(name)
                if res.get("success"):
                    print("已附加到: %s (PID %d)" % (res.get("processName"), res.get("processID") or 0))
                else:
                    print("附加失败: %s" % res.get("error"))
            except RuntimeError as e:
                print("CE 未连接: %s" % e)
            continue
        if user_input.startswith("/lua "):
            # 手动执行一段 Lua,便于调试
            code = user_input.split(" ", 1)[1].strip()
            try:
                res = ce_exec(code)
                print(json.dumps(res, ensure_ascii=False, indent=2))
            except RuntimeError as e:
                print("CE 未连接: %s" % e)
            continue
        if user_input.startswith("/"):
            print("未知命令。可用: /status /attach /lua /quit")
            continue

        messages.append({"role": "user", "content": user_input})
        try:
            reply = chat_once(client, messages)
            print("\nAI: " + reply)
        except RuntimeError as e:
            print("\n错误: %s" % e)
            messages.pop()  # 移除失败的那条用户消息


if __name__ == "__main__":
    main()
