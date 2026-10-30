# Model-scope results

## Tested configuration

Qwen2.5-Coder-7B-Instruct (7.61B parameters), official Q4_K_M weights, 8,192
context tokens, 2,048 output budget, temperature 0.2, native thinking disabled.
Inference ran on a free Colab Tesla T4 with a pinned llama.cpp CUDA runtime.

| Task | Final observed outcome |
| --- | --- |
| Create a utility module | 8 acceptance tests passed |
| Repair two cooperating files | 6 acceptance tests passed |
| Answer a question across three files | Correct answer; source unchanged |
| Advanced expense tracker | Partial build: 13 checks passed, 18 failed |

Both successful coding results passed the same tests again on Windows. Protected
test files remained unchanged. The functional build passed 150 automated tests.

These are small single-trial fixtures, not a general capability score or a
promise of complete application generation. The earlier advanced build is still
classified as partial. Passing supplied tests does not prove all requirements.

## Interpretation

Model reasoning quality and orchestration quality are separate. Retaining source
evidence, supporting natural file tools, recovering from repeated actions and
synthesizing read-only answers improved the observed small-task workflow.

Failure guidance distinguishes connection problems, configured limits, incomplete
verification, tool stalls and failed implementation. It does not assign a global
reasoning score from parameter count or a single failed attempt.

## Limits

Kaggle live operation, broad framework performance and fresh-PC prerequisite
installation remain unverified. Hardware-fit estimates are not measured runtime
requirements or quality guarantees. New catalog downloads were checked using
controlled byte streams; no new catalog weights were downloaded to the test PC.
Native-thinking configuration was checked, but inference in that mode was not
benchmarked. A compatible model runtime is required separately.
