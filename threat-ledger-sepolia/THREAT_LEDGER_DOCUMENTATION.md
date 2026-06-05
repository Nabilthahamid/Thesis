# Threat Ledger Full Documentation

This document explains the complete Sepolia + IPFS threat-ledger system in plain language. It is written so a new person can understand what the project does, why each part exists, and how to run the workflow.

## 1. Big Picture

The system stores attack dataset updates in a decentralized way.

Large files and datasets are not stored directly on blockchain. Instead:

```text
Attack records or images
    -> uploaded to IPFS
    -> IPFS returns CIDs
    -> CIDs are written into a manifest CSV
    -> manifest CSV is uploaded to IPFS
    -> manifest CSV CID is stored on Sepolia blockchain
    -> daemon reads blockchain, downloads IPFS data, verifies it, and prepares training batch
```

The blockchain stores only small proof/metadata:

```text
version
manifest CID
manifest SHA-256
row count
timestamp
proposer address
confirmed status
```

This is cheaper and more scalable than putting thousands of CSV rows on-chain.

## 2. Project Location

Main project:

```text
D:\Thesisssss\threat-ledger-sepolia
```

Important files:

```text
contracts\ThreatLedger.sol              Smart contract
scripts\deploy.js                       Deploy contract to Sepolia
scripts\register-manifest.js            Register new manifest version
scripts\confirm-manifest.js             Approve/confirm a manifest
scripts\read-latest.js                  Read latest ledger state
tools\upload_manifest_to_ipfs.py        Upload manifest CSV to IPFS
daemon\threat_daemon.py                 Download/verify confirmed updates
daemon\approver_notifier.py             Notify when approval is needed
abi\ThreatLedger.json                   Small ABI used by Python scripts
.env                                    Local secrets/config, do not share
```

Generated files:

```text
artifacts\
cache\
node_modules\
```

Do not edit generated files manually.

## 3. Current Deployed Demo State

Sepolia contract:

```text
0x0feA4dB9Bd1933d7F69b73627e5C8CE56B413C1A
```

Approver/demo wallet:

```text
0x774389F22753F169a0DebE7D3c0Df3773070Cc17
```

Current blockchain state:

```text
latest registered version = 2
latest confirmed version = 2
```

Version 1:

```text
Manifest CID: bafkreiemkvfnty6nwu4acvxxlqgowpxkthxtw6ffpddahodanxyfffib4i
SHA-256:      0x8c554ad9e3cdb5380156f75c0ceb3eea99ef3b78a578c603b8606df0529501e2
Rows:         280
Confirmed:    true
```

Version 2:

```text
Manifest CID: bafkreiazmkg3y3x2uuxm6uznf6mqb2ysghg557bew4sl64wdj26oeyuwxa
SHA-256:      0x19628dbc6efaa52ecf532d2f9900eb1231cddefc24b724bf72c34ebce26296b8
Rows:         251
Confirmed:    true
```

Version 2 local files:

```text
D:\Thesisssss\new_information.csv
D:\Thesisssss\ipfs_cid_outputs\new_information_cids.csv
D:\Thesisssss\ipfs_cid_outputs\new_information_with_cids.csv
```

## 4. Environment Variables

Config is stored in:

```text
D:\Thesisssss\threat-ledger-sepolia\.env
```

Required fields:

```env
SEPOLIA_RPC_URL=
PRIVATE_KEY=
APPROVER_ADDRESS=
THREAT_LEDGER_ADDRESS=
TRAINING_COMMAND=
```

Meaning:

```text
SEPOLIA_RPC_URL       Sepolia RPC endpoint from Infura/Alchemy/etc.
PRIVATE_KEY           Private key for the test wallet that sends transactions
APPROVER_ADDRESS      Wallet allowed to confirm manifests
THREAT_LEDGER_ADDRESS Deployed ThreatLedger smart contract address
TRAINING_COMMAND      Optional command to run after daemon verifies a batch
```

Security warning:

```text
Never share PRIVATE_KEY.
Do not commit .env.
Use only a test wallet for this demo.
```

## 5. Smart Contract Logic

The smart contract is:

```text
contracts\ThreatLedger.sol
```

The central data structure is:

```solidity
struct Manifest {
    uint256 version;
    string manifestCid;
    bytes32 manifestSha256;
    uint256 rowCount;
    uint256 timestamp;
    address proposer;
    bool confirmed;
}
```

### Owner

The owner registers a new manifest:

```solidity
registerManifest(version, manifestCid, manifestSha256, rowCount)
```

This means:

```text
"Here is a new dataset update proposal."
```

The manifest starts as:

```text
confirmed = false
```

### Approver

The approver confirms a manifest:

```solidity
confirmManifest(version)
```

