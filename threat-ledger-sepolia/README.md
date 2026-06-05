# Sepolia Threat Ledger

This demo stores only the IPFS CID of a manifest CSV on Sepolia. The manifest CSV stores the actual attack record CIDs:

For the full explanation of the architecture, code, update workflow, daemon, notifier, and troubleshooting, see:

```text
THREAT_LEDGER_DOCUMENTATION.md
```

```csv
id,ipfs_cid
0,bafkre...
1,bafkre...
```

Large files and records stay on IPFS. Sepolia stores the public, timestamped, confirmed manifest version.

## Setup

Install JavaScript dependencies:

```powershell
cd D:\Thesisssss\threat-ledger-sepolia
npm install
```

Install Python dependency for the daemon:

```powershell
pip install -r requirements.txt
```

Create `.env` from `.env.example`:

```text
SEPOLIA_RPC_URL=https://sepolia.infura.io/v3/YOUR_KEY
PRIVATE_KEY=YOUR_SEPOLIA_PRIVATE_KEY
APPROVER_ADDRESS=YOUR_APPROVER_WALLET_ADDRESS
THREAT_LEDGER_ADDRESS=
TRAINING_COMMAND=
```

For the 1-approver thesis demo, `APPROVER_ADDRESS` can be the same wallet as `PRIVATE_KEY`.

## Deploy to Sepolia

```powershell
npm run deploy:sepolia
```

Copy the printed contract address into `.env` as `THREAT_LEDGER_ADDRESS`.

## Upload the Manifest CSV to IPFS

Start IPFS Desktop or run an IPFS daemon, then upload the current manifest:

```powershell
python tools\upload_manifest_to_ipfs.py
```

The script prints:

```json
{
  "manifest_cid": "bafy...",
  "manifest_sha256": "0x...",
  "row_count": 280
}
```

## Register and Confirm Version 1

Set the printed CID and version as environment variables for the current PowerShell session:

```powershell
$env:MANIFEST_CID="bafy..."
$env:MANIFEST_VERSION="1"
```

Register it on Sepolia:

```powershell
npm run register:sepolia
```

Confirm it:

```powershell
npm run confirm:sepolia
```

Check the latest confirmed version:

```powershell
npm run latest:sepolia
```

## Run the Daemon

Run once:

```powershell
python daemon\threat_daemon.py --once
```

Run continuously:

```powershell
python daemon\threat_daemon.py --poll-interval 60
```

The daemon writes batches here:

```text
training_batches\version_<n>\
```

Each batch contains:

```text
manifest.csv
download_report.csv
daemon_state.json
files\
```

If `TRAINING_COMMAND` is set in `.env`, the daemon runs it after a full successful batch. The command receives these environment variables:

```text
BATCH_DIR
MANIFEST_PATH
VERSION
```

## Run the Approver Notifier

Use this when you want the approver wallet to be notified about registered manifests that are not confirmed yet:

```powershell
python daemon\approver_notifier.py --once
```

Run continuously:

```powershell
python daemon\approver_notifier.py --poll-interval 60 --beep
```

If the Sepolia RPC provider briefly disconnects, the notifier retries RPC reads and keeps running. You can tune that behavior:

```powershell
python daemon\approver_notifier.py --poll-interval 60 --rpc-retries 5 --rpc-retry-delay 10
```

When approval is needed, it prints the version and the exact confirm command:

```powershell
$env:MANIFEST_VERSION="2"
npm run confirm:sepolia
```

## Common Commands

Compute manifest SHA-256 and row count:

```powershell
npm run manifest:info
```

To use a different manifest path, set:

```powershell
$env:MANIFEST_CSV="D:\path\to\manifest.csv"
```

Run contract tests:

```powershell
npm test
```

Compile contracts:

```powershell
npm run compile
```
