import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from web3 import Web3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ABI_PATH = PROJECT_ROOT / "abi" / "ThreatLedger.json"
STATE_FILENAME = "daemon_state.json"

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


def load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_args():
    parser = argparse.ArgumentParser(description="Watch Sepolia ThreatLedger and download confirmed IPFS batches.")
    parser.add_argument("--once", action="store_true", help="Check once and exit.")
    parser.add_argument("--poll-interval", type=int, default=60, help="Seconds between blockchain checks.")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "training_batches"))
    parser.add_argument("--timeout", type=int, default=60, help="Timeout per IPFS gateway request.")
    parser.add_argument("--retries", type=int, default=2, help="Retry rounds across all gateways.")
    parser.add_argument("--limit", type=int, default=0, help="Download only first N rows per manifest. 0 means all.")
    parser.add_argument(
        "--all-confirmed",
        action="store_true",
        help="Process every unprocessed confirmed version. Default is latest confirmed version only.",
    )
    parser.add_argument(
        "--skip-training",
        action="store_true",
        help="Download and verify only; do not run TRAINING_COMMAND.",
    )
    return parser.parse_args()


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def safe_filename_part(value):
    value = str(value or "").strip() or "row"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def guess_extension(content_type):
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not content_type:
        return ".bin"
    if content_type in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[content_type]
    guessed = mimetypes.guess_extension(content_type)
    return ".jpg" if guessed == ".jpe" else guessed or ".bin"


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

    if version == 1 and codec == 0x55 and hash_code == 0x12 and hash_size == 32:
        digest = cid_bytes[offset : offset + hash_size]
        if len(digest) == 32:
            return digest
    return None


def sha256_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.digest()


def sha256_hex(path):
    return "0x" + sha256_file(path).hex()


def load_state(output_dir):
    state_path = output_dir / STATE_FILENAME
    if not state_path.exists():
        return {"last_processed_version": 0}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(output_dir, state):
    state_path = output_dir / STATE_FILENAME
    temp_path = state_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp_path.replace(state_path)


