# STRUT-Unity

STRUT-Unity connects STRUT-style C test-case generation with the Unity C unit-test framework.

Given a C source file and one target function, it can:

- extract function context with `libclang` and `tree-sitter-c`;
- generate deterministic seed cases, LLM cases, or a hybrid of both;
- compute expected return values by compiling a small oracle program;
- emit Unity test code;
- compile and run the generated test binary;
- report estimated branch coverage and `gcov` coverage when available.

Generated files are written under `build/`. The directory is disposable.

## Repository Layout

- `strut_unity/`: Python connector package.
  - `__main__.py`: entry point for `python3 -m strut_unity`.
  - `pipeline.py`: main orchestration flow.
  - `analyzer.py`: C function analysis, type information, dependencies, globals, and branch conditions.
  - `cases.py`: test-case data model, deterministic seed cases, and STRUT JSON conversion helpers.
  - `llm_client.py`: OpenAI-compatible chat-completions client.
  - `llm_cases.py`: LLM response parsing and conversion into internal test cases.
  - `prompts.py`: loads prompt files and renders LLM messages.
  - `oracle.py`: compiles/runs oracle programs to fill expected outputs.
  - `source_rewriter.py`: rewrites `main` and stubbed callee names for test builds.
  - `stubs.py`: generates C stubs from structured `stubins`.
  - `unity_writer.py`: writes Unity C test files.
  - `coverage.py`: estimates branch outcomes and collects `gcov` data.
- `unity/`: Unity C test framework source files.
- `_dataset/data_structures/`: bundled C data-structure corpus for batch runs.
- `run_dataset.py`: batch runner over `_dataset/data_structures`.
- `Makefile`: convenience commands for demos and cleanup.
- Prompt resources used by the pipeline:
  - `Test Cases Generation Prompts.md`
  - `json structure.md`
  - `Test Suite Optimization Prompts Used By STRUT.md`
- `Prompts Used by LLM baseline method.md`: baseline-comparison material, not used by the current pipeline.

## Requirements

Install the Python and native tools used by the pipeline:

```sh
pip install clang tree-sitter tree-sitter-c
```

The runtime also expects:

- `clang` for normal test and oracle compilation;
- `gcc` and `gcov` for coverage collection;
- an OpenAI-compatible LLM endpoint for `llm` or `hybrid` mode.

`rules` mode does not call an LLM.

## Single-Function Usage

Run the deterministic rule generator only:

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source rules \
  --no-optimize
```

Run with the default hybrid mode:

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray
```

`--case-source` supports:

- `rules`: deterministic seed cases only.
- `llm`: LLM-generated cases only.
- `hybrid`: merge deterministic seed cases with LLM-generated cases. This is the default.

LLM and hybrid modes run an optimization pass by default when estimated branch outcomes are still uncovered after the first run. Use `--no-optimize` to skip that pass.

Each run prints a JSON summary and writes artifacts under `build/`, including:

- `<function>_context.json`
- `<function>_cases.json`
- `test_<function>.c`
- `test_<function>`
- optional LLM prompt/response traces
- optional `coverage_<function>_<stage>/`

## Local LLM

The default local endpoint is Ollama-compatible:

```sh
export STRUT_LLM_BASE_URL=http://127.0.0.1:11434/v1
export STRUT_LLM_MODEL=qwen3.5:latest
```

Then run a single function with local LLM generation:

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source hybrid
```

You can also pass the model directly:

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source llm \
  --llm-base-url http://127.0.0.1:11434/v1 \
  --llm-model qwen3.5:latest
```

If `STRUT_LLM_MODEL` is not set for a local URL, the client tries `ollama list` and prefers an installed `qwen3.5` model; otherwise it falls back to `qwen3.5:latest`.

## Online LLM

Any OpenAI-compatible chat-completions API can be used:

```sh
export STRUT_LLM_BASE_URL=https://api.openai.com/v1
export STRUT_LLM_API_KEY=...
export STRUT_LLM_MODEL=...
```

Run one function:

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source hybrid
```

The same values can be passed as flags:

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source llm \
  --llm-base-url https://api.openai.com/v1 \
  --llm-model YOUR_MODEL \
  --llm-api-key YOUR_API_KEY
```

## Batch Dataset Usage

`run_dataset.py` scans `_dataset/data_structures`, skips test drivers and `main.c`, discovers function definitions, and runs `python3 -m strut_unity` for each target.

Run a small rules-only smoke test:

```sh
python3 run_dataset.py \
  --case-source rules \
  --limit 5 \
  --no-optimize
```

