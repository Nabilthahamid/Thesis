import argparse
import csv
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "JailBreakV_28K"
    / "JailBreakV_28k"
    / "ipfs_cid_outputs"
    / "mini_JailBreakV_28K_cids.csv"
)


def parse_args():
    parser = argparse.ArgumentParser(description="Upload a manifest CSV to a local IPFS node.")
    parser.add_argument("manifest_csv", nargs="?", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--api", default="http://127.0.0.1:5001", help="Local IPFS API base URL.")
    parser.add_argument("--cid-version", choices=("0", "1"), default="1")
    parser.add_argument("--no-pin", action="store_true", help="Do not pin the manifest in the local IPFS node.")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def manifest_info(path):
    data = path.read_bytes()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if not reader.fieldnames or "id" not in reader.fieldnames or "ipfs_cid" not in reader.fieldnames:
            raise ValueError("Manifest CSV must contain id and ipfs_cid columns")

    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "row_count": len(rows),
        "bytes": data,
    }


def upload_file(api_base, path, data, cid_version, pin, timeout):
    boundary = "----ThreatLedgerManifestBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode("utf-8"),
            b"Content-Type: text/csv\r\n\r\n",
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )

    url = (
        f"{api_base.rstrip('/')}/api/v0/add"
        f"?cid-version={cid_version}&pin={'true' if pin else 'false'}"
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8")

    last = None
    for line in text.splitlines():
        if line.strip():
            last = json.loads(line)
    if not last or "Hash" not in last:
        raise ValueError(f"Unexpected IPFS response: {text}")
    return last["Hash"]


def main():
    args = parse_args()
    path = Path(args.manifest_csv).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    info = manifest_info(path)
    cid = upload_file(args.api, path, info["bytes"], args.cid_version, not args.no_pin, args.timeout)

    print(json.dumps({
        "manifest_cid": cid,
        "manifest_sha256": "0x" + info["sha256"],
        "row_count": info["row_count"],
        "path": info["path"],
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach IPFS API. Is IPFS Desktop/daemon running? {exc}") from exc