Only this address can approve:

```solidity
require(msg.sender == approver)
```

After confirmation:

```text
confirmed = true
latestConfirmedVersion = version
```

The daemon trusts only confirmed versions.

### Why Confirmation Exists

Without confirmation, anyone with owner access could accidentally or maliciously register bad data and the daemon might use it for training.

With confirmation:

```text
registered manifest = proposed update
confirmed manifest = approved/canonical update
```

## 6. IPFS Data Model

There are two IPFS levels.

### Level 1: Attack Records

Each CSV row from a raw dataset is uploaded as a JSON object to IPFS.

Example row output:

```text
id=0 -> bafkreic7urafysnqyox4twe43bicbolyahwwdt2q6eb6e3zxneystwpqly
```

### Level 2: Manifest CSV

The CIDs are collected into a manifest CSV:

```csv
id,ipfs_cid
0,bafkreic7urafysnqyox4twe43bicbolyahwwdt2q6eb6e3zxneystwpqly
1,bafkreiax5hravghirhwfg27ymtmvora2uawnen7odf7kpdwjghtro5owvu
```

Then this manifest CSV is uploaded to IPFS too.

The smart contract stores the manifest CSV CID, not every row.

## 7. SHA-256 Verification

The SHA-256 hash is calculated from the exact bytes of the manifest CSV.

Python code:

```python
data = path.read_bytes()
hashlib.sha256(data).hexdigest()
```

The hash is stored on blockchain as `manifestSha256`.

When the daemon later downloads the manifest from IPFS:

```text
download manifest CSV
calculate SHA-256 again
compare with blockchain SHA-256
if match, continue
if mismatch, reject
```

This protects the training pipeline from corrupted or wrong manifest files.

## 8. Deploy Flow

Install dependencies:

```powershell
cd D:\Thesisssss\threat-ledger-sepolia
npm install
pip install -r requirements.txt
```

Deploy contract:

```powershell
npm run deploy:sepolia
```

The deploy script prints:

```text
ThreatLedger deployed to: 0x...
```

Copy that into `.env`:

```env
THREAT_LEDGER_ADDRESS=0x...
```

Check contract:

```powershell
npm run latest:sepolia
```

## 9. Initial Version Flow

Initial manifest source:

```text
D:\Thesisssss\JailBreakV_28K\JailBreakV_28k\ipfs_cid_outputs\mini_JailBreakV_28K_cids.csv
```

Upload manifest CSV to IPFS:

```powershell
python tools\upload_manifest_to_ipfs.py "D:\Thesisssss\JailBreakV_28K\JailBreakV_28k\ipfs_cid_outputs\mini_JailBreakV_28K_cids.csv"
```

Set returned CID and version:

```powershell
$env:MANIFEST_CID="bafkreiemkvfnty6nwu4acvxxlqgowpxkthxtw6ffpddahodanxyfffib4i"
$env:MANIFEST_VERSION="1"
```

Register:

```powershell
npm run register:sepolia
```

Confirm:

```powershell
npm run confirm:sepolia
```

Check:

```powershell
npm run latest:sepolia
```

## 10. Update Version Flow

Version 2 used this raw CSV:

```text
D:\Thesisssss\new_information.csv
```

Because it did not already contain IPFS CIDs, each row was uploaded first:

```powershell
python "D:\Thesisssss\scripts\upload_csv_rows_to_ipfs.py" "D:\Thesisssss\new_information.csv" --output-csv "D:\Thesisssss\ipfs_cid_outputs\new_information_with_cids.csv" --cid-map "D:\Thesisssss\ipfs_cid_outputs\new_information_cids.csv" --checkpoint-every 25 --timeout 120
```

This created:

```text
D:\Thesisssss\ipfs_cid_outputs\new_information_cids.csv
D:\Thesisssss\ipfs_cid_outputs\new_information_with_cids.csv
```

Then the v2 manifest was uploaded:

```powershell
python tools\upload_manifest_to_ipfs.py "D:\Thesisssss\ipfs_cid_outputs\new_information_cids.csv"
```

Set v2 values:

```powershell
$env:MANIFEST_CSV="D:\Thesisssss\ipfs_cid_outputs\new_information_cids.csv"
$env:MANIFEST_CID="bafkreiazmkg3y3x2uuxm6uznf6mqb2ysghg557bew4sl64wdj26oeyuwxa"
$env:MANIFEST_VERSION="2"
```

Register:

```powershell
npm run register:sepolia
```

Confirm:

```powershell
npm run confirm:sepolia
```

Result:

```text
latest registered version = 2
latest confirmed version = 2
```

## 11. Approver Notifier

The notifier watches for registered manifests that are not confirmed yet.

Run once:

```powershell
python daemon\approver_notifier.py --once
```