Run the dataset with a local LLM:

```sh
export STRUT_LLM_BASE_URL=http://127.0.0.1:11434/v1
export STRUT_LLM_MODEL=qwen3.5:latest

python3 run_dataset.py \
  --case-source hybrid \
  --llm-model qwen3.5:latest \
  --timeout 300
```

Run the dataset with an online OpenAI-compatible API:

```sh
export STRUT_LLM_BASE_URL=https://api.openai.com/v1
export STRUT_LLM_API_KEY=...
export STRUT_LLM_MODEL=...

python3 run_dataset.py \
  --case-source hybrid \
  --llm-model "$STRUT_LLM_MODEL" \
  --timeout 300
```

Useful batch options:

- `--case-source rules|llm|hybrid`: choose generation mode.
- `--limit N`: run only the first `N` discovered targets.
- `--timeout SECONDS`: per-function timeout.
- `--include-main`: include `main` functions.
- `--no-optimize`: skip the LLM optimization pass.

Batch artifacts are moved into `build/dataset_results/<relative-source>/<source-stem>/<function>/`.

## Convenience Commands

```sh
make rules-demo
make llm-demo
make hybrid-demo
make clean
```

The demo targets currently reference `examples/classify_score.c`. If that file is not present in your checkout, use the direct commands above against `_dataset/data_structures/...` instead.

## Notes

- Prompt files are loaded at runtime by `strut_unity/prompts.py`; they are not generated artifacts.
- The LLM is asked for structured test inputs. Expected return values are recomputed locally by the oracle step before Unity assertions are written.
- Supported generated inputs include scalar basic types, arrays by element zero, pointers with local targets or `NULL` when a null branch is visible, and struct pointers with bounded field expansion.
- `build/`, `__pycache__/`, `.pyc`, `.gcda`, `.gcno`, and `.gcov` files are disposable intermediate outputs.

# 中文说明

STRUT-Unity 将 STRUT 风格的 C 语言测试用例生成流程和 Unity C 单元测试框架连接在一起。

给定一个 C 源文件和一个目标函数后，它可以：

- 使用 `libclang` 和 `tree-sitter-c` 提取函数上下文；
- 生成确定性种子用例、LLM 用例，或二者混合后的用例；
- 通过编译一个小型 oracle 程序计算期望返回值；
- 生成 Unity 测试代码；
- 编译并运行生成的测试二进制文件；
- 在可用时输出估算分支覆盖率和 `gcov` 覆盖率。

生成文件会写入 `build/`。该目录是可删除的中间产物目录。

## 仓库结构

- `strut_unity/`：Python 连接层包。
  - `__main__.py`：`python3 -m strut_unity` 的入口。
  - `pipeline.py`：主流程调度。
  - `analyzer.py`：分析 C 函数、类型信息、依赖、全局变量和分支条件。
  - `cases.py`：测试用例数据模型、确定性种子用例生成、STRUT JSON 转换辅助函数。
  - `llm_client.py`：OpenAI-compatible chat-completions 客户端。
  - `llm_cases.py`：解析 LLM 响应并转换为内部测试用例。
  - `prompts.py`：加载 prompt 文件并渲染 LLM 消息。
  - `oracle.py`：编译并运行 oracle 程序，用于回填 expected outputs。
  - `source_rewriter.py`：为测试构建重写 `main` 和被 stub 的被调函数名。
  - `stubs.py`：根据结构化 `stubins` 生成 C stub。
  - `unity_writer.py`：生成 Unity C 测试文件。
  - `coverage.py`：估算分支结果覆盖率并收集 `gcov` 数据。
- `unity/`：Unity C 测试框架源码。
- `_dataset/data_structures/`：随仓库提供的 C 数据结构语料，用于批量运行。
- `run_dataset.py`：针对 `_dataset/data_structures` 的批量运行脚本。
- `Makefile`：演示和清理用的快捷命令。
- 当前流程会读取的 prompt 资源：
  - `Test Cases Generation Prompts.md`
  - `json structure.md`
  - `Test Suite Optimization Prompts Used By STRUT.md`
- `Prompts Used by LLM baseline method.md`：基线方法对比材料，当前 pipeline 不使用。

## 环境要求

安装 Python 依赖：

```sh
pip install clang tree-sitter tree-sitter-c
```

运行时还需要：

- `clang`：用于普通测试和 oracle 编译；
- `gcc` 和 `gcov`：用于覆盖率收集；
- OpenAI-compatible LLM endpoint：用于 `llm` 或 `hybrid` 模式。

`rules` 模式不会调用大模型。