def download_url(url, temp_path, timeout):
    request = urllib.request.Request(
        url,
        headers={"Accept": "*/*", "User-Agent": "ThreatLedgerDaemon/1.0"},
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


def download_cid(cid, target_base, timeout, retries, verify=True):
    last_error = ""
    expected_digest = expected_sha256_from_cid(cid) if verify else None

    existing = list(target_base.parent.glob(target_base.name + ".*"))
    for path in existing:
        if path.suffix != ".part" and path.is_file() and path.stat().st_size > 0:
            if expected_digest is None or sha256_file(path) == expected_digest:
                return {
                    "status": "skipped",
                    "path": str(path),
                    "gateway": "",
                    "verified": "yes" if expected_digest is not None else "not_checked",
                    "error": "Already downloaded",
                }

    for attempt in range(1, retries + 1):
        for gateway_template in GATEWAYS:
            url = gateway_template.format(cid=cid)
            temp_path = target_base.with_suffix(".part")
            try:
                if temp_path.exists():
                    temp_path.unlink()
                content_type = download_url(url, temp_path, timeout)

                verified = "not_checked"
                if expected_digest is not None:
                    if sha256_file(temp_path) != expected_digest:
                        temp_path.unlink(missing_ok=True)
                        raise ValueError("Downloaded bytes do not match the CID SHA-256 hash")
                    verified = "yes"

                final_path = target_base.with_suffix(guess_extension(content_type))
                temp_path.replace(final_path)
                return {
                    "status": "downloaded",
                    "path": str(final_path),
                    "gateway": url,
                    "verified": verified,
                    "error": "",
                }
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        if attempt < retries:
            time.sleep(min(5, attempt * 2))

    return {
        "status": "failed",
        "path": "",
        "gateway": "",
        "verified": "no",
        "error": last_error,
    }


def connect_contract():
    load_dotenv(PROJECT_ROOT / ".env")
    rpc_url = require_env("SEPOLIA_RPC_URL")
    contract_address = Web3.to_checksum_address(require_env("THREAT_LEDGER_ADDRESS"))

    with ABI_PATH.open("r", encoding="utf-8") as handle:
        abi = json.load(handle)

    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise RuntimeError("Could not connect to Sepolia RPC URL")
    return web3.eth.contract(address=contract_address, abi=abi)


def normalize_manifest(raw):
    sha_value = raw[2].hex() if hasattr(raw[2], "hex") else str(raw[2])
    if not sha_value.startswith("0x"):
        sha_value = "0x" + sha_value

    return {
        "version": int(raw[0]),
        "manifest_cid": raw[1],
        "manifest_sha256": sha_value,
        "row_count": int(raw[3]),
        "timestamp": int(raw[4]),
        "proposer": raw[5],
        "confirmed": bool(raw[6]),
    }


def read_manifest_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "id" not in reader.fieldnames or "ipfs_cid" not in reader.fieldnames:
            raise ValueError("Manifest CSV must contain id and ipfs_cid columns")
        return list(reader)


def run_training_command(command, version_dir, manifest_path, version):
    if not command:
        return

    env = os.environ.copy()
    env["BATCH_DIR"] = str(version_dir)
    env["MANIFEST_PATH"] = str(manifest_path)
    env["VERSION"] = str(version)

    print(f"Running training command for version {version}: {command}")
    subprocess.run(command, shell=True, cwd=version_dir, env=env, check=True)


def process_version(contract, version, output_dir, args):
    manifest = normalize_manifest(contract.functions.getManifest(version).call())
    if not manifest["confirmed"]:
        print(f"Version {version} is not confirmed; skipping.")
        return False

    version_dir = output_dir / f"version_{version}"
    files_dir = version_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    manifest_base = version_dir / "manifest"
    manifest_result = download_cid(
        manifest["manifest_cid"],
        manifest_base,
        args.timeout,
        args.retries,
        verify=False,
    )
    if manifest_result["status"] == "failed":
        print(f"Could not download manifest version {version}: {manifest_result['error']}")
        return False

    manifest_path = Path(manifest_result["path"])
    actual_sha = sha256_hex(manifest_path)
    if actual_sha.lower() != manifest["manifest_sha256"].lower():
        print(f"Manifest SHA mismatch for version {version}")
        print(f"Expected: {manifest['manifest_sha256']}")
        print(f"Actual:   {actual_sha}")
        return False

    rows = read_manifest_csv(manifest_path)
    if len(rows) != manifest["row_count"]:
        print(f"Manifest row count mismatch for version {version}: chain={manifest['row_count']} csv={len(rows)}")
        return False

    if args.limit > 0:
        rows = rows[: args.limit]

    report_path = version_dir / "download_report.csv"
    failed = 0
    with report_path.open("w", encoding="utf-8", newline="") as report_file:
        fieldnames = ["id", "cid", "status", "path", "gateway", "verified", "error"]
        writer = csv.DictWriter(report_file, fieldnames=fieldnames)
        writer.writeheader()

        for index, row in enumerate(rows, start=1):
            record_id = row.get("id", str(index)).strip()
            cid = row.get("ipfs_cid", "").strip()
            if not cid:
                result = {
                    "status": "failed",
                    "path": "",
                    "gateway": "",
                    "verified": "no",
                    "error": "Missing CID",
                }
            else:
                target_base = files_dir / f"id_{safe_filename_part(record_id)}_{safe_filename_part(cid)}"
                result = download_cid(cid, target_base, args.timeout, args.retries, verify=True)
            writer.writerow({
                "id": record_id,
                "cid": cid,
                **result,
            })
            report_file.flush()

            if result["status"] == "failed":
                failed += 1
            status_text = result["status"]
            if result["status"] == "failed" and result.get("error"):
                status_text = f"{status_text}: {result['error']}"
            print(f"[v{version} {index}/{len(rows)}] id={record_id} {status_text}")

    if failed:
        print(f"Version {version} has {failed} failed download(s). Training was not started.")
        return False

    if args.skip_training:
        print(f"Skipping training command for version {version}.")
    else:
        run_training_command(os.environ.get("TRAINING_COMMAND", "").strip(), version_dir, manifest_path, version)
    return True


def versions_to_process(last_processed, latest_confirmed, all_confirmed):
    if latest_confirmed <= last_processed:
        return []
    if all_confirmed:
        return range(last_processed + 1, latest_confirmed + 1)
    return [latest_confirmed]


def main():
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    contract = connect_contract()

    while True:
        state = load_state(output_dir)
        last_processed = int(state.get("last_processed_version", 0))
        latest_confirmed = int(contract.functions.latestConfirmedVersion().call())

        versions = versions_to_process(last_processed, latest_confirmed, args.all_confirmed)

        if not versions:
            print(f"No new confirmed manifests. last_processed={last_processed}, latest_confirmed={latest_confirmed}")
        else:
            if not args.all_confirmed:
                if last_processed == 0:
                    print(f"Latest-only mode: processing latest confirmed manifest version {latest_confirmed}.")
                elif latest_confirmed > last_processed + 1:
                    skipped_from = last_processed + 1
                    skipped_to = latest_confirmed - 1
                    print(
                        "Latest-only mode: "
                        f"skipping historical versions {skipped_from}-{skipped_to} "
                        f"and processing version {latest_confirmed}."
                    )
                else:
                    print(f"Latest-only mode: processing latest confirmed manifest version {latest_confirmed}.")

            for version in versions:
                print(f"Processing confirmed manifest version {version}")
                if process_version(contract, version, output_dir, args):
                    state["last_processed_version"] = version
                    save_state(output_dir, state)
                    print(f"Version {version} processed successfully.")
                else:
                    print(f"Version {version} was not completed. It will be retried later.")
                    break

        if args.once:
            break
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Daemon stopped.")
    except Exception as exc:
        print(f"Daemon failed: {exc}", file=sys.stderr)
        raise