Run continuously:

```powershell
python daemon\approver_notifier.py --poll-interval 60 --rpc-retries 5 --rpc-retry-delay 10
```

If there is a pending update, it prints something like:

```text
Approval needed
Version:      2
Manifest CID: ...
Rows:         ...

Approve with:
$env:MANIFEST_VERSION="2"
npm run confirm:sepolia
```

This script does not approve automatically. It only notifies.

## 12. Daemon Flow

The daemon is the automated bridge between blockchain/IPFS and training.

Run once:

```powershell
python daemon\threat_daemon.py --once
```

Run continuously:

```powershell
python daemon\threat_daemon.py --poll-interval 60
```

Daemon steps:

```text
1. Load .env
2. Connect to Sepolia
3. Read latestConfirmedVersion
4. Compare with daemon_state.json
5. For each unprocessed confirmed version:
   a. Read manifest CID from blockchain
   b. Download manifest CSV from IPFS
   c. Verify manifest SHA-256
   d. Parse id,ipfs_cid rows
   e. Download each record CID from IPFS
   f. Verify each downloaded file against CID
   g. Write download_report.csv
   h. Run TRAINING_COMMAND if set
   i. Save daemon_state.json
```

Output folder:

```text
D:\Thesisssss\threat-ledger-sepolia\training_batches
```

Expected batch structure:

```text
training_batches\
  daemon_state.json
  version_1\
    manifest.csv
    download_report.csv
    files\
  version_2\
    manifest.csv
    download_report.csv
    files\
```

## 13. Training Command

The daemon runs `TRAINING_COMMAND` only after all downloads for a version succeed.

Example:

```env
TRAINING_COMMAND=python D:\Thesisssss\train.py --batch "%BATCH_DIR%"
```

The daemon provides:

```text
BATCH_DIR      Folder for the verified version batch
MANIFEST_PATH  Downloaded manifest path
VERSION        Confirmed version number
```

Important:

```text
D:\Thesisssss\train.py currently must exist if TRAINING_COMMAND points to it.
If it does not exist, clear TRAINING_COMMAND before running the daemon.
```

Download-only test:

```powershell
$env:TRAINING_COMMAND=""
python daemon\threat_daemon.py --once
```

## 14. Why The Daemon Did Not Run Automatically

Blockchain cannot run local Python or AI training by itself.

Blockchain only records:

```text
version 2 confirmed
```

The daemon must be running on a computer/server to react:

```text
confirmed version appears on-chain
daemon sees it
daemon downloads IPFS data
daemon triggers training
```

If the daemon is not running, the update is still safely stored and confirmed, but no local download/training happens.

## 15. Troubleshooting

### RPC timeout or RemoteDisconnected

Sometimes Infura/Sepolia closes a connection temporarily.

Symptoms:

```text
ConnectTimeoutError
Remote end closed connection without response
```

Fix:

```powershell
python daemon\approver_notifier.py --poll-interval 60 --rpc-retries 5 --rpc-retry-delay 10
```

Or rerun the Hardhat command after a short wait.

### IPFS gateway 504

Public gateways may be slow to find freshly uploaded content.

Try another gateway:

```text
https://dweb.link/ipfs/<CID>
https://gateway.pinata.cloud/ipfs/<CID>
https://ipfs.io/ipfs/<CID>
```

Check local IPFS:

```powershell
curl.exe -X POST "http://127.0.0.1:5001/api/v0/block/stat?arg=<CID>"
```

### No Sepolia ETH

Deployment/register/confirm transactions need Sepolia ETH.

Use a Sepolia faucet, then check balance:

```powershell
npm run latest:sepolia
```

### Duplicate manifest rejected

The contract rejects the same manifest SHA-256 twice.

Reason:

```text
The same CSV should not be registered as a new version.
```

Create a new manifest CSV for a real update.

## 16. Security and Limitations

This is a thesis/demo system on Sepolia.

Important points:

```text
Sepolia is a testnet, not production permanence.
IPFS data must be pinned or it may disappear.
Private keys must never be shared.
Attack data on IPFS should be treated as public.
The current demo uses one wallet as owner and approver.
Production governance should use multiple independent approvers.
```

Recommended production improvements:

```text
Use 2-of-3 or 3-of-5 approval.
Use a stable pinning service.
Use a stronger monitoring service for daemon uptime.
Use logs/alerts for failed downloads.
Use a real training pipeline command.
Use separate owner and approver wallets.
```

## 17. One-Sentence Thesis Summary

The system stores attack data in IPFS and stores only verified manifest fingerprints on Sepolia; an approval-controlled smart contract marks trusted versions, and a daemon watches the chain to download, verify, and prepare newly confirmed datasets for training.

