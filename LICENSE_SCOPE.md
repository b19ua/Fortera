# Fortera license scope and redistribution audit

Audit date: 2026-11-24.

## Finding
The exact upstream Hugging Face repository `Qwen/Qwen3-1.7B` identifies its license as Apache-2.0 and contains a LICENSE file. The file was retrieved from revision `70d244c`, the verified commit shown in the repository file listing. Its text is Apache License, Version 2.0.

## Derivative model
Apache-2.0 permits commercial use, modification, derivative works and redistribution, subject to its conditions. Fine-tuning Qwen3-1.7B, merging the Fortera adapter, converting to GGUF and distributing the derived weights are permitted under the upstream copyright license if the conditions are met.

Redistribution requirements include: provide a copy of Apache-2.0; mark modified files prominently; retain applicable upstream notices; reproduce applicable upstream NOTICE attributions if supplied; and do not imply that the license grants upstream trademark rights. The license includes a patent-license termination clause for certain patent litigation and provides the work AS-IS without warranties.

## Scope
This is a repository-level audit of the identified base model license. It is not a legal opinion and does not establish rights to third-party training data, independently added code, datasets or assets. Fortera-created training examples and runtime code need their own provenance and license records if third parties contributed them. Recheck if the base model revision changes.

Full upstream license text is included with the model release: https://huggingface.co/Askit87/Fortera/blob/main/LICENSE
