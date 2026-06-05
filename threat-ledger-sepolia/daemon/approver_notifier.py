import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from web3 import Web3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ABI_PATH = PROJECT_ROOT / "abi" / "ThreatLedger.json"
DEFAULT_STATE = PROJECT_ROOT / "training_batches" / "approver_notifier_state.json"


def load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Notify the approver when Sepolia manifests are registered but not confirmed."
    )
    parser.add_argument("--once", action="store_true", help="Check once and exit.")
    parser.add_argument("--poll-interval", type=int, default=60, help="Seconds between checks.")
    parser.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE),
        help="JSON file used to remember already displayed notifications.",
    )
    parser.add_argument(
        "--repeat",
        action="store_true",
        help="Print pending approvals on every poll instead of only once per version.",
    )
    parser.add_argument("--beep", action="store_true", help="Print a terminal bell when approval is needed.")
    parser.add_argument("--rpc-retries", type=int, default=3, help="Retry failed RPC reads this many times.")
    parser.add_argument("--rpc-retry-delay", type=int, default=5, help="Seconds to wait between RPC retries.")
    return parser.parse_args()


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_state(path):
    if not path.exists():
        return {"notified_versions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp_path.replace(path)


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


def format_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def print_notification(manifest, beep):
    if beep:
        print("\a", end="")

    version = manifest["version"]
    print("")
    print("Approval needed")
    print(f"Version:      {version}")
    print(f"Manifest CID: {manifest['manifest_cid']}")
    print(f"SHA-256:      {manifest['manifest_sha256']}")
    print(f"Rows:         {manifest['row_count']}")
    print(f"Proposer:     {manifest['proposer']}")
    print(f"Registered:   {format_timestamp(manifest['timestamp'])}")
    print("")
    print("Approve with:")
    print(f'$env:MANIFEST_VERSION="{version}"')
    print("npm run confirm:sepolia")


def retry_rpc(label, callback, retries, retry_delay):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return callback()
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                print(f"RPC read failed during {label}; retrying {attempt}/{retries - 1}: {exc}")
                time.sleep(retry_delay)
    raise last_error


def find_pending_manifests(contract, retries, retry_delay):
    latest_version = int(
        retry_rpc(
            "latestVersion",
            lambda: contract.functions.latestVersion().call(),
            retries,
            retry_delay,
        )
    )
    latest_confirmed = int(
        retry_rpc(
            "latestConfirmedVersion",
            lambda: contract.functions.latestConfirmedVersion().call(),
            retries,
            retry_delay,
        )
    )
    pending = []

    for version in range(latest_confirmed + 1, latest_version + 1):
        manifest = normalize_manifest(
            retry_rpc(
                f"getManifest({version})",
                lambda version=version: contract.functions.getManifest(version).call(),
                retries,
                retry_delay,
            )
        )
        if not manifest["confirmed"]:
            pending.append(manifest)

    return latest_version, latest_confirmed, pending


def main():
    args = parse_args()
    state_path = Path(args.state_file).expanduser().resolve()
    contract = connect_contract()

    while True:
        state = load_state(state_path)
        notified = {int(version) for version in state.get("notified_versions", [])}

        try:
            latest_version, latest_confirmed, pending = find_pending_manifests(
                contract,
                args.rpc_retries,
                args.rpc_retry_delay,
            )
        except Exception as exc:
            print(f"RPC connection problem; notifier will retry later: {exc}")
            if args.once:
                return 1
            time.sleep(args.poll_interval)
            continue
        new_notifications = []

        for manifest in pending:
            if args.repeat or manifest["version"] not in notified:
                print_notification(manifest, args.beep)
                new_notifications.append(manifest["version"])

        if new_notifications and not args.repeat:
            notified.update(new_notifications)
            state["notified_versions"] = sorted(notified)
            save_state(state_path, state)

        if not pending:
            print(f"No pending approvals. latest_registered={latest_version}, latest_confirmed={latest_confirmed}")
        elif not new_notifications:
            print(f"Pending approvals already notified. pending={len(pending)}")

        if args.once:
            break
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Notifier stopped.")
    except Exception as exc:
        print(f"Notifier failed: {exc}", file=sys.stderr)
        raise
