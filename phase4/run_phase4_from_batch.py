#!/usr/bin/env python3
"""Daemon entrypoint: convert a verified ledger batch into Phase 4 training."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from phase4.common import DEFAULT_ATTACK_IMAGE_ROOT, DEFAULT_OUTPUT_ROOT, save_json, sha256_file, write_jsonl  # noqa: E402


PREFERRED_FIELDS = [
    "id",
    "jailbreak_query",
    "redteam_query",
    "format",
    "policy",
    "image_path",
    "from",
    "selected_mini",
    "transfer_from_llm",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", default=os.environ.get("BATCH_DIR", ""))
    parser.add_argument("--manifest-path", default=os.environ.get("MANIFEST_PATH", ""))
    parser.add_argument("--version", default=os.environ.get("VERSION", "manual"))
    parser.add_argument("--attack-image-root", default=os.environ.get("ATTACK_IMAGE_ROOT", str(DEFAULT_ATTACK_IMAGE_ROOT)))
    parser.add_argument("--model-name", default=os.environ.get("PHASE4_MODEL_NAME", "deepseek-ai/deepseek-vl-7b-chat"))
    parser.add_argument("--deepseek-vl-path", default=os.environ.get("DEEPSEEK_VL_PATH", ""))
    parser.add_argument("--previous-adapter", default=os.environ.get("PHASE4_PREVIOUS_ADAPTER", ""))
    parser.add_argument("--output-root", default=os.environ.get("PHASE4_OUTPUT_ROOT", str(DEFAULT_OUTPUT_ROOT)))
    parser.add_argument("--max-attacks", type=int, default=int(os.environ.get("PHASE4_MAX_ATTACKS", "0") or "0"))
    parser.add_argument("--epochs", type=int, default=int(os.environ.get("PHASE4_EPOCHS", "1") or "1"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("PHASE4_BATCH_SIZE", "1") or "1"))
    parser.add_argument(
        "--grad-accum-steps",
        type=int,
        default=int(os.environ.get("PHASE4_GRAD_ACCUM_STEPS", "8") or "8"),
    )
    parser.add_argument("--learning-rate", default=os.environ.get("PHASE4_LEARNING_RATE", "2e-4"))
    parser.add_argument(
        "--run-train",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("PHASE4_RUN_TRAIN", "1") == "1",
    )
    parser.add_argument("--run-eval", action=argparse.BooleanOptionalAction, default=os.environ.get("PHASE4_RUN_EVAL", "0") == "1")
    parser.add_argument("--judge", default=os.environ.get("PHASE4_JUDGE", "heuristic"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_batch_rows(batch_dir: Path) -> list[dict[str, Any]]:
    files_dir = batch_dir / "files"
    if not files_dir.exists():
        raise FileNotFoundError(f"Batch files directory not found: {files_dir}")

    rows: list[dict[str, Any]] = []
    for path in sorted(files_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON row file {path}: {exc}") from exc
        if isinstance(payload, dict):
            rows.append(payload)

    if not rows:
        raise RuntimeError(f"No JSON row files found in {files_dir}")
    return rows


def write_attack_csv(rows: list[dict[str, Any]], path: Path) -> None:
    extra_fields = sorted({key for row in rows for key in row.keys()} - set(PREFERRED_FIELDS))
    fieldnames = PREFERRED_FIELDS + extra_fields
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for index, row in enumerate(rows):
            output = dict(row)
            output.setdefault("id", str(index))
            writer.writerow(output)


def safe_filename(value: str) -> str:
    value = str(value or "row")
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value) or "row"


def image_suffix(image_payload: dict[str, Any]) -> str:
    file_name = str(image_payload.get("file_name") or "")
    suffix = Path(file_name).suffix
    if suffix:
        return suffix
    mime_type = str(image_payload.get("mime_type") or "")
    return mimetypes.guess_extension(mime_type) or ".bin"


def is_phase4_json_row(row: dict[str, Any]) -> bool:
    required = {"sample_id", "kind", "split", "image", "prompt", "answer"}
    return required.issubset(row.keys()) and isinstance(row.get("image"), dict)


def is_phase4_json_batch(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(is_phase4_json_row(row) for row in rows[: min(len(rows), 20)])


def source_from_flat_fields(row: dict[str, Any]) -> dict[str, Any]:
    source: dict[str, Any] = {}
    for key, value in row.items():
        if not key.startswith("source_"):
            continue
        if value in ("", None):
            continue
        source[key.removeprefix("source_")] = value
    return source


def write_embedded_image(row: dict[str, Any], image_dir: Path, row_id: str) -> Path:
    image_payload = row.get("image")
    if not isinstance(image_payload, dict):
        raise ValueError(f"Row {row_id} has no embedded image object")

    encoded = image_payload.get("data_base64")
    if not encoded:
        raise ValueError(f"Row {row_id} image object has no data_base64 field")

    image_bytes = base64.b64decode(str(encoded), validate=True)
    expected_sha = str(image_payload.get("sha256") or "").lower()
    actual_sha = hashlib.sha256(image_bytes).hexdigest()
    if expected_sha and actual_sha != expected_sha:
        raise ValueError(f"Row {row_id} image SHA mismatch: expected {expected_sha}, got {actual_sha}")

    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{safe_filename(row_id)}{image_suffix(image_payload)}"
    if not image_path.exists() or image_path.read_bytes() != image_bytes:
        image_path.write_bytes(image_bytes)
    return image_path.resolve()


def materialize_phase4_json_batch(
    rows: list[dict[str, Any]],
    data_dir: Path,
    batch_dir: Path,
    manifest_path: str,
    version: str,
) -> dict[str, Any]:
    split_rows: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}
    image_root = data_dir / "images"

    for index, row in enumerate(rows):
        row_id = str(row.get("sample_id") or row.get("id") or f"row:{index}")
        split = str(row.get("split") or "").strip()
        if split not in split_rows:
            raise ValueError(f"Row {row_id} has unsupported split {split!r}")

        kind = str(row.get("kind") or "unknown")
        image_path = write_embedded_image(row, image_root / split / kind, row_id)
        output_row: dict[str, Any] = {
            "id": row_id,
            "kind": kind,
            "split": split,
            "image": str(image_path),
            "prompt": row.get("prompt", ""),
            "answer": row.get("answer", ""),
        }
        if row.get("intent_key"):
            output_row["intent_key"] = row["intent_key"]

        source = source_from_flat_fields(row)
        if source:
            output_row["source"] = source
        if row.get("_ipfs_upload_metadata"):
            output_row["ledger_upload_metadata"] = row["_ipfs_upload_metadata"]

        split_rows[split].append(output_row)

    jsonl_paths: dict[str, str] = {}
    jsonl_sha256: dict[str, str] = {}
    counts: dict[str, dict[str, int]] = {}
    for split, split_items in split_rows.items():
        jsonl_path = data_dir / f"{split}.jsonl"
        write_jsonl(jsonl_path, split_items)
        jsonl_paths[split] = str(jsonl_path.resolve())
        jsonl_sha256[split] = sha256_file(jsonl_path)

        attack_count = sum(1 for item in split_items if item.get("kind") == "attack")
        benign_count = sum(1 for item in split_items if item.get("kind") == "benign")
        counts[split] = {
            "attack": attack_count,
            "benign": benign_count,
            "total": len(split_items),
        }

    metadata = {
        "source": "threat-ledger-ipfs-batch",
        "version": version,
        "batch_dir": str(batch_dir.resolve()),
        "manifest_path": manifest_path,
        "row_count": len(rows),
        "image_root": str(image_root.resolve()),
        "counts": counts,
        "jsonl_paths": jsonl_paths,
        "jsonl_sha256": jsonl_sha256,
    }
    save_json(data_dir / "dataset_metadata.json", metadata)
    return metadata


def run_command(command: list[str], dry_run: bool) -> None:
    print(" ".join(command), flush=True)
    if not dry_run:
        subprocess.run(command, check=True)


def main() -> int:
    args = parse_args()
    if not args.batch_dir:
        raise RuntimeError("--batch-dir or BATCH_DIR is required")

    output_root = Path(args.output_root).expanduser().resolve()
    version_label = f"ledger_v{args.version}"
    work_dir = output_root / "batches" / version_label
    data_dir = output_root / "data" / version_label
    adapter_dir = output_root / "adapters" / version_label
    eval_dir = output_root / "eval" / version_label

    batch_dir = Path(args.batch_dir).expanduser().resolve()
    rows = load_batch_rows(batch_dir)
    attack_csv = work_dir / "attack_rows.csv"

    if is_phase4_json_batch(rows):
        metadata = materialize_phase4_json_batch(rows, data_dir, batch_dir, args.manifest_path, args.version)
        prepare_command = []
        prepared_from = "embedded_phase4_json_rows"
    else:
        write_attack_csv(rows, attack_csv)
        prepare_command = [
            sys.executable,
            str(Path(__file__).with_name("prepare_sft_data.py")),
            "--attack-csv",
            str(attack_csv),
            "--attack-image-root",
            args.attack_image_root,
            "--output-dir",
            str(data_dir),
        ]
        if args.max_attacks > 0:
            prepare_command.extend(["--max-attacks", str(args.max_attacks)])
        metadata = {}
        prepared_from = "raw_attack_rows"

    train_command = [
        sys.executable,
        str(Path(__file__).with_name("train_lora.py")),
        "--model-name",
        args.model_name,
        "--train-jsonl",
        str(data_dir / "train.jsonl"),
        "--val-jsonl",
        str(data_dir / "val.jsonl"),
        "--dataset-metadata",
        str(data_dir / "dataset_metadata.json"),
        "--output-dir",
        str(adapter_dir),
        "--epochs",
        str(args.epochs),
        "--batch-size",
        str(args.batch_size),
        "--grad-accum-steps",
        str(args.grad_accum_steps),
        "--learning-rate",
        args.learning_rate,
    ]
    if args.deepseek_vl_path:
        train_command.extend(["--deepseek-vl-path", args.deepseek_vl_path])
    if args.previous_adapter:
        train_command.extend(["--resume-adapter", args.previous_adapter])

    save_json(
        work_dir / "batch_run_config.json",
        {
            "version": args.version,
            "batch_dir": args.batch_dir,
            "manifest_path": args.manifest_path,
            "attack_csv": str(attack_csv),
            "data_dir": str(data_dir),
            "adapter_dir": str(adapter_dir),
            "eval_dir": str(eval_dir),
            "row_count": len(rows),
            "prepared_from": prepared_from,
            "run_train": args.run_train,
            "dataset_metadata": metadata,
        },
    )

    if prepare_command:
        run_command(prepare_command, args.dry_run)
    if args.run_train:
        run_command(train_command, args.dry_run)
    else:
        print("Skipping LoRA training because --no-run-train or PHASE4_RUN_TRAIN=0 is set.", flush=True)

    if args.run_eval and args.run_train:
        eval_command = [
            sys.executable,
            str(Path(__file__).with_name("eval_attacks.py")),
            "--model-name",
            args.model_name,
            "--adapter-path",
            str(adapter_dir),
            "--attack-jsonl",
            str(data_dir / "test.jsonl"),
            "--benign-jsonl",
            str(data_dir / "test.jsonl"),
            "--output-dir",
            str(eval_dir),
            "--judge",
            args.judge,
        ]
        if args.deepseek_vl_path:
            eval_command.extend(["--deepseek-vl-path", args.deepseek_vl_path])
        run_command(eval_command, args.dry_run)
    elif args.run_eval:
        print("Skipping evaluation because training was skipped.", flush=True)

    print(f"Phase 4 batch pipeline finished for version {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
