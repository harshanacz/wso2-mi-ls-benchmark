import subprocess
import time
import json
import os

COMPARE_DIR = os.path.dirname(os.path.abspath(__file__))
JAVA_JAR    = os.path.join(COMPARE_DIR, "mi-language-server-uber.jar")
TS_SERVER   = os.path.join(COMPARE_DIR, "dist", "server.js")

# ── LSP helpers ───────────────────────────────────────────────────────────────

def make_message(content):
    body = json.dumps(content)
    return f"Content-Length: {len(body)}\r\n\r\n{body}".encode()

def read_message(proc, timeout=15):
    deadline = time.time() + timeout
    headers = b""
    while b"\r\n\r\n" not in headers:
        if time.time() > deadline:
            raise TimeoutError("Timeout reading headers")
        ch = proc.stdout.read(1)
        if not ch:
            raise EOFError("Server closed stdout")
        headers += ch
    content_length = 0
    for line in headers.decode().split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":")[1].strip())
    body = proc.stdout.read(content_length)
    return json.loads(body.decode())

def wait_for_diagnostics(proc, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = read_message(proc, timeout=timeout)
        method = msg.get("method", "")
        print(f"      [recv] {method or 'response id='+str(msg.get('id','?'))}")
        
        # Handle workspace/configuration request
        if method == "workspace/configuration":
            # respond with empty config
            response = make_message({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": [{}]
            })
            proc.stdin.write(response)
            proc.stdin.flush()
            print(f"      [sent] workspace/configuration response")
            continue
        
        # Handle client/registerCapability
        if method == "client/registerCapability":
            response = make_message({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": None
            })
            proc.stdin.write(response)
            proc.stdin.flush()
            print(f"      [sent] client/registerCapability response")
            continue
        
        if method == "textDocument/publishDiagnostics":
            return msg["params"]["diagnostics"]
    
    raise TimeoutError("No diagnostics received")

def get_rss(pid):
    try:
        kb = int(subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(pid)]
        ).decode().strip())
        return kb / 1024
    except Exception:
        return -1

# ── XML samples ───────────────────────────────────────────────────────────────

JAVA_VALID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<proxy xmlns="http://ws.apache.org/ns/synapse"
       name="TestProxy"
       transports="http https"
       startOnLoad="true">
  <target>
    <inSequence>
      <log level="full"/>
      <respond/>
    </inSequence>
  </target>
</proxy>"""

JAVA_INVALID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<endpoint xmlns="http://ws.apache.org/ns/synapse"
          name="BadEndpoint">
  <http method="get"/>
</endpoint>"""

JAVA_VALID_URI   = "file:///tmp/test/src/main/wso2mi/artifacts/proxy-services/TestProxy.xml"
JAVA_INVALID_URI = "file:///tmp/test/src/main/wso2mi/artifacts/endpoints/BadEndpoint.xml"

TS_VALID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://ws.apache.org/ns/synapse">
  <api name="TestAPI" context="/test">
    <resource methods="GET" uri-template="/items">
      <inSequence>
        <log level="full"/>
        <respond/>
      </inSequence>
    </resource>
  </api>
</definitions>"""

TS_INVALID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://ws.apache.org/ns/synapse">
  <invalidElement name="demo"/>
</definitions>"""

TS_VALID_URI   = "file:///tmp/test/valid.xml"
TS_INVALID_URI = "file:///tmp/test/invalid.xml"

# ── Benchmark function ────────────────────────────────────────────────────────

