# STRUT-Unity

This folder connects the useful pieces from `STRUT-C` and `Unity` into one runnable prototype.

- `unity/` contains Unity's core C test framework files.
- `original_strut_c/` keeps the active STRUT-C scripts for reference.
- `strut_unity/` is the connector layer: libclang extracts function context, tree-sitter checks C syntax, deterministic seed cases are generated, Unity tests are emitted, compiled, and run.

Run the demo:

```sh
make demo
```

The generated context, cases, Unity test file, and executable are written under `build/`.

