# Runtime setup

1. Download `Fortera-Q4_K_M.gguf` and the tokenizer files from https://huggingface.co/Askit87/Fortera.
2. Put the GGUF at the repository root or set `FORTERA_MODEL_PATH` to its location.
3. Put the tokenizer files in `./tokenizer/` or set `FORTERA_TOKENIZER_PATH` to their directory.
4. Install `llama.cpp` with `llama-server` available on PATH.
5. Install Python dependencies with `python -m pip install -r requirements.txt`.
6. Start `bash scripts/run_fortera.sh`.
7. Run `python -m fortera_runtime.client "Hold position" --mode simulator --language en`.

The server binds to `127.0.0.1:8081`. The client loads the local tokenizer and calls llama.cpp's raw `/completion` endpoint. The helper emits validated proposals only; physical dispatch is disabled.
