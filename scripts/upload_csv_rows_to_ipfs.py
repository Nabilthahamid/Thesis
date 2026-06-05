#!/usr/bin/env python3
"""Upload CSV rows to a local IPFS node and store the returned CIDs."""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


# This script is intentionally dependency-free. It uses Python's built-in
# urllib module instead of requests, so you can run it without pip install.


def parse_args() -> argparse.Namespace:
    """Read command-line options and set useful defaults for this dataset."""
    parser = argparse.ArgumentParser(
        description="Upload each CSV row, id, column value, or referenced image to local IPFS."
    )
    parser.add_argument(
        "input_csv",
        nargs="?",
        default=r"D:\Thesisssss\JailBreakV_28K\JailBreakV_28k\mini_JailBreakV_28K.csv",
        help="Input CSV path.",
    )
    parser.add_argument(
        "--api",
        default="http://127.0.0.1:5001",
        help="Local IPFS API base URL.",
    )
    parser.add_argument("--id-column", default="id", help="Column used as the row id.")
    parser.add_argument(
        "--cid-column",
        default="ipfs_cid",
        help="CID column name to add to the output CSV.",
    )
    parser.add_argument(
        "--content",
        choices=("row", "id", "column", "image"),
        default="row",
        help=(
            "What to upload for each CSV row. "
            "'row' uploads the whole row as JSON; 'id' uploads only the id value; "
            "'column' uploads one column value; 'image' uploads the file in image_path."
        ),
    )
    parser.add_argument(
        "--column",
        help="Column name to upload when --content column is used.",
    )
    parser.add_argument(
        "--image-column",
        default="image_path",
        help="Image path column to upload when --content image is used.",
    )
    parser.add_argument(
        "--output-csv",
        help="Output CSV path. Defaults to INPUT_with_cids.csv.",
    )
    parser.add_argument(
        "--cid-map",
        help="Append-only id/CID progress CSV. Defaults to INPUT_cids.csv.",
    )
    parser.add_argument(
        "--cid-version",
        choices=("0", "1"),
        default="1",
        help="CID version requested from IPFS.",
    )
    parser.add_argument(
        "--pin",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pin uploaded objects in the local IPFS node.",
    )
    parser.add_argument(
        "--only-hash",
        action="store_true",
        help="Ask IPFS to calculate CIDs without storing the objects.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=50,
        help="Rewrite the full output CSV after this many new uploads.",
    )
    parser.add_argument("--limit", type=int, help="Upload only this many new rows.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout per IPFS upload, in seconds.",
    )
    return parser.parse_args()


def default_output_paths(input_csv: Path) -> Tuple[Path, Path]:
    """Build the default output paths inside the ipfs_cid_outputs folder."""
    output_dir = input_csv.parent / "ipfs_cid_outputs"
    output_csv = output_dir / f"{input_csv.stem}_with_cids{input_csv.suffix}"
    cid_map = output_dir / f"{input_csv.stem}_cids{input_csv.suffix}"
    return output_csv, cid_map


def read_csv_rows(path: Path) -> Tuple[List[dict], List[str]]:
    """Load the whole CSV file as a list of dictionaries."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header found in {path}")
        return rows, list(reader.fieldnames)


def load_cid_map(path: Path, id_column: str, cid_column: str) -> Dict[str, str]:
    """Read an existing id-to-CID CSV so interrupted uploads can resume."""
    if not path.exists():
        return {}

    cids: Dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return cids
        for row in reader:
            row_id = row.get(id_column, "")
            cid = row.get(cid_column, "")
            if row_id and cid:
                cids[row_id] = cid
    return cids


def ensure_cid_map(path: Path, id_column: str, cid_column: str) -> None:
    """Create the append-only CID progress file if it does not exist yet."""
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[id_column, cid_column])
        writer.writeheader()


def append_cid(path: Path, id_column: str, cid_column: str, row_id: str, cid: str) -> None:
    """Immediately save one uploaded row's CID to the progress file."""
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[id_column, cid_column])
        writer.writerow({id_column: row_id, cid_column: cid})
        handle.flush()
        # Force the write to disk so progress survives a crash or Ctrl+C.
        os.fsync(handle.fileno())


def write_output_csv(
    path: Path,
    rows: Iterable[dict],
    fieldnames: List[str],
    id_column: str,
    cid_column: str,
    cids: Dict[str, str],
) -> None:
    """Write a full copy of the input CSV plus the new CID column."""
    output_fields = list(fieldnames)
    if cid_column not in output_fields:
        output_fields.append(cid_column)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output_row = dict(row)
            row_id = str(output_row.get(id_column, ""))
            output_row[cid_column] = cids.get(row_id, output_row.get(cid_column, ""))
            writer.writerow(output_row)
    # Replace the output atomically, avoiding a half-written CSV if interrupted.
    os.replace(tmp_path, path)


def normalize_api_url(api_url: str) -> str:
    """Remove a trailing slash so URL joining is predictable."""
    return api_url.rstrip("/")


def filename_for_row(row_id: str, extension: str) -> str:
    """Create a safe filename for the temporary file sent to IPFS."""
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in row_id)
    return f"{safe or 'row'}{extension}"


