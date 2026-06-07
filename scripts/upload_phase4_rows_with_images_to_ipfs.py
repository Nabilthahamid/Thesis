#!/usr/bin/env python3
"""Upload Phase 4 CSV rows to IPFS with image bytes embedded as base64."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INPUT_CSV = Path(
    r"D:\Thesisssss\phase4_outputs\data\jailbreakv_2k_8k\jailbreakv_2k_8k_used_dataset.csv"
)
DEFAULT_API = "http://127.0.0.1:5001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload each Phase 4 CSV row as JSON with the image embedded as base64."
    )
    parser.add_argument("input_csv", nargs="?", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--api", default=DEFAULT_API, help="IPFS API URL, usually http://127.0.0.1:5001")
    parser.add_argument("--id-column", default="sample_id")
    parser.add_argument("--image-column", default="image")
    parser.add_argument("--output-dir", help="Defaults to INPUT_CSV parent / ipfs_row_uploads.")
    parser.add_argument("--cid-version", choices=("0", "1"), default="1")
    parser.add_argument("--pin", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--only-hash", action="store_true", help="Calculate CIDs without storing objects.")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--limit", type=int, default=0, help="Upload only first N not-yet-successful rows. 0 means all.")
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument(
        "--no-final-upload",
        action="store_true",
        help="Write final CSV locally but do not upload it to IPFS.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_api_url(api_url: str) -> str:
    return api_url.rstrip("/")


def count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        return max(0, sum(1 for _ in reader) - 1)


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            return next(reader)
        except StopIteration:
            raise ValueError(f"CSV file is empty: {path}") from None


def safe_filename(value: str, suffix: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)
    return f"{safe or 'row'}{suffix}"


def resolve_image_path(input_csv: Path, image_value: str) -> Path:
    image_path = Path(image_value or "")
    if not image_path.is_absolute():
        image_path = input_csv.parent / image_path
    return image_path.resolve()


def make_multipart_body(file_bytes: bytes, filename: str, content_type: str) -> tuple[bytes, str]:
    boundary = f"----phase4-ipfs-{uuid.uuid4().hex}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + file_bytes + footer, boundary


def ipfs_add(
    api_url: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    cid_version: str,
    pin: bool,
    only_hash: bool,
    timeout: int,
) -> str:
    query = urllib.parse.urlencode(
        {
            "cid-version": cid_version,
            "pin": str(pin).lower(),
            "only-hash": str(only_hash).lower(),
        }
    )
    body, boundary = make_multipart_body(file_bytes, filename, content_type)
    request = urllib.request.Request(
        f"{api_url}/api/v0/add?{query}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")

    for line in reversed([line for line in raw.splitlines() if line.strip()]):
        parsed = json.loads(line)
        cid = parsed.get("Hash")
        if cid:
            return cid
    raise RuntimeError(f"IPFS did not return a CID. Raw response: {raw}")


def ensure_csv(path: Path, fieldnames: list[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()


def append_csv_row(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def load_success_cids(progress_csv: Path) -> dict[str, str]:
    if not progress_csv.exists():
        return {}
    successes: dict[str, str] = {}
    with progress_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_id = row.get("row_id", "")
            row_cid = row.get("row_cid", "")
            if row.get("status") == "success" and row_id and row_cid:
                successes[row_id] = row_cid
    return successes


def build_payload(
    input_csv: Path,
    row: dict[str, str],
    row_index: int,
    row_id: str,
    image_column: str,
) -> tuple[bytes, str, str, int, str]:
    original_image_path = row.get(image_column, "")
    image_path = resolve_image_path(input_csv, original_image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image_bytes = image_path.read_bytes()
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()

    payload_row: dict[str, Any] = dict(row)
    payload_row[image_column] = {
        "encoding": "base64",
        "file_name": image_path.name,
        "mime_type": mime_type,
        "size_bytes": len(image_bytes),
        "sha256": image_sha256,
        "data_base64": base64.b64encode(image_bytes).decode("ascii"),
    }
    payload_row["_ipfs_upload_metadata"] = {
        "row_index": row_index,
        "row_id": row_id,
        "image_column": image_column,
        "original_image_path": original_image_path,
        "uploaded_format": "json-with-base64-image",
    }

    payload = json.dumps(payload_row, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return payload, safe_filename(row_id, ".json"), "application/json", len(image_bytes), image_sha256


def write_final_manifest(
    input_csv: Path,
    final_csv: Path,
    id_column: str,
    image_column: str,
    success_cids: dict[str, str],
) -> dict[str, int]:
    total = 0
    successes = 0
    pending = 0
    fieldnames = ["row_index", "row_id", "original_image_path", "row_cid", "status"]
    tmp_path = final_csv.with_name(f"{final_csv.name}.tmp")
    final_csv.parent.mkdir(parents=True, exist_ok=True)

    with input_csv.open("r", encoding="utf-8-sig", newline="") as source, tmp_path.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        for row_index, row in enumerate(reader):
            total += 1
            row_id = str(row.get(id_column) or row_index)
            row_cid = success_cids.get(row_id, "")
            status = "success" if row_cid else "pending"
            if row_cid:
                successes += 1
            else:
                pending += 1
            writer.writerow(
                {
                    "row_index": row_index,
                    "row_id": row_id,
                    "original_image_path": row.get(image_column, ""),
                    "row_cid": row_cid,
                    "status": status,
                }
            )

    os.replace(tmp_path, final_csv)
    return {"total_rows": total, "successes": successes, "pending": pending}


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def main() -> int:
    args = parse_args()
    input_csv = Path(args.input_csv).expanduser().resolve()
    if not input_csv.exists():
        print(f"Input CSV not found: {input_csv}", file=sys.stderr)
        return 1

    header = read_header(input_csv)
    if args.id_column not in header:
        print(f"Missing id column {args.id_column!r}. Found: {', '.join(header)}", file=sys.stderr)
        return 1
    if args.image_column not in header:
        print(f"Missing image column {args.image_column!r}. Found: {', '.join(header)}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_csv.parent / "ipfs_row_uploads"
    progress_csv = output_dir / f"{input_csv.stem}_upload_progress.csv"
    final_csv = output_dir / f"{input_csv.stem}_row_cids.csv"
    final_cid_file = output_dir / f"{input_csv.stem}_final_csv_cid.txt"
    summary_json = output_dir / f"{input_csv.stem}_upload_summary.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    progress_fields = [
        "row_index",
        "row_id",
        "original_image_path",
        "row_cid",
        "status",
        "error",
        "image_size_bytes",
        "image_sha256",
        "json_size_bytes",
        "uploaded_at_utc",
    ]
    ensure_csv(progress_csv, progress_fields)

    api_url = normalize_api_url(args.api)
    total_rows = count_csv_rows(input_csv)
    success_cids = load_success_cids(progress_csv)
    uploaded = 0
    skipped = 0
    failures = 0
    start = time.time()

    try:
        with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row_index, row in enumerate(reader):
                row_id = str(row.get(args.id_column) or row_index)
                original_image_path = row.get(args.image_column, "")

                if row_id in success_cids:
                    skipped += 1
                    continue

                try:
                    payload, filename, content_type, image_size, image_sha256 = build_payload(
                        input_csv, row, row_index, row_id, args.image_column
                    )
                    row_cid = ipfs_add(
                        api_url=api_url,
                        file_bytes=payload,
                        filename=filename,
                        content_type=content_type,
                        cid_version=args.cid_version,
                        pin=args.pin,
                        only_hash=args.only_hash,
                        timeout=args.timeout,
                    )
                    success_cids[row_id] = row_cid
                    uploaded += 1
                    append_csv_row(
                        progress_csv,
                        progress_fields,
                        {
                            "row_index": row_index,
                            "row_id": row_id,
                            "original_image_path": original_image_path,
                            "row_cid": row_cid,
                            "status": "success",
                            "error": "",
                            "image_size_bytes": image_size,
                            "image_sha256": image_sha256,
                            "json_size_bytes": len(payload),
                            "uploaded_at_utc": utc_now(),
                        },
                    )
                    print(f"[{row_index + 1}/{total_rows}] id={row_id} uploaded cid={row_cid}", flush=True)
                except Exception as exc:
                    failures += 1
                    append_csv_row(
                        progress_csv,
                        progress_fields,
                        {
                            "row_index": row_index,
                            "row_id": row_id,
                            "original_image_path": original_image_path,
                            "row_cid": "",
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "image_size_bytes": "",
                            "image_sha256": "",
                            "json_size_bytes": "",
                            "uploaded_at_utc": utc_now(),
                        },
                    )
                    print(f"[{row_index + 1}/{total_rows}] id={row_id} failed: {exc}", flush=True)

                if args.checkpoint_every > 0 and (uploaded + failures) % args.checkpoint_every == 0:
                    write_final_manifest(input_csv, final_csv, args.id_column, args.image_column, success_cids)

                if args.limit and uploaded >= args.limit:
                    break
    except KeyboardInterrupt:
        print("Interrupted. Progress has been saved.", file=sys.stderr)

    manifest_stats = write_final_manifest(input_csv, final_csv, args.id_column, args.image_column, success_cids)
    final_csv_cid = ""
    final_csv_error = ""
    if not args.no_final_upload:
        try:
            final_csv_cid = ipfs_add(
                api_url=api_url,
                file_bytes=final_csv.read_bytes(),
                filename=final_csv.name,
                content_type="text/csv",
                cid_version=args.cid_version,
                pin=args.pin,
                only_hash=args.only_hash,
                timeout=args.timeout,
            )
            final_cid_file.write_text(final_csv_cid + "\n", encoding="utf-8")
        except Exception as exc:
            final_csv_error = f"{type(exc).__name__}: {exc}"
            print(f"Final CSV upload failed: {final_csv_error}", file=sys.stderr)

    elapsed = time.time() - start
    summary = {
        "input_csv": str(input_csv),
        "progress_csv": str(progress_csv),
        "final_csv": str(final_csv),
        "final_csv_cid": final_csv_cid,
        "final_csv_error": final_csv_error,
        "total_rows": total_rows,
        "uploaded_this_run": uploaded,
        "skipped_existing_successes": skipped,
        "failures_this_run": failures,
        "total_successful_row_cids": len(success_cids),
        "pending_rows": manifest_stats["pending"],
        "elapsed_seconds": round(elapsed, 2),
        "pin": args.pin,
        "only_hash": args.only_hash,
        "cid_version": args.cid_version,
        "finished_at_utc": utc_now(),
    }
    write_summary(summary_json, summary)
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if failures == 0 and not final_csv_error else 1


if __name__ == "__main__":
    raise SystemExit(main())
