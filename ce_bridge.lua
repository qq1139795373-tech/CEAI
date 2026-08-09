-- ============================================================================
-- CE AI Bridge for Cheat Engine
-- 在 Cheat Engine 的 Lua 控制台运行本脚本,开启命名管道服务器。
-- 服务器监听 \\.\pipe\CEAIBridge,接收 JSON 格式命令并返回 JSON 结果。
--
-- 用法:
--   1. 打开 Cheat Engine,点击 "Table" -> "Show Cheat Table Lua Script"
--      或 "File" -> "Execute Script" (Lua 控制台)
--   2. 粘贴本文件全部内容并执行
--   3. 看到 "CEAI bridge listening..." 即成功
-- ============================================================================

local PIPE_NAME = "CEAIBridge"

local json = {}
local encode, decode

-- ====================== JSON 编码器 ======================
local escape_char_map = { ["\\"] = "\\", ["\""] = "\"", ["\b"] = "b", ["\f"] = "f", ["\n"] = "n", ["\r"] = "r", ["\t"] = "t" }

local function escape_char(c)
  return "\\" .. (escape_char_map[c] or string.format("u%04x", c:byte()))
end

local function encode_string(val)
  return '"' .. val:gsub('[%z\1-\31\\"]', escape_char) .. '"'
end

local function encode_number(val)
  if val ~= val or val <= -math.huge or val >= math.huge then return "null" end
  return string.format("%.14g", val)
end

local function encode_value(val, stack)
  local t = type(val)
  if t == "nil" then return "null" end
  if t == "boolean" then return tostring(val) end
  if t == "number" then return encode_number(val) end
  if t == "string" then return encode_string(val) end
  if t == "table" then
    if stack[val] then return '"<circular>"' end
    stack[val] = true
    local res, n = {}, 0
    local isArray = true
    local count = 0
    for k, v in pairs(val) do
      count = count + 1
      if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then isArray = false end
    end
    if isArray and count > 0 then
      for i = 1, count do
        if val[i] == nil then isArray = false break end
      end
    end
    if isArray and count > 0 then
      for i = 1, count do
        n = n + 1
        res[n] = encode_value(val[i], stack)
      end
      stack[val] = nil
      return "[" .. table.concat(res, ",") .. "]"
    else
      for k, v in pairs(val) do
        n = n + 1
        if type(k) == "string" then
          res[n] = encode_string(k) .. ":" .. encode_value(v, stack)
        else
          res[n] = encode_number(k) .. ":" .. encode_value(v, stack)
        end
      end
      stack[val] = nil
      return "{" .. table.concat(res, ",") .. "}"
    end
  end
  return '""'
end

encode = function(val)
  return encode_value(val, {})
end
json.encode = encode

-- ====================== JSON 解码器 ======================
local function decode_scanwhite(str, pos)
  while pos <= #str and (str:sub(pos, pos) == " " or str:sub(pos, pos) == "\t"
      or str:sub(pos, pos) == "\n" or str:sub(pos, pos) == "\r") do
    pos = pos + 1
  end
  return pos
end

