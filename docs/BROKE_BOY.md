# Broke Boy operations

Broke Boy mode runs planning deterministically on the controller and all generative inference on disposable workers. The controller never silently falls back to its paid provider for a Broke Boy project.

## Launch flow

1. Create a project with `execution_mode: broke_boy`.
2. Accept the evidence-backed model recommendation or select a known catalog model.
3. Generate one notebook per requested worker from the project launchpad.
4. Make the controller URL reachable from Colab. HTTPS is recommended outside localhost.
5. Download each notebook, choose a GPU runtime, run all cells, and enter its one-time pairing code.
6. Wait for the exact model/runtime/quantization to report `loaded: true` and `status: ready`.
7. Start the build.

Every worker hosts one complete model. Cross-worker tensor or layer sharding is intentionally outside this milestone. Independent task dispatch can use multiple compatible workers, while the initial deterministic DAG keeps implementation and tests sequential to preserve repository contracts.

## Pairing and secrets

Notebook artifacts contain the controller URL, model ID, runtime configuration, and code that prompts for credentials. They do not embed the shared worker token, pairing code, redeemed credential, or Hugging Face token.

Pairing codes are random, hashed at rest, single-use, and expire after `AEGAEON_PAIRING_CODE_LIFETIME_SECONDS` (30 minutes by default). Redemption returns a 12-hour credential bound to the issued worker ID and model. The WebSocket controller rejects a paired worker that registers a different identity or model.

Set `HF_TOKEN` on the controller only to enable authenticated Model Scout discovery. A notebook asks for its own Hugging Face token interactively when a model requires one; the value remains in the notebook runtime.

## Worker state and recovery

Workers advertise hardware, CUDA, runtime, quantization, model load state, context length, task scores, and safe error text. The scheduler applies hard capability, RAM, GPU, VRAM, model, runtime, and quantization filters before ranking idle capacity.

Jobs use durable leases. A disconnect interrupts the active lease and requeues bounded work. Results are accepted only from the worker that owns both the in-memory assignment and durable lease; stale, duplicate, and foreign results are rejected and recorded as security events.

## Typed model jobs

`model.generate` and `model.repair` carry validated context and constraints. Worker results are structured source bundles with repository-relative paths. Hidden control paths, `.git`, absolute paths, traversal, more than 200 files, and bundles above 2 MB are rejected before canonical integration.

The controller alone writes the canonical Git repository, runs allowlisted verification commands, and approves final review from objective evidence.

## Troubleshooting

- **No compatible model worker:** confirm the exact model ID, `transformers` runtime, quantization, loaded/ready status, RAM, and VRAM.
- **Colab cannot pair:** the controller URL must be reachable from the notebook; localhost refers to Colab itself.
- **VRAM check fails:** generate a notebook for a smaller model or use stronger quantization. The notebook checks before downloading weights.
- **Pairing code expired/used:** generate a fresh notebook; codes cannot be reused.
- **Model returns invalid JSON:** the worker rejects it without integrating files. Retry or choose a model with a stronger structured-output score.