# Model provider runtime

AEGAEON can run its planner, coder, failure reviewer, repair coder, and final reviewer through one OpenAI-compatible chat-completions endpoint. Model calls happen only on the controller. API keys are never sent to workers or returned by the REST API.

## Enable model mode

Copy `.env.example` to `.env`, then configure:

```dotenv
AEGAEON_DEMO_MODE=false
AEGAEON_LLM_BASE_URL=http://localhost:11434/v1
AEGAEON_LLM_API_KEY=
AEGAEON_LLM_MODEL=your-code-model
```

Restart the controller after changing `.env`. Open **Settings** in the web UI and use **Check connection**. The controller probes `GET /models`; orchestration uses `POST /chat/completions`.

Model mode is explicit. If the endpoint is unavailable, a project fails with an observable provider error instead of silently switching to the deterministic demo agent.

## Structured output

The planner and agent results are validated with strict Pydantic models. AEGAEON tries provider response formats according to `AEGAEON_LLM_STRUCTURED_OUTPUT_MODE`:

- `auto`: strict JSON Schema, then JSON object, then prompt-constrained JSON.
- `json_schema`: require strict structured-output support.
- `json_object`: require JSON mode.
- `prompt`: work with a basic compatible endpoint using schema instructions and local validation.

Invalid output receives a bounded corrective retry. Configure the limits with:

```dotenv
AEGAEON_LLM_TIMEOUT_SECONDS=180
AEGAEON_LLM_MAX_OUTPUT_TOKENS=16384
AEGAEON_LLM_STRUCTURED_OUTPUT_MODE=auto
AEGAEON_LLM_OUTPUT_RETRIES=2
AEGAEON_LLM_CONTEXT_CHARACTERS=60000
```

## Execution boundary

The controller retrieves bounded, focused repository context and asks the coder for complete UTF-8 file contents. Generated paths reject traversal, hidden control files, `.git`, and binary/cache output. A worker may only materialize the typed source bundle in a disposable directory; it cannot execute a model-supplied shell command.

After Git integration, AEGAEON detects repository-native pytest or installed Node test/build commands. It does not automatically install dependencies from generated projects. Failed verification is passed to a structured reviewer and repair coder, then re-run up to the project retry limit.

The development runner is path-confined, allowlisted, timeout-bound, and output-limited, but it is not a hardened sandbox. Use isolated worker hosts for untrusted workloads.
