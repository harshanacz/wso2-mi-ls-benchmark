# wso2-mi-ls-benchmark

Performance benchmark comparing two WSO2 MI Language Server implementations.

| Implementation | Stack |
|---|---|
| [Java LS](https://github.com/wso2/mi-language-server) | Java + LemMinX + Xerces-J |
| [TypeScript LS](https://github.com/harshanacz/wso2-mi-language-server-ts) | TypeScript + Node.js + Xerces-C++ → WASM |

## Results

| Metric | Java LS | TypeScript LS | Winner |
|---|---|---|---|
| Startup time | 252ms | 74ms | TS 3x faster |
| Cold validation | 111ms | 114ms | ~same |
| Warm validation (avg) | 537ms | 0.68ms | TS 788x faster |
| Memory (RSS) | 145MB | 128.9MB | TS 11% less |
| Error detection | 15ms | 17ms | ~same |

## Methodology

Both servers tested using identical Python LSP mock client.
Same machine, same XML, same LSP protocol messages.

## How to Run

### Prerequisites
- Python 3
- Java 21+
- Node.js 18+

### Setup
1. Build Java LS:
   cd mi-language-server
   ./mvnw clean verify -DskipTests
   cp org.eclipse.lemminx/target/mi-language-server-uber.jar compare/

2. Build TypeScript LS:
   cd wso2-mi-language-server-ts
   npm run bundle
   cp -r dist compare/

3. Run benchmark:
   cd compare
   python3 benchmark.py

## What the Benchmark Measures

- **Startup time** — process spawn to initialize response (3 runs avg)
- **Cold validation** — didOpen to publishDiagnostics (first file open)
- **Warm validation** — didChange to publishDiagnostics (10 edits avg)
- **Memory** — process RSS after warm validation loop
- **Error detection** — time to catch invalid XML element

## Key Finding

Warm validation is where the implementations differ most.

Java LS uses a 500ms debounce — it waits until the user 
stops typing before running validation.

TypeScript LS validates every keystroke at 0.68ms using 
Xerces-C++ compiled to WebAssembly with XMLGrammarPool 
caching. No debounce needed.

## Files

benchmark.py          ← Python LSP mock client (runs both servers)
dist/                 ← TypeScript LS bundle
mi-language-server-uber.jar ← Java LS jar