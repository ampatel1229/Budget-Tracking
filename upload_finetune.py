#!/usr/bin/env python3
"""Upload fine-tuning files and start a fine-tune job.

Usage:
  export OPENAI_API_KEY=...
  python upload_finetune.py \
    --train ./ft_train_policy_only.jsonl \
    --eval ./ft_eval_messages_clean.jsonl \
    --model gpt-4.1-mini

Notes:
- Fine-tuning availability depends on the base model and account access.
- If your selected model is not fine-tune-capable, switch to a supported model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from openai import OpenAI
from openai import BadRequestError


AUTO_MODEL_CANDIDATES: List[str] = [
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-3.5-turbo",
]


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def validate_jsonl(path: Path) -> None:
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                raise ValueError(f"{path} has an empty line at {i}.")
            obj = json.loads(line)
            if "messages" not in obj:
                raise ValueError(f"{path}:{i} missing 'messages' key.")
            messages = obj["messages"]
            if not isinstance(messages, list) or len(messages) < 2:
                raise ValueError(f"{path}:{i} must contain at least 2 messages.")
            for msg in messages:
                if "role" not in msg or "content" not in msg:
                    raise ValueError(f"{path}:{i} contains invalid message object.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload fine-tune files and create a fine-tune job.")
    parser.add_argument("--train", type=Path, required=True, help="Training JSONL path")
    parser.add_argument("--eval", type=Path, required=False, help="Validation/eval JSONL path")
    parser.add_argument(
        "--model",
        type=str,
        default="auto",
        help="Base model for fine-tuning (must be fine-tune-capable)",
    )
    parser.add_argument("--suffix", type=str, default="purdue-budget-privacy", help="Fine-tune model suffix")
    parser.add_argument("--dry-run", action="store_true", help="Validate files only; do not upload")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.train.exists():
        raise FileNotFoundError(f"Training file not found: {args.train}")
    if args.eval and not args.eval.exists():
        raise FileNotFoundError(f"Eval file not found: {args.eval}")

    validate_jsonl(args.train)
    if args.eval:
        validate_jsonl(args.eval)

    print(f"Training file: {args.train} ({count_lines(args.train)} rows)")
    if args.eval:
        print(f"Eval file: {args.eval} ({count_lines(args.eval)} rows)")

    if args.dry_run:
        print("Dry run complete. Files are valid.")
        return

    client = OpenAI()

    with args.train.open("rb") as f:
        train_file = client.files.create(file=f, purpose="fine-tune")
    print(f"Uploaded training file id: {train_file.id}")

    eval_file_id = None
    if args.eval:
        with args.eval.open("rb") as f:
            eval_file = client.files.create(file=f, purpose="fine-tune")
        eval_file_id = eval_file.id
        print(f"Uploaded eval file id: {eval_file_id}")

    models_to_try = AUTO_MODEL_CANDIDATES if args.model == "auto" else [args.model]

    last_err = None
    for model_name in models_to_try:
        job_kwargs = {
            "training_file": train_file.id,
            "model": model_name,
            "suffix": args.suffix,
        }
        if eval_file_id:
            job_kwargs["validation_file"] = eval_file_id

        try:
            job = client.fine_tuning.jobs.create(**job_kwargs)
            print(f"Created fine-tune job id: {job.id}")
            print(f"Base model used: {model_name}")
            print("Track with: client.fine_tuning.jobs.retrieve(job_id)")
            return
        except BadRequestError as err:
            last_err = err
            err_text = str(err).lower()
            if "not available for fine-tuning" in err_text or "model_not_available" in err_text:
                print(f"Model not available for fine-tuning on this account: {model_name}")
                continue
            raise

    tried = ", ".join(models_to_try)
    raise RuntimeError(
        f"No fine-tune-capable model worked. Tried: {tried}. "
        f"Last API error: {last_err}"
    )


if __name__ == "__main__":
    main()