def benchmark(name, cmd, init_params,
              valid_xml, invalid_xml,
              valid_uri, invalid_uri):

    print(f"\n{'='*62}")
    print(f"  {name}")
    print(f"{'='*62}")

    # 1. Startup
    print("\n[1] Startup (3 runs)...")
    startup_times = []
    for i in range(3):
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        proc.stdin.write(make_message({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize",
            "params": init_params
        }))
        proc.stdin.flush()
        read_message(proc)
        t1 = time.perf_counter()
        startup_times.append((t1 - t0) * 1000)
        proc.terminate()
        proc.wait()
        time.sleep(0.3)

    avg_startup = sum(startup_times) / len(startup_times)
    print(f"    Runs:    {[f'{t:.1f}ms' for t in startup_times]}")
    print(f"    Average: {avg_startup:.2f}ms")

    # Fresh server for remaining tests
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    proc.stdin.write(make_message({
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": init_params
    }))
    proc.stdin.flush()
    read_message(proc)

    proc.stdin.write(make_message({
        "jsonrpc": "2.0",
        "method": "initialized",
        "params": {}
    }))
    proc.stdin.flush()

    # 2. Cold validation
    print("\n[2] Cold validation...")
    t0 = time.perf_counter()
    proc.stdin.write(make_message({
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": valid_uri,
                "languageId": "xml",
                "version": 1,
                "text": valid_xml
            }
        }
    }))
    proc.stdin.flush()
    wait_for_diagnostics(proc)
    t1 = time.perf_counter()
    cold_time = (t1 - t0) * 1000
    print(f"    Cold: {cold_time:.2f}ms")

    # 3. Warm validation
    print("\n[3] Warm validation (10 edits)...")
    warm_times = []
    for i in range(10):
        edited = valid_xml.replace(
            "<log level=\"full\"/>",
            f"<log level=\"full\"/><!-- edit {i} -->"
        )
        t0 = time.perf_counter()
        proc.stdin.write(make_message({
            "jsonrpc": "2.0",
            "method": "textDocument/didChange",
            "params": {
                "textDocument": {
                    "uri": valid_uri,
                    "version": i + 2
                },
                "contentChanges": [{"text": edited}]
            }
        }))
        proc.stdin.flush()
        wait_for_diagnostics(proc)
        t1 = time.perf_counter()
        warm_times.append((t1 - t0) * 1000)

    avg_warm = sum(warm_times) / len(warm_times)
    min_warm = min(warm_times)
    max_warm = max(warm_times)
    print(f"    Times:   {[f'{t:.1f}ms' for t in warm_times]}")
    print(f"    avg={avg_warm:.2f}ms  min={min_warm:.2f}ms  max={max_warm:.2f}ms")

    # 4. Memory
    print("\n[4] Memory...")
    rss_mb = get_rss(proc.pid)
    print(f"    RSS: {rss_mb:.1f}MB")

    # 5. Error detection
    print("\n[5] Error detection...")
    t0 = time.perf_counter()
    proc.stdin.write(make_message({
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": invalid_uri,
                "languageId": "xml",
                "version": 1,
                "text": invalid_xml
            }
        }
    }))
    proc.stdin.flush()
    diags = wait_for_diagnostics(proc)
    t1 = time.perf_counter()
    error_time = (t1 - t0) * 1000
    print(f"    Detection: {error_time:.2f}ms")
    print(f"    Errors:    {len(diags)}")
    for d in diags[:3]:
        sev = {1:"ERROR",2:"WARN",3:"INFO",4:"HINT"}.get(d.get("severity"),str(d.get("severity","")))
        print(f"      [{sev}] {d.get('message','')[:70]}")

    proc.terminate()
    proc.wait()

    return {
        "name": name,
        "startup": avg_startup,
        "cold": cold_time,
        "warm_avg": avg_warm,
        "warm_min": min_warm,
        "warm_max": max_warm,
        "rss": rss_mb,
        "error_time": error_time,
        "error_count": len(diags)
    }

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    results = []

    # Java LS
    java_result = benchmark(
        name="Java MI Language Server",
        cmd=[
            "java",
            "-Xms64m", "-Xmx512m",
            "-DwatchParentProcess=false",
            "-jar", JAVA_JAR
        ],
        init_params={
            "processId": os.getpid(),
            "rootPath": "/tmp/test",
            "rootUri": "file:///tmp/test",
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {
                        "relatedInformation": True
                    }
                }
            },
            "initializationOptions": {
                "settings": {
                    "xml": {
                        "validation": {
                            "enabled": True
                        }
                    }
                }
            }
        },
        valid_xml=JAVA_VALID_XML,
        invalid_xml=JAVA_INVALID_XML,
        valid_uri=JAVA_VALID_URI,
        invalid_uri=JAVA_INVALID_URI
    )
    results.append(java_result)

    # TypeScript LS
    ts_result = benchmark(
        name="TypeScript MI Language Server",
        cmd=["node", TS_SERVER, "--stdio"],
        init_params={
            "processId": os.getpid(),
            "rootUri": "file:///tmp/test",
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {
                        "relatedInformation": True
                    }
                },
                "workspace": {
                    "workspaceFolders": True
                }
            },
            "workspaceFolders": [
                {
                    "uri": "file:///tmp/test",
                    "name": "test"
                }
            ],
            "initializationOptions": {
                "schemas": [
                    {
                        "pattern": "**/*.xml",
                        "schema": "440"
                    }
                ]
            }
        },
        valid_xml=TS_VALID_XML,
        invalid_xml=TS_INVALID_XML,
        valid_uri=TS_VALID_URI,
        invalid_uri=TS_INVALID_URI
    )
    results.append(ts_result)

    # ── Comparison table ──────────────────────────────────────────
    java = results[0]
    ts   = results[1]

    def winner(j, t):
        return "Java ✅" if j < t else "TS ✅"

    def speedup(j, t):
        if t == 0: return "∞x"
        r = j / t
        if r >= 1: return f"TS {r:.0f}x faster"
        return f"Java {1/r:.0f}x faster"

    print(f"\n\n{'='*72}")
    print(f"  FINAL COMPARISON — Same methodology, same Python script")
    print(f"{'='*72}")
    print(f"{'Metric':<28} {'Java LS':>10} {'TypeScript LS':>14} {'Winner':>10} {'Speedup':>12}")
    print(f"{'-'*72}")
    print(f"{'Startup time':<28} {java['startup']:>8.1f}ms {ts['startup']:>12.1f}ms {winner(java['startup'],ts['startup']):>10} {speedup(java['startup'],ts['startup']):>12}")
    print(f"{'Cold validation':<28} {java['cold']:>8.1f}ms {ts['cold']:>12.1f}ms {winner(java['cold'],ts['cold']):>10} {speedup(java['cold'],ts['cold']):>12}")
    print(f"{'Warm validation (avg)':<28} {java['warm_avg']:>8.1f}ms {ts['warm_avg']:>12.1f}ms {winner(java['warm_avg'],ts['warm_avg']):>10} {speedup(java['warm_avg'],ts['warm_avg']):>12}")
    print(f"{'Warm validation (min)':<28} {java['warm_min']:>8.1f}ms {ts['warm_min']:>12.1f}ms {winner(java['warm_min'],ts['warm_min']):>10} {speedup(java['warm_min'],ts['warm_min']):>12}")
    print(f"{'Warm validation (max)':<28} {java['warm_max']:>8.1f}ms {ts['warm_max']:>12.1f}ms {winner(java['warm_max'],ts['warm_max']):>10} {speedup(java['warm_max'],ts['warm_max']):>12}")
    print(f"{'Process RSS':<28} {java['rss']:>8.1f}MB {ts['rss']:>12.1f}MB {winner(java['rss'],ts['rss']):>10}")
    print(f"{'Error detection':<28} {java['error_time']:>8.1f}ms {ts['error_time']:>12.1f}ms {winner(java['error_time'],ts['error_time']):>10} {speedup(java['error_time'],ts['error_time']):>12}")
    print(f"{'Errors caught':<28} {java['error_count']:>9} {ts['error_count']:>13}")
    print(f"{'-'*72}")