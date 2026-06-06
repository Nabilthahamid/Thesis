#!/usr/bin/env python3
"""Prepare attack and benign multimodal SFT JSONL files for Phase 4."""

from __future__ import annotations

import argparse
import csv
import io
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from phase4.common import (  # noqa: E402
    DEFAULT_ATTACK_CSV,
    DEFAULT_ATTACK_IMAGE_ROOT,
    DEFAULT_OUTPUT_ROOT,
    IMAGE_PLACEHOLDER,
    make_attack_answer,
    normalize_intent,
    resolve_image_path,
    save_json,
    set_seed,
    sha256_file,
    truncate_text,
    write_jsonl,
)


SPLITS = ("train", "val", "test")
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
    parser.add_argument("--attack-csv", default=str(DEFAULT_ATTACK_CSV))
    parser.add_argument("--attack-image-root", default=str(DEFAULT_ATTACK_IMAGE_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT / "data" / "jailbreakv_full"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.10)
    parser.add_argument("--test-ratio", type=float, default=0.20)
    parser.add_argument("--attack-benign-ratio", type=int, default=4)
    parser.add_argument("--max-attacks", type=int, default=0, help="Limit attack rows for smoke tests. 0 means all.")
    parser.add_argument("--max-prompt-chars", type=int, default=6000)
    parser.add_argument("--skip-benign-download", action="store_true")
    parser.add_argument("--benign-dataset", default="lmms-lab/VQAv2")
    parser.add_argument("--benign-split", default="train")
    parser.add_argument("--benign-image-field", default="image")
    parser.add_argument("--benign-question-field", default="question")
    parser.add_argument(
        "--benign-answer-fields",
        default="multiple_choice_answer,answer,answers",
        help="Comma-separated answer fields to try in order.",
    )
    parser.add_argument("--benign-streaming", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--benign-buffer-size", type=int, default=512)
    parser.add_argument("--benign-progress-every", type=int, default=250)
    parser.add_argument("--benign-prompt", default=f"{IMAGE_PLACEHOLDER}\n{{question}}")
    return parser.parse_args()


def validate_ratios(args: argparse.Namespace) -> None:
    total = args.train_ratio + args.val_ratio + args.test_ratio
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Split ratios must sum to 1.0, got {total:.4f}")
    if args.attack_benign_ratio < 0:
        raise ValueError("--attack-benign-ratio must be non-negative")


def read_attack_rows(path: Path, image_root: Path, max_prompt_chars: int) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header found in {path}")

    prepared: list[dict[str, Any]] = []
    missing: list[str] = []
    for index, row in enumerate(rows):
        image_path = resolve_image_path(row.get("image_path", ""), image_root)
        if not image_path.exists():
            missing.append(str(image_path))
            continue

        row_id = str(row.get("id") or index)
        jailbreak_query = truncate_text(row.get("jailbreak_query", ""), max_prompt_chars).strip()
        if not jailbreak_query:
            continue

        prepared.append(
            {
                "id": f"attack:{row_id}",
                "kind": "attack",
                "image": str(image_path),
                "prompt": f"{IMAGE_PLACEHOLDER}\n{jailbreak_query}",
                "answer": make_attack_answer(row.get("policy", "")),
                "intent_key": normalize_intent(row.get("redteam_query", "")),
                "source": {field: row.get(field, "") for field in PREFERRED_FIELDS},
            }
        )

    if missing:
        preview = "\n".join(missing[:10])
        raise FileNotFoundError(f"{len(missing)} attack image(s) were missing. First paths:\n{preview}")

    return prepared


def limit_by_intent_groups(rows: list[dict[str, Any]], max_attacks: int, seed: int) -> list[dict[str, Any]]:
    if max_attacks <= 0 or len(rows) <= max_attacks:
        return rows

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["intent_key"]].append(row)

    rng = random.Random(seed)
    groups = list(grouped.values())
    for group in groups:
        rng.shuffle(group)
    rng.shuffle(groups)

    selected: list[dict[str, Any]] = []
    while len(selected) < max_attacks:
        added = False
        for group in groups:
            if group:
                selected.append(group.pop())
                added = True
                if len(selected) >= max_attacks:
                    break
        if not added:
            break
    return selected


def split_attack_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["intent_key"]].append(row)

    rng = random.Random(args.seed)
    groups = list(grouped.values())
    rng.shuffle(groups)

    total_rows = len(rows)
    targets = {
        "train": int(total_rows * args.train_ratio),
        "val": int(total_rows * args.val_ratio),
    }
    split_rows = {split: [] for split in SPLITS}

    for group in groups:
        if len(split_rows["train"]) + len(group) <= targets["train"] or not split_rows["train"]:
            split = "train"
        elif len(split_rows["val"]) + len(group) <= targets["val"] or not split_rows["val"]:
            split = "val"
        else:
            split = "test"

        for row in group:
            row["split"] = split
        split_rows[split].extend(group)

    return split_rows