local function decode_value(str, pos)
  pos = decode_scanwhite(str, pos)
  if pos > #str then return nil, pos end
  local c = str:sub(pos, pos)
  if c == "{" then
    pos = pos + 1
    local obj = {}
    while true do
      pos = decode_scanwhite(str, pos)
      if str:sub(pos, pos) == "}" then return obj, pos + 1 end
      local key
      key, pos = decode_value(str, pos)
      if type(key) ~= "string" then return nil, pos end
      pos = decode_scanwhite(str, pos)
      if str:sub(pos, pos) ~= ":" then return nil, pos end
      pos = decode_scanwhite(str, pos + 1)
      local val
      val, pos = decode_value(str, pos)
      obj[key] = val
      pos = decode_scanwhite(str, pos)
      local sep = str:sub(pos, pos)
      if sep == "," then pos = pos + 1
      elseif sep ~= "}" then return nil, pos end
    end
  elseif c == "[" then
    pos = pos + 1
    local arr = {}
    local n = 0
    while true do
      pos = decode_scanwhite(str, pos)
      if str:sub(pos, pos) == "]" then return arr, pos + 1 end
      local val
      val, pos = decode_value(str, pos)
      n = n + 1
      arr[n] = val
      pos = decode_scanwhite(str, pos)
      local sep = str:sub(pos, pos)
      if sep == "," then pos = pos + 1
      elseif sep ~= "]" then return nil, pos end
    end
  elseif c == '"' then
    pos = pos + 1
    local parts = {}
    local n = 0
    while true do
      if pos > #str then return nil, pos end
      local ch = str:sub(pos, pos)
      if ch == '"' then return table.concat(parts), pos + 1 end
      if ch == "\\" then
        pos = pos + 1
        local esc = str:sub(pos, pos)
        if esc == "u" then
          local hex = str:sub(pos + 1, pos + 4)
          n = n + 1
          parts[n] = string.char(tonumber(hex, 16))
          pos = pos + 5
        else
          local m = { ['"'] = '"', ["\\"] = "\\", ["/"] = "/", ["b"] = "\b",
                      ["f"] = "\f", ["n"] = "\n", ["r"] = "\r", ["t"] = "\t" }
          n = n + 1
          parts[n] = m[esc] or esc
          pos = pos + 1
        end
      else
        n = n + 1
        parts[n] = ch
        pos = pos + 1
      end
    end
  elseif c == "t" and str:sub(pos, pos + 3) == "true" then return true, pos + 4
  elseif c == "f" and str:sub(pos, pos + 4) == "false" then return false, pos + 5
  elseif c == "n" and str:sub(pos, pos + 3) == "null" then return nil, pos + 4
  else
    local numstr = str:match("^-?%d+%.?%d*[eE]?[+-]?%d*", pos)
    if numstr then
      local val = tonumber(numstr)
      if val then return val, pos + #numstr end
    end
    return nil, pos
  end
end

decode = function(str)
  local val, pos = decode_value(str, 1)
  if val == nil then return nil end
  return val
end
json.decode = decode

-- ====================== 管道服务器 ======================