def payload_for_row(args: argparse.Namespace, input_csv: Path, row: dict) -> Tuple[bytes, str, str]:
    """Convert one CSV row into the bytes that will be uploaded to IPFS."""
    row_id = str(row[args.id_column])

    if args.content == "row":
        # Default mode: upload the complete CSV row as a JSON object.
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return payload, filename_for_row(row_id, ".json"), "application/json"

    if args.content == "id":
        # Upload only the row id, for example: 0, 1, 2.
        return f"{row_id}\n".encode("utf-8"), filename_for_row(row_id, ".txt"), "text/plain"

    if args.content == "column":
        # Upload only one chosen CSV column, such as jailbreak_query.
        if not args.column:
            raise ValueError("--column is required when --content column is used")
        payload = str(row.get(args.column, ""))
        return payload.encode("utf-8"), filename_for_row(row_id, ".txt"), "text/plain"

    # Image mode: read the image path from the CSV and upload that image file.
    image_value = row.get(args.image_column, "")
    if not image_value:
        raise ValueError(f"Row id {row_id} has no value in {args.image_column}")

    image_path = Path(image_value)
    if not image_path.is_absolute():
        image_path = input_csv.parent / image_path
    if not image_path.exists():
        raise FileNotFoundError(f"Row id {row_id} image not found: {image_path}")

    content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    return image_path.read_bytes(), image_path.name, content_type


def make_multipart_body(file_bytes: bytes, filename: str, content_type: str) -> Tuple[bytes, str]:
    """Build the multipart/form-data body required by IPFS /api/v0/add."""
    boundary = f"----codex-ipfs-{uuid.uuid4().hex}"
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
    """Upload one file/object to local IPFS and return the CID."""
    # These query parameters control how Kubo/IPFS stores the object.
    query = urllib.parse.urlencode(
        {
            "cid-version": cid_version,
            "pin": str(pin).lower(),
            "only-hash": str(only_hash).lower(),
        }
    )
    body, boundary = make_multipart_body(file_bytes, filename, content_type)

    # POST the file bytes to the local IPFS API. Port 5001 is the API port.
    request = urllib.request.Request(
        f"{api_url}/api/v0/add?{query}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")

    # Kubo returns JSON lines. The CID is in the "Hash" field.
    for line in reversed([line for line in raw.splitlines() if line.strip()]):
        parsed = json.loads(line)
        cid = parsed.get("Hash")
        if cid:
            return cid

    raise RuntimeError(f"IPFS add did not return a CID. Raw response: {raw}")


def main() -> int:
    """Run the full CSV-to-IPFS upload workflow."""
    args = parse_args()
    input_csv = Path(args.input_csv)
    if not input_csv.exists():
        print(f"Input CSV not found: {input_csv}", file=sys.stderr)
        return 1

    output_default, cid_map_default = default_output_paths(input_csv)
    output_csv = Path(args.output_csv) if args.output_csv else output_default
    cid_map = Path(args.cid_map) if args.cid_map else cid_map_default
    api_url = normalize_api_url(args.api)

    # Load the source CSV and validate the columns we need.
    rows, fieldnames = read_csv_rows(input_csv)
    if args.id_column not in fieldnames:
        print(f"Missing id column '{args.id_column}'. Columns: {', '.join(fieldnames)}", file=sys.stderr)
        return 1
    if args.cid_column in fieldnames:
        print(f"Input already has a '{args.cid_column}' column; choose another --cid-column.", file=sys.stderr)
        return 1

    # Load previous progress from both output files, then create the progress file.
    cids = load_cid_map(cid_map, args.id_column, args.cid_column)
    cids.update(load_cid_map(output_csv, args.id_column, args.cid_column))
    ensure_cid_map(cid_map, args.id_column, args.cid_column)

    uploaded = 0
    skipped = 0
    start = time.time()

    try:
        for index, row in enumerate(rows, start=1):
            row_id = str(row[args.id_column])

            # If this id already has a CID, skip it. This makes reruns safe.
            if row_id in cids:
                skipped += 1
                continue

            # Convert the row into upload bytes, send it to IPFS, and receive a CID.
            file_bytes, filename, content_type = payload_for_row(args, input_csv, row)
            cid = ipfs_add(
                api_url=api_url,
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
                cid_version=args.cid_version,
                pin=args.pin,
                only_hash=args.only_hash,
                timeout=args.timeout,
            )

            # Save progress immediately after each successful upload.
            cids[row_id] = cid
            append_cid(cid_map, args.id_column, args.cid_column, row_id, cid)
            uploaded += 1
            print(f"[{index}/{len(rows)}] id={row_id} cid={cid}", flush=True)

            # Periodically rewrite the complete CSV with all known CIDs.
            if args.checkpoint_every > 0 and uploaded % args.checkpoint_every == 0:
                write_output_csv(output_csv, rows, fieldnames, args.id_column, args.cid_column, cids)

            # Useful for testing: stop after a small number of new uploads.
            if args.limit and uploaded >= args.limit:
                break
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"IPFS API error: {exc}", file=sys.stderr)
        print(f"Progress is saved in: {cid_map}", file=sys.stderr)
        # Even on error, write the full output CSV with whatever completed.
        write_output_csv(output_csv, rows, fieldnames, args.id_column, args.cid_column, cids)
        return 1

    # Final write after all rows finish.
    write_output_csv(output_csv, rows, fieldnames, args.id_column, args.cid_column, cids)
    elapsed = time.time() - start
    print(f"Done. Uploaded {uploaded}, skipped {skipped}, elapsed {elapsed:.1f}s")
    print(f"CID map: {cid_map}")
    print(f"Output CSV: {output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
