from __future__ import annotations

import argparse
import asyncio
import os

from worker.client import WorkerClient
from worker.model_runtime import TransformersRuntime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start an AEGAEON disposable worker")
    parser.add_argument(
        "--controller",
        default="ws://127.0.0.1:8000/ws/worker",
        help="Controller worker WebSocket URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("AEGAEON_WORKER_TOKEN", "development-token"),
        help="Worker authentication token (or set AEGAEON_WORKER_TOKEN)",
    )
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--model-id", default=os.getenv("AEGAEON_MODEL_ID", ""))
    parser.add_argument("--model-runtime", choices=["transformers"], default="transformers")
    parser.add_argument("--quantization", choices=["4bit", "8bit", "bf16"], default="4bit")
    parser.add_argument("--minimum-vram-mb", type=int, default=3072)
    parser.add_argument("--max-context", type=int, default=32768)
    parser.add_argument(
        "--cuda-allocator-config",
        default=os.getenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.cuda_allocator_config:
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", args.cuda_allocator_config)
    model_runtime = None
    if args.model_id:
        model_runtime = TransformersRuntime(
            args.model_id,
            quantization=args.quantization,
            minimum_vram_mb=args.minimum_vram_mb,
            max_context=args.max_context,
            hf_token=os.getenv("HF_TOKEN", ""),
        )
    asyncio.run(
        WorkerClient(
            args.controller,
            args.token,
            args.worker_id,
            model_runtime=model_runtime,
        ).run_forever()
    )


if __name__ == "__main__":
    main()