## 单函数用法

只运行确定性规则生成器：

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source rules \
  --no-optimize
```

使用默认的 hybrid 模式：

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray
```

`--case-source` 支持：

- `rules`：只使用确定性种子用例。
- `llm`：只使用 LLM 生成的用例。
- `hybrid`：合并确定性种子用例和 LLM 生成用例。这是默认模式。

`llm` 和 `hybrid` 模式下，如果第一次运行后估算分支结果仍有未覆盖项，默认会运行一次优化生成 pass。使用 `--no-optimize` 可以跳过该 pass。

每次运行会打印 JSON 摘要，并在 `build/` 下写入产物，包括：

- `<function>_context.json`
- `<function>_cases.json`
- `test_<function>.c`
- `test_<function>`
- 可选的 LLM prompt/response trace
- 可选的 `coverage_<function>_<stage>/`

## 使用本地大模型

默认本地 endpoint 兼容 Ollama：

```sh
export STRUT_LLM_BASE_URL=http://127.0.0.1:11434/v1
export STRUT_LLM_MODEL=qwen3.5:latest
```

使用本地 LLM 处理单个函数：

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source hybrid
```

也可以直接通过参数指定模型：

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source llm \
  --llm-base-url http://127.0.0.1:11434/v1 \
  --llm-model qwen3.5:latest
```

如果本地 URL 下没有设置 `STRUT_LLM_MODEL`，客户端会尝试执行 `ollama list`，优先选择已安装的 `qwen3.5` 模型；否则回退到 `qwen3.5:latest`。

## 使用在线大模型

可以使用任意 OpenAI-compatible chat-completions API：

```sh
export STRUT_LLM_BASE_URL=https://api.openai.com/v1
export STRUT_LLM_API_KEY=...
export STRUT_LLM_MODEL=...
```

处理单个函数：

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source hybrid
```

也可以通过命令行参数传入相同配置：

```sh
python3 -m strut_unity _dataset/data_structures/array/carray.c \
  --function insertValueCArray \
  --case-source llm \
  --llm-base-url https://api.openai.com/v1 \
  --llm-model YOUR_MODEL \
  --llm-api-key YOUR_API_KEY
```

## 批量数据处理

`run_dataset.py` 会扫描 `_dataset/data_structures`，跳过测试驱动文件和 `main.c`，发现函数定义，并对每个目标函数运行 `python3 -m strut_unity`。

运行一个小规模 rules-only 冒烟测试：

```sh
python3 run_dataset.py \
  --case-source rules \
  --limit 5 \
  --no-optimize
```

使用本地 LLM 批量处理数据集：

```sh
export STRUT_LLM_BASE_URL=http://127.0.0.1:11434/v1
export STRUT_LLM_MODEL=qwen3.5:latest

python3 run_dataset.py \
  --case-source hybrid \
  --llm-model qwen3.5:latest \
  --timeout 300
```

使用在线 OpenAI-compatible API 批量处理数据集：

```sh
export STRUT_LLM_BASE_URL=https://api.openai.com/v1
export STRUT_LLM_API_KEY=...
export STRUT_LLM_MODEL=...

python3 run_dataset.py \
  --case-source hybrid \
  --llm-model "$STRUT_LLM_MODEL" \
  --timeout 300
```

常用批量参数：

- `--case-source rules|llm|hybrid`：选择用例生成模式。
- `--limit N`：只运行前 `N` 个发现的目标函数。
- `--timeout SECONDS`：设置每个函数的超时时间。
- `--include-main`：包含 `main` 函数。
- `--no-optimize`：跳过 LLM 优化 pass。

批量产物会移动到 `build/dataset_results/<relative-source>/<source-stem>/<function>/`。

## 快捷命令

```sh
make rules-demo
make llm-demo
make hybrid-demo
make clean
```

这些 demo target 当前引用 `examples/classify_score.c`。如果你的 checkout 中没有这个文件，请改用上文针对 `_dataset/data_structures/...` 的直接命令。

## 说明

- Prompt 文件由 `strut_unity/prompts.py` 在运行时读取；它们不是生成产物。
- LLM 只需要生成结构化测试输入。期望返回值会在本地通过 oracle 步骤重新计算，然后再写入 Unity 断言。
- 当前支持的生成输入包括基础标量类型、使用第 0 个元素初始化的数组、带本地目标或可见空指针分支时的 `NULL` 指针，以及有限深度展开字段的结构体指针。
- `build/`、`__pycache__/`、`.pyc`、`.gcda`、`.gcno` 和 `.gcov` 都是可删除的中间产物。