def assert_no_intent_leakage(split_rows: dict[str, list[dict[str, Any]]]) -> None:
    seen: dict[str, str] = {}
    for split, rows in split_rows.items():
        for row in rows:
            key = row["intent_key"]
            previous = seen.get(key)
            if previous and previous != split:
                raise AssertionError(f"Intent split leakage for key {key!r}: {previous} and {split}")
            seen[key] = split


def most_common_text(values: list[str]) -> str:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return ""
    return Counter(cleaned).most_common(1)[0][0]


def extract_benign_answer(row: dict[str, Any], answer_fields: list[str]) -> str:
    for field in answer_fields:
        if field not in row:
            continue
        value = row[field]
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("answer", "text", "caption"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
        if isinstance(value, list):
            values: list[str] = []
            for item in value:
                if isinstance(item, str):
                    values.append(item)
                elif isinstance(item, dict):
                    nested = item.get("answer") or item.get("text") or item.get("caption")
                    if isinstance(nested, str):
                        values.append(nested)
            answer = most_common_text(values)
            if answer:
                return answer
    return ""


def save_benign_image(image_value: Any, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if hasattr(image_value, "save"):
            image_value.convert("RGB").save(path, format="JPEG", quality=92)
            return True
        if isinstance(image_value, dict):
            if image_value.get("bytes"):
                from PIL import Image

                image = Image.open(io.BytesIO(image_value["bytes"])).convert("RGB")
                image.save(path, format="JPEG", quality=92)
                return True
            if image_value.get("path"):
                source = Path(image_value["path"])
                if source.exists():
                    shutil.copyfile(source, path)
                    return True
        if isinstance(image_value, str):
            source = Path(image_value)
            if source.exists():
                shutil.copyfile(source, path)
                return True
    except Exception:
        return False
    return False


def load_benign_iterable(args: argparse.Namespace):
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Install datasets before downloading benign VQA rows.") from exc

    split_candidates = []
    for split in (args.benign_split, "train", "validation", "val", "test"):
        if split and split not in split_candidates:
            split_candidates.append(split)

    last_error: Exception | None = None
    dataset = None
    chosen_split = args.benign_split
    for split in split_candidates:
        try:
            print(
                f"Loading benign dataset {args.benign_dataset!r} split {split!r} "
                f"(streaming={args.benign_streaming})...",
                flush=True,
            )
            dataset = load_dataset(args.benign_dataset, split=split, streaming=args.benign_streaming)
            chosen_split = split
            break
        except Exception as exc:
            last_error = exc

    if dataset is None:
        raise RuntimeError(
            f"Could not load any split from {args.benign_dataset}. Tried: {split_candidates}"
        ) from last_error
    if chosen_split != args.benign_split:
        print(f"Using benign split {chosen_split!r} because {args.benign_split!r} was unavailable.")
        args.benign_split = chosen_split

    if args.benign_streaming and hasattr(dataset, "shuffle"):
        print(f"Shuffling benign stream with buffer_size={args.benign_buffer_size}...", flush=True)
        dataset = dataset.shuffle(seed=args.seed, buffer_size=args.benign_buffer_size)
    elif not args.benign_streaming and hasattr(dataset, "shuffle"):
        dataset = dataset.shuffle(seed=args.seed)
    return dataset


def collect_benign_rows(
    args: argparse.Namespace,
    output_dir: Path,
    split_counts: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    needed_by_split = {
        split: split_counts[split] * args.attack_benign_ratio for split in SPLITS
    }
    answer_fields = [field.strip() for field in args.benign_answer_fields.split(",") if field.strip()]
    benign_rows = {split: [] for split in SPLITS}
    dataset = load_benign_iterable(args)
    total_needed = sum(needed_by_split.values())
    print(f"Collecting {total_needed} benign rows: {needed_by_split}", flush=True)
    reused_images = 0
    saved_images = 0

    for index, row in enumerate(dataset):
        split = next(
            (candidate for candidate in SPLITS if len(benign_rows[candidate]) < needed_by_split[candidate]),
            "",
        )
        if not split:
            break

        question = str(row.get(args.benign_question_field, "")).strip()
        answer = extract_benign_answer(row, answer_fields)
        image_value = row.get(args.benign_image_field)
        if not question or not answer or image_value is None:
            continue

        image_path = output_dir / "benign_images" / split / f"benign_{len(benign_rows[split]):06d}.jpg"
        image_exists = image_path.exists() and image_path.stat().st_size > 0
        if image_exists:
            reused_images += 1
        elif save_benign_image(image_value, image_path):
            saved_images += 1
        else:
            continue

        benign_rows[split].append(
            {
                "id": f"benign:{split}:{len(benign_rows[split])}",
                "kind": "benign",
                "split": split,
                "image": str(image_path.resolve()),
                "prompt": args.benign_prompt.format(question=question),
                "answer": answer,
                "source": {
                    "dataset": args.benign_dataset,
                    "split": args.benign_split,
                    "row_index": index,
                    "question": question,
                    "answer": answer,
                },
            }
        )

        collected = sum(len(rows) for rows in benign_rows.values())
        if args.benign_progress_every > 0 and collected % args.benign_progress_every == 0:
            print(
                "Collected benign rows: "
                f"{collected}/{total_needed} "
                f"(train={len(benign_rows['train'])}, "
                f"val={len(benign_rows['val'])}, "
                f"test={len(benign_rows['test'])}, "
                f"reused_images={reused_images}, "
                f"saved_images={saved_images})",
                flush=True,
            )

    missing = {
        split: needed_by_split[split] - len(benign_rows[split])
        for split in SPLITS
        if len(benign_rows[split]) < needed_by_split[split]
    }
    if missing:
        raise RuntimeError(f"Could not collect enough benign rows: {missing}")
    return benign_rows


def write_outputs(
    output_dir: Path,
    attack_rows: dict[str, list[dict[str, Any]]],
    benign_rows: dict[str, list[dict[str, Any]]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    jsonl_paths: dict[str, str] = {}
    hashes: dict[str, str] = {}

    rng = random.Random(args.seed)
    for split in SPLITS:
        rows = list(attack_rows[split]) + list(benign_rows.get(split, []))
        rng.shuffle(rows)
        jsonl_path = output_dir / f"{split}.jsonl"
        write_jsonl(jsonl_path, rows)
        jsonl_paths[split] = str(jsonl_path.resolve())
        hashes[split] = sha256_file(jsonl_path)
        counts[split] = {
            "attack": len(attack_rows[split]),
            "benign": len(benign_rows.get(split, [])),
            "total": len(rows),
        }

    metadata = {
        "seed": args.seed,
        "attack_csv": str(Path(args.attack_csv).resolve()),
        "attack_csv_sha256": sha256_file(Path(args.attack_csv).resolve()),
        "attack_image_root": str(Path(args.attack_image_root).resolve()),
        "attack_benign_ratio": args.attack_benign_ratio,
        "benign_dataset": None if args.skip_benign_download else args.benign_dataset,
        "benign_split": None if args.skip_benign_download else args.benign_split,
        "counts": counts,
        "jsonl_paths": jsonl_paths,
        "jsonl_sha256": hashes,
        "split_ratios": {
            "train": args.train_ratio,
            "val": args.val_ratio,
            "test": args.test_ratio,
        },
    }
    save_json(output_dir / "dataset_metadata.json", metadata)
    return metadata


def main() -> int:
    args = parse_args()
    validate_ratios(args)
    set_seed(args.seed)

    attack_csv = Path(args.attack_csv).expanduser().resolve()
    image_root = Path(args.attack_image_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    attack_rows = read_attack_rows(attack_csv, image_root, args.max_prompt_chars)
    print(f"Loaded {len(attack_rows)} attack rows from {attack_csv}", flush=True)
    attack_rows = limit_by_intent_groups(attack_rows, args.max_attacks, args.seed)
    if args.max_attacks > 0:
        print(f"Using {len(attack_rows)} attack rows after --max-attacks {args.max_attacks}", flush=True)
    split_rows = split_attack_rows(attack_rows, args)
    assert_no_intent_leakage(split_rows)
    print(
        "Attack split counts: "
        f"train={len(split_rows['train'])}, "
        f"val={len(split_rows['val'])}, "
        f"test={len(split_rows['test'])}",
        flush=True,
    )

    benign_rows = {split: [] for split in SPLITS}
    if not args.skip_benign_download and args.attack_benign_ratio > 0:
        split_counts = {split: len(rows) for split, rows in split_rows.items()}
        benign_rows = collect_benign_rows(args, output_dir, split_counts)

    metadata = write_outputs(output_dir, split_rows, benign_rows, args)
    print(f"Wrote Phase 4 SFT data to {output_dir}")
    print(f"Counts: {metadata['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
