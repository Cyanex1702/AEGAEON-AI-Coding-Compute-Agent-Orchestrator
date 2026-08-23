# Model Scout

Model Scout recommends open coding models from normalized factual evidence. It separates discovery and evidence storage from compatibility filtering and scoring.

## Evidence

The initial catalog contains official-card evidence for:

- `Qwen/Qwen2.5-Coder-1.5B-Instruct`
- `deepseek-ai/deepseek-coder-1.3b-instruct`
- `Qwen/Qwen2.5-Coder-7B-Instruct`

Records include publisher, family, parameter count where known, instruction tuning, context length, runtimes, supported quantizations, license, gating, safe VRAM estimates, benchmark evidence, structured-output score, languages, source URL, and provenance. Hugging Face discovery imports only normalized metadata it can support; incomplete discovered records are not made recommendable by invented estimates.

## Recommendation rules

Hard filters run before scoring:

- only loaded runtime and requested quantization support;
- model VRAM estimate must fit within 85% of reported VRAM (15% headroom);
- minimum context and instruction-tuning requirements;
- public/gating constraint.

Compatible candidates receive a transparent weighted score across coding quality, hardware fit, structured-output reliability, context, runtime support, and license. The API returns the score breakdown, confidence, reasons, warnings, recommended quantization, and estimated VRAM.

`POST /model-scout/recommend` is deterministic for the same evidence and request. `POST /model-scout/discover?query=...` queries the official Hugging Face API and records provenance; it never exposes `HF_TOKEN`.

## Manual research

`POST /projects/{id}/research/prompt` creates a bounded prompt containing only the candidate evidence AEGAEON knows. Paste that prompt into an external advisor, then import its exact JSON through `/projects/{id}/research/import`.

The importer validates candidate IDs and flags VRAM claims that conflict materially with stored estimates. Imported advice is preserved as research evidence; it does not silently overwrite factual catalog data or the project model selection.