local server
local function runServer()
  while true do
    server.acceptConnection()
    if not server.connected then
      sleep(50)
    else
      -- 协议:先读 4 字节 = 请求长度 (LE),再读请求体
      local lenBytes = server.readBytes(4)
      if lenBytes and #lenBytes == 4 then
        local len = lenBytes[1] + lenBytes[2] * 256 + lenBytes[3] * 65536 + lenBytes[4] * 16777216
        if len > 0 and len < 64 * 1024 * 1024 then
          local dataBytes = server.readBytes(len)
          if dataBytes and #dataBytes == len then
            local data = {}
            for i = 1, len do data[i] = string.char(dataBytes[i]) end
            local requestStr = table.concat(data)
            local ok, result = pcall(handleRequest, requestStr)

            local responseStr
            if ok then
              responseStr = result
            else
              responseStr = json.encode({ success = false, error = tostring(result) })
            end

            local respBytes = { string.byte(responseStr, 1, #responseStr) }
            local rlen = #respBytes
            local head = {
              rlen % 256,
              math.floor(rlen / 256) % 256,
              math.floor(rlen / 65536) % 256,
              math.floor(rlen / 16777216) % 256,
            }
            server.writeBytes(head)
            server.writeBytes(respBytes)
          end
        end
      end
      -- 客户端断开连接,回到 acceptConnection 等待下一个客户端
    end
  end
end

local function startServer()
  server = createPipe(PIPE_NAME, 4 * 1024 * 1024, 4 * 1024 * 1024)
  if server and server.valid then
    print("[CEAI] bridge listening on \\\\.\\pipe\\" .. PIPE_NAME)
    createNativeThread(runServer)
  else
    print("[CEAI] Failed to create pipe, is another instance running?")
  end
end

-- ====================== 请求处理 ======================

function handleRequest(requestStr)
  local req = json.decode(requestStr)
  if type(req) ~= "table" then
    return json.encode({ success = false, error = "Invalid JSON request" })
  end

  local cmd = req.cmd
  if cmd == "ping" then
    return json.encode({ success = true, response = "pong",
      version = "1.0",
      processID = getOpenedProcessID() or 0,
      processName = process or "" })
  elseif cmd == "exec" then
    local code = req.code or ""
    local fn, err = load(code, "CEAI_exec", "t", _ENV)
    if not fn then
      return json.encode({ success = false, error = "Lua syntax error: " .. tostring(err) })
    end
    local okExec, res = pcall(fn)
    if not okExec then
      return json.encode({ success = false, error = tostring(res) })
    end
    return json.encode({ success = true, result = res })
  elseif cmd == "eval" then
    -- eval 返回单个表达式的值(JSON 编码)
    local code = req.code or ""
    local fn, err = load("return (" .. code .. ")", "CEAI_eval", "t", _ENV)
    if not fn then
      return json.encode({ success = false, error = "Lua syntax error: " .. tostring(err) })
    end
    local okExec, res = pcall(fn)
    if not okExec then
      return json.encode({ success = false, error = tostring(res) })
    end
    return json.encode({ success = true, result = res })
  elseif cmd == "attach" then
    local name = req.name or req.process or ""
    if name ~= "" then
      local okAtt = openProcess(name, false)
      if okAtt then
        return json.encode({ success = true, processID = getOpenedProcessID(), processName = process or name })
      end
      return json.encode({ success = false, error = "Failed to open process: " .. name })
    end
    return json.encode({ success = false, error = "No process name given" })
  elseif cmd == "read" then
    local addr = getAddressSafe(req.address or "")
    if not addr then
      return json.encode({ success = false, error = "Invalid address: " .. tostring(req.address) })
    end
    local vtype = req.type or "int"
    local val
    if vtype == "int" then val = readInteger(addr)
    elseif vtype == "int64" then val = readQword(addr)
    elseif vtype == "byte" then val = readBytes(addr, 1, false)
    elseif vtype == "float" then val = readFloat(addr)
    elseif vtype == "double" then val = readDouble(addr)
    elseif vtype == "string" then val = readString(addr, req.length or 256)
    else val = readInteger(addr) end
    return json.encode({ success = true, address = addr, value = val })
  elseif cmd == "write" then
    local addr = getAddressSafe(req.address or "")
    if not addr then
      return json.encode({ success = false, error = "Invalid address: " .. tostring(req.address) })
    end
    local vtype = req.type or "int"
    local okW = false
    if vtype == "int" then okW = writeInteger(addr, req.value)
    elseif vtype == "int64" then okW = writeQword(addr, req.value)
    elseif vtype == "byte" then okW = writeBytes(addr, { req.value })
    elseif vtype == "float" then okW = writeFloat(addr, req.value)
    elseif vtype == "double" then okW = writeDouble(addr, req.value)
    elseif vtype == "string" then okW = writeString(addr, req.value, false)
    else okW = writeInteger(addr, req.value) end
    return json.encode({ success = okW, address = addr })
  elseif cmd == "scan" then
    -- 简化扫描:使用 CE 的 memscan
    local ms = createMemScan()
    local value = req.value
    local vtype = req.vtype or vtDword
    local scanOpt = req.scanOpt or soExactValue
    ms.firstScan(scanOpt, vtype, rtRounded, value, nil, 0, 0x7fffffffffffffff, "+W-C", fsmNotAligned, "1", false, false, false, false)
    ms.waitTillDone()
    local fl = createFoundList(ms)
    fl.initialize()
    local count = fl.getCount()
    local addresses = {}
    for i = 0, math.min(count - 1, (req.maxResults or 100) - 1) do
      addresses[i + 1] = fl.getAddress(i)
    end
    fl.destroy()
    ms.destroy()
    return json.encode({ success = true, count = count, addresses = addresses })
  elseif cmd == "nextscan" then
    return json.encode({ success = false, error = "nextscan requires session state; use exec with createMemScan in one block" })
  end

  return json.encode({ success = false, error = "Unknown command: " .. tostring(cmd) })
end

startServer()
