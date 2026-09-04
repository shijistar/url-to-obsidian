# Windows Chrome CDP Extraction from WSL

When SPA (Single Page Application) content can't be extracted via Node.js extractor, `web_extract`, or curl because the page requires JavaScript rendering, use Chrome on the Windows host via CDP (Chrome DevTools Protocol).

## Prerequisites

- Chrome installed on Windows (`/mnt/c/Program Files/Google/Chrome/Application/chrome.exe`)
- PowerShell accessible from WSL (`powershell.exe`)
- Chrome must NOT already be running with the same user-data-dir (Chrome singleton lock prevents multiple instances)

## Step-by-Step Workflow

### 1. Launch Chrome with Remote Debugging

```powershell
# Via PowerShell Start-Process (avoids WSL shell escaping issues).
# -PassThru returns the process so we can stop THIS instance later (Step 7).
powershell.exe -Command "\$p = Start-Process 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList '--remote-debugging-port=9222','--no-first-run','--no-default-browser-check','--user-data-dir=C:\Users\<username>\chrome-debug','<TARGET_URL>' -PassThru; \$p.Id" > /tmp/chrome_debug_pid.txt
```

**Important**: Chrome binds to Windows `127.0.0.1:9222`. WSL2 cannot access this via `localhost` or Windows host IP due to NAT networking. All CDP communication must go through PowerShell.

### 2. Verify Chrome is Listening

```powershell
powershell.exe -Command "Get-NetTCPConnection -LocalPort 9222 -ErrorAction SilentlyContinue | Select-Object LocalPort,OwningProcess,State"
```

Should show `Listen` state.

### 3. List Open Tabs via CDP HTTP API

```powershell
powershell.exe -Command "
\$tabs = Invoke-RestMethod -Uri 'http://localhost:9222/json' -TimeoutSec 5
foreach (\$tab in \$tabs) {
    Write-Output ('TAB: ' + \$tab.id + ' | ' + \$tab.url + ' | ' + \$tab.title)
}
"
```

Find the tab matching your target URL and note its `id`.

### 4. Extract Content via CDP WebSocket

Use PowerShell WebSocket to connect to the tab's CDP endpoint and evaluate JavaScript:

```powershell
powershell.exe -Command "
\$tabId = '<TAB_ID>'
\$ws = New-Object System.Net.WebSockets.ClientWebSocket
\$ct = [System.Threading.CancellationToken]::None
\$uri = [Uri]\"ws://localhost:9222/devtools/page/\$tabId\"
\$ws.ConnectAsync(\$uri, \$ct).Wait()

\$cmd = '{\"id\":1,\"method\":\"Runtime.evaluate\",\"params\":{\"expression\":\"document.body.innerText\",\"returnByValue\":true}}'
\$bytes = [System.Text.Encoding]::UTF8.GetBytes(\$cmd)
\$seg = New-Object System.ArraySegment[byte] -ArgumentList @(,\$bytes)
\$ws.SendAsync(\$seg, [System.Net.WebSockets.WebSocketMessageType]::Text, \$true, \$ct).Wait()

\$ms = New-Object System.IO.MemoryStream
do {
    \$buf = New-Object byte[] 65536
    \$res = \$ws.ReceiveAsync((New-Object System.ArraySegment[byte] -ArgumentList @(,\$buf)), \$ct)
    \$res.Wait()
    \$ms.Write(\$buf, 0, \$res.Result.Count)
} while (-not \$res.Result.EndOfMessage)

\$rawBytes = \$ms.ToArray()
\$ms.Dispose()
[System.IO.File]::WriteAllBytes('C:\\Users\\<username>\\kimi_raw.json', \$rawBytes)
Write-Output ('Saved ' + \$rawBytes.Length + ' bytes')

\$ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, '', \$ct).Wait() 2>&1 | Out-Null
"
```

**Critical**: Save as raw bytes to a file. Do NOT try to parse JSON or decode unicode in PowerShell — its unicode handling is unreliable for large CDP responses.

### 5. Decode in WSL with Python

```python
import json

with open('/mnt/c/Users/<username>/kimi_raw.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

content = data['result']['result']['value']
print(f"Content length: {len(content)}")

with open('/mnt/c/Users/<username>/kimi_decoded.txt', 'w', encoding='utf-8') as f:
    f.write(content)
```

### 6. Process the Decoded Content

Use the decoded text file for further processing (split into articles, create markdown, etc.).

### 7. Close Chrome (Optional)

Stop only the debug Chrome instance started in Step 1 — never `Stop-Process -Name chrome`, which kills the user's normal Chrome sessions. Use the saved PID:

```powershell
# PID captured by Start-Process -PassThru in Step 1
$pid = Get-Content /tmp/chrome_debug_pid.txt
powershell.exe -Command "Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue"
```

If the PID was not captured, fall back to filtering by the dedicated `--user-data-dir`:

```powershell
powershell.exe -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { \$_.CommandLine -like '*chrome-debug*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"
```

## Pitfalls

- **Chrome singleton lock**: If Chrome is already running, `Start-Process` with a new `--user-data-dir` works. But if using the default profile, it just opens a new tab in the existing instance (which may not have `--remote-debugging-port`).
- **WSL networking barrier**: `localhost:9222` from WSL does NOT reach Windows Chrome. Always use PowerShell for CDP communication.
- **Large responses**: CDP WebSocket responses can be very large (60KB+). Use `MemoryStream` with chunked reads, not a single buffer.
- **PowerShell unicode**: Do NOT use PowerShell's `ConvertFrom-Json` or regex unicode decoding for CDP responses — they fail on large payloads. Save raw bytes and decode in Python.
- **SPA login walls**: Some SPAs (like kimi.com) require authentication. Even with CDP, if the page shows a login screen, the content won't be in `document.body.innerText`. Check for login redirects before extracting.
- **Port conflicts**: If port 9222 is already in use, Chrome will fail silently. Check with `Get-NetTCPConnection` first.

## Session Reference

- **2026-09-03**: First use — extracted kimi.com share page (SPA requiring login). Chrome launched via PowerShell, CDP WebSocket extracted 61KB JSON, Python decoded 11,404 chars of content. Split into 2 Obsidian articles.
