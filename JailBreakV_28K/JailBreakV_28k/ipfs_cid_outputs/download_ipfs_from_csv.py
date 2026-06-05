import argparse
import base64
import csv
import hashlib
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CSV = Path(__file__).with_name("mini_JailBreakV_28K_cids.csv")

GATEWAYS = (
    "https://ipfs.io/ipfs/{cid}",
    "https://dweb.link/ipfs/{cid}",
    "https://gateway.pinata.cloud/ipfs/{cid}",
)

CONTENT_TYPE_EXTENSIONS = {
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/octet-stream": ".bin",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/plain": ".txt",
}


def safe_filename_part(value):
    value = str(value or "").strip()
    if not value:
        value = "row"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def guess_extension(content_type):
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not content_type:
        return ".bin"
    if content_type in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[content_type]
    guessed = mimetypes.guess_extension(content_type)
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".bin"


def read_varint(data, offset):
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("Invalid CID varint")


def expected_sha256_from_cid(cid):
    """Return SHA-256 digest for raw CIDv1 values such as bafkrei..., else None."""
    cid = cid.strip()
    if not cid.startswith("b"):
        return None

    encoded = cid[1:].upper()
    encoded += "=" * ((8 - len(encoded) % 8) % 8)

    try:
        cid_bytes = base64.b32decode(encoded)
        version, offset = read_varint(cid_bytes, 0)
        codec, offset = read_varint(cid_bytes, offset)
        hash_code, offset = read_varint(cid_bytes, offset)
        hash_size, offset = read_varint(cid_bytes, offset)
    except Exception:
        return None

    # 1 = CIDv1, 0x55 = raw, 0x12 = sha2-256, 32 = digest bytes.
    if version == 1 and codec == 0x55 and hash_code == 0x12 and hash_size == 32:
        digest = cid_bytes[offset : offset + hash_size]
        if len(digest) == 32:
            return digest
    return None


def sha256_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.digest()


def existing_download(output_dir, base_name):
    for path in output_dir.glob(base_name + ".*"):
        if path.suffix != ".part" and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def download_url(url, temp_path, timeout):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "User-Agent": "IPFS-CSV-Downloader/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        with temp_path.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    return content_type


def download_cid(row, row_number, args, output_dir):
    cid = (row.get(args.cid_column) or "").strip()
    record_id = (row.get(args.id_column) or str(row_number)).strip()

    if not cid:
        return {
            "id": record_id,
            "cid": cid,
            "status": "failed",
            "path": "",
            "gateway": "",
            "verified": "no",
            "error": "Missing CID",
        }

    base_name = f"id_{safe_filename_part(record_id)}_{safe_filename_part(cid)}"

    if not args.force:
        existing = existing_download(output_dir, base_name)
        if existing:
            return {
                "id": record_id,
                "cid": cid,
                "status": "skipped",
                "path": str(existing),
                "gateway": "",
                "verified": "not_checked",
                "error": "Already downloaded",
            }

    last_error = ""
    expected_digest = expected_sha256_from_cid(cid)

    for attempt in range(1, args.retries + 1):
        for gateway_template in GATEWAYS:
            gateway_url = gateway_template.format(cid=cid)
            temp_path = output_dir / f"{base_name}.part"

            try:
                if temp_path.exists():
                    temp_path.unlink()

                content_type = download_url(gateway_url, temp_path, args.timeout)

                verified = "not_checked"
                if expected_digest is not None:
                    if sha256_file(temp_path) != expected_digest:
                        temp_path.unlink(missing_ok=True)
                        raise ValueError("Downloaded bytes do not match the CID SHA-256 hash")
                    verified = "yes"

                extension = guess_extension(content_type)
                final_path = output_dir / f"{base_name}{extension}"
                if final_path.exists() and args.force:
                    final_path.unlink()
                temp_path.replace(final_path)

                return {
                    "id": record_id,
                    "cid": cid,
                    "status": "downloaded",
                    "path": str(final_path),
                    "gateway": gateway_url,
                    "verified": verified,
                    "error": "",
                }
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        if attempt < args.retries:
            time.sleep(min(5, attempt * 2))

    return {
        "id": record_id,
        "cid": cid,
        "status": "failed",
        "path": "",
        "gateway": "",
        "verified": "no",
        "error": last_error,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download every IPFS CID listed in a CSV file."
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        default=str(DEFAULT_CSV),
        help="CSV file containing an ipfs_cid column.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="Folder where downloaded files will be saved. Default: downloaded_ipfs_files beside the CSV.",
    )
    parser.add_argument("--cid-column", default="ipfs_cid", help="CSV column containing CIDs.")
    parser.add_argument("--id-column", default="id", help="CSV column used in output filenames.")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout per gateway request in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retry rounds across all gateways.")
    parser.add_argument("--limit", type=int, default=0, help="Download only the first N rows. 0 means all rows.")
    parser.add_argument("--force", action="store_true", help="Download again even if files already exist.")
    return parser.parse_args()


def main():
    args = parse_args()
    csv_path = Path(args.csv_file).expanduser().resolve()

    if not csv_path.exists():
        print(f"CSV file not found: {csv_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else csv_path.with_name("downloaded_ipfs_files")
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "download_report.csv"
    total = 0
    failed = 0
    skipped = 0
    downloaded = 0

    with csv_path.open("r", newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)

        if args.cid_column not in (reader.fieldnames or []):
            print(f"CID column '{args.cid_column}' was not found in {csv_path}", file=sys.stderr)
            print(f"Available columns: {', '.join(reader.fieldnames or [])}", file=sys.stderr)
            return 1

        rows = list(reader)
        if args.limit > 0:
            rows = rows[: args.limit]

    with report_path.open("w", newline="", encoding="utf-8") as report_file:
        fieldnames = ["id", "cid", "status", "path", "gateway", "verified", "error"]
        writer = csv.DictWriter(report_file, fieldnames=fieldnames)
        writer.writeheader()

        for index, row in enumerate(rows, start=1):
            total += 1
            result = download_cid(row, index, args, output_dir)
            writer.writerow(result)
            report_file.flush()

            if result["status"] == "downloaded":
                downloaded += 1
            elif result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1

            print(
                f"[{index}/{len(rows)}] id={result['id']} {result['status']}: "
                f"{result['path'] or result['error']}"
            )

    print("")
    print(f"Done. Downloaded: {downloaded}, skipped: {skipped}, failed: {failed}")
    print(f"Files folder: {output_dir}")
    print(f"Report: {report_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
