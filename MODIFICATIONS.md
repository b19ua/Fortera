# Modification notice

The upstream `Qwen/Qwen3-1.7B` model weights were modified for Fortera:
- supervised fine-tuning with the Fortera task dataset through a LoRA adapter;
- adapter merge into the base model weights;
- conversion to GGUF Q4_K_M for local edge inference.

This is a modified derivative model, not an unmodified copy of the upstream model. Upstream license and attribution notices are included with the model release at https://huggingface.co/Askit87/Fortera.
