# V-AIS Project Workflow Overview

This file explains the full project in plain language: what the system does,
why each part exists, how data moves through the pipeline, and which files are
responsible for each task.

## 1. What This Project Does

This project builds a verifiable security update pipeline for a multimodal AI
model, DeepSeek-VL.

The problem is that multimodal models can be attacked using images. An attacker
can place malicious instructions inside or alongside an image, and the model may
follow the attack instead of behaving safely.

The project solves this with a repeatable security cycle:

```text
Attack data is collected
    -> attack records are uploaded to IPFS
    -> IPFS CIDs are recorded in a manifest
    -> manifest CID is registered on Sepolia blockchain
    -> approved manifest is downloaded by a daemon
    -> verified data is used for LoRA fine-tuning
    -> model becomes more resistant to attacks
    -> results and adapter files can be verified
```

In simple terms: the blockchain records what attacks are trusted, IPFS stores
the actual data, and Phase 4 fine-tunes the model to resist those attacks.

## 2. Why IPFS Is Used

Blockchain is good for storing small, permanent, tamper-resistant records. It is
not good for storing large files such as images, CSV datasets, or model files.
Putting full images or datasets directly on-chain would be too expensive.

IPFS solves this by storing the large files off-chain.

When a file is uploaded to IPFS, IPFS returns a CID, or Content Identifier. The
CID is calculated from the file content itself. This means:

```text
same file      -> same CID
changed file   -> different CID
```

So if someone modifies even one byte of an attack record, the CID will no longer
match. This gives the system a simple verification rule:

```text
download file from IPFS
check that its content matches the recorded CID/hash
only then use it for training
```

In this project, IPFS stores the actual dataset records, while the blockchain
stores only the CID and metadata needed to verify them.

## 3. Why Blockchain Is Used

The blockchain is used as a public trust layer.

Without blockchain, a normal database owner could secretly edit or delete attack
records. With Sepolia/Ethereum smart contracts, every registered manifest has a
public transaction history.

The smart contract records:

```text
manifest version
manifest CID
manifest SHA-256 hash
row count
timestamp
proposer address
confirmation status
```

The important idea is that the training system should not blindly trust local
files. It should train only from a confirmed, publicly recorded manifest.

## 4. Full Workflow

### Phase 1: Attack Dataset

The starting dataset is:

```text
JailBreakV_28K/JailBreakV_28k/JailBreakV_28K.csv
```

This CSV contains attack rows. Each row includes fields such as:

```text
id
jailbreak_query
redteam_query
format
policy
image_path
from
```

The images are stored locally inside:

```text
JailBreakV_28K/JailBreakV_28k/
```

The attack dataset is used later by Phase 4 to teach the model safer behavior.

### Phase 2: Upload Rows To IPFS

Rows from a CSV can be uploaded to IPFS using:

```text
scripts/upload_csv_rows_to_ipfs.py
```

This script reads a CSV and uploads each row as a JSON object to IPFS. It writes
CID output files such as:

```text
ipfs_cid_outputs/new_information_cids.csv
ipfs_cid_outputs/new_information_with_cids.csv
```

The important output is a manifest-style CSV:

```csv
id,ipfs_cid
0,bafkre...
1,bafkre...
```

Each `ipfs_cid` points to one uploaded attack record.

### Phase 3: Register Manifest On Blockchain

The manifest CSV is uploaded to IPFS using:

```text
threat-ledger-sepolia/tools/upload_manifest_to_ipfs.py
```

This produces:

```text
manifest CID
manifest SHA-256
row count
```

Then the manifest is registered and confirmed on Sepolia using the smart
contract scripts:

```text
threat-ledger-sepolia/scripts/register-manifest.js
threat-ledger-sepolia/scripts/confirm-manifest.js
threat-ledger-sepolia/scripts/read-latest.js
```

The smart contract is:

```text
threat-ledger-sepolia/contracts/ThreatLedger.sol
```

It stores the manifest metadata and tracks which manifest versions are confirmed.

### Phase 4: Daemon Downloads Verified Data

The daemon is:

```text
threat-ledger-sepolia/daemon/threat_daemon.py
```

It watches the blockchain for confirmed manifests. When it finds a new confirmed
version, it:

```text
1. Reads latest confirmed version from Sepolia
2. Downloads the manifest CSV from IPFS
3. Checks the manifest SHA-256 against the blockchain value
4. Reads all row CIDs from the manifest
5. Downloads every row from IPFS
6. Verifies downloaded content
7. Writes a local training batch
8. Runs TRAINING_COMMAND if configured
```

The downloaded batch is written to:

```text
threat-ledger-sepolia/training_batches/version_<n>/
```

If `.env` contains `TRAINING_COMMAND`, the daemon can automatically trigger
Phase 4 training after a verified batch is downloaded.

## 5. Phase 4: Model Hardening With LoRA

Phase 4 is implemented in:

```text
phase4/
```

The goal is to fine-tune DeepSeek-VL so it learns to resist the recorded attacks
without retraining the entire model.

### Why LoRA Is Used

DeepSeek-VL has many parameters. Full retraining is expensive and slow. LoRA
solves this by training only small adapter weights.

The base model mostly stays frozen. The LoRA adapter acts like a small security
patch.

In this project, LoRA targets:

```text
aligner layers
q_proj layers
v_proj layers
```

The `aligner` connects visual embeddings to the language model, so it is closely
related to multimodal behavior. The `q_proj` and `v_proj` layers are part of the
language model attention mechanism.

The vision model is frozen during training.

### Phase 4 Dataset Preparation

The data preparation script is:

```text
phase4/prepare_sft_data.py
```

It converts attack CSV rows into supervised fine-tuning examples.

For an attack row, it creates:

```text
image  = image_path from the CSV
prompt = <image_placeholder> + jailbreak_query
answer = safe refusal with short visible reasoning
```

Example target answer style:

```text
I can see that the image or prompt is trying to elicit unsafe guidance.
I will not provide harmful instructions. I can help with safe alternatives.
```

The script also adds benign VQAv2 examples to reduce catastrophic forgetting.
The ratio used in the current experiment is:

```text
1 attack : 4 benign
```

For the completed local experiment, the prepared dataset is:

```text
phase4_outputs/data/jailbreakv_2k_8k/
```

It contains:

```text
2,000 attack samples
8,000 benign samples
10,000 total samples
```

Split:

```text
train: 1,400 attack + 5,600 benign = 7,000
val:     200 attack +   800 benign = 1,000
test:    400 attack + 1,600 benign = 2,000
```

The files used by the code are:

```text
train.jsonl
val.jsonl
test.jsonl
dataset_metadata.json
```

An easy-to-read CSV export was also created:

```text
phase4_outputs/data/jailbreakv_2k_8k/jailbreakv_2k_8k_used_dataset.csv
```

That CSV is for inspection and documentation. The training code uses the JSONL
files.

### Phase 4 Training

The training script is:

```text
phase4/train_lora.py
```

It loads DeepSeek-VL, prepares image/text batches, applies LoRA, and trains the
adapter.

The local completed run used:

```text
model: deepseek-ai/deepseek-vl-1.3b-chat
method: LoRA
quantization: no QLoRA
epochs: 1
train samples: 7,000
validation samples: 1,000
```

The command was:

```bat
python phase4\train_lora.py --model-name deepseek-ai/deepseek-vl-1.3b-chat --deepseek-vl-path DeepSeek-VL --train-jsonl phase4_outputs\data\jailbreakv_2k_8k\train.jsonl --val-jsonl phase4_outputs\data\jailbreakv_2k_8k\val.jsonl --dataset-metadata phase4_outputs\data\jailbreakv_2k_8k\dataset_metadata.json --output-dir phase4_outputs\adapters\jailbreakv_2k_8k_1.3b --epochs 1 --batch-size 1 --grad-accum-steps 4 --dtype fp16 --no-qlora
```

The adapter output is:

```text
phase4_outputs/adapters/jailbreakv_2k_8k_1.3b/
```

Important files:

```text
adapter_model.safetensors
adapter_config.json
training_metrics.json
run_config.json
dataset_metadata.json
processor/
```

Training result:

```text
train_loss = 0.9525
val_loss   = 0.4716
optimizer_steps = 1750
```

### Phase 4 Evaluation

The evaluation script is:

```text
phase4/eval_attacks.py
```

It evaluates two versions:

```text
1. Base model without LoRA
2. Base model + LoRA adapter
```

The base model evaluation used no `--adapter-path`, so it tested the original
downloaded DeepSeek-VL chat model.

The LoRA evaluation used:

```text
--adapter-path phase4_outputs/adapters/jailbreakv_2k_8k_1.3b
```

Metrics:

```text
ASR = attack success rate
Defense success rate = 1 - ASR
False refusal rate = benign answers incorrectly refused
Benign answer match = simple answer match on benign VQA examples
```

Current results:

```text
Base model without LoRA:
  attacks tested: 400
  successful attacks: 58
  ASR: 14.5%
  defense success rate: 85.5%
  false refusal rate: 1.94%
  benign answer match: 62.38%

LoRA-hardened model:
  attacks tested: 400
  successful attacks: 0
  ASR: 0.0%
  defense success rate: 100%
  false refusal rate: 0.0%
  benign answer match: 63.94%
```

This means the LoRA adapter reduced attack success from:

```text
58 / 400 attacks
```

to:

```text
0 / 400 attacks
```

under the current heuristic judge.

## 6. What Each Important File Does

### Root-Level / Dataset Files

```text
JailBreakV_28K/JailBreakV_28k/JailBreakV_28K.csv
```

Full original attack dataset.

```text
new_information.csv
```

Smaller update CSV used for ledger/IPFS testing.

```text
scripts/upload_csv_rows_to_ipfs.py
```

Uploads CSV rows or images to IPFS and writes CID output CSVs.

### Threat Ledger Files

```text
threat-ledger-sepolia/contracts/ThreatLedger.sol
```

Smart contract that stores manifest metadata and confirmation status.

```text
threat-ledger-sepolia/scripts/deploy.js
```

Deploys the smart contract.

```text
threat-ledger-sepolia/scripts/register-manifest.js
```

Registers a manifest CID and metadata on Sepolia.

```text
threat-ledger-sepolia/scripts/confirm-manifest.js
```

Confirms a registered manifest so the daemon can trust it.

```text
threat-ledger-sepolia/scripts/read-latest.js
```

Reads the latest registered and confirmed versions from the contract.

```text
threat-ledger-sepolia/tools/upload_manifest_to_ipfs.py
```

Uploads a manifest CSV to IPFS and computes its SHA-256 hash.

```text
threat-ledger-sepolia/daemon/threat_daemon.py
```

Watches the blockchain, downloads confirmed IPFS data, verifies it, and can
trigger training.

```text
threat-ledger-sepolia/daemon/approver_notifier.py
```

Notifies when a registered manifest still needs approval.

### Phase 4 Files

```text
phase4/prepare_sft_data.py
```

Creates training, validation, and test JSONL files from attack rows and benign
VQAv2 rows.

```text
phase4/train_lora.py
```

Fine-tunes DeepSeek-VL using LoRA.

```text
phase4/eval_attacks.py
```

Evaluates attack success rate and benign performance.

```text
phase4/run_phase4_from_batch.py
```

Bridge script that lets the daemon trigger Phase 4 from a verified ledger batch.

```text
phase4/common.py
```

Shared helper functions used by Phase 4 scripts.

```text
phase4/requirements.txt
```

Python dependencies for Phase 4.

```text
phase4/README.md
```

Command-focused Phase 4 usage guide.

## 7. How To Explain The Project In One Paragraph

This project creates a verifiable security update pipeline for DeepSeek-VL. New
attack records are uploaded to IPFS, and their CIDs are recorded on an Ethereum
Sepolia smart contract so the data source is public and tamper-evident. A daemon
monitors the contract, downloads confirmed attack records, verifies them, and
prepares them for training. Phase 4 then uses LoRA fine-tuning to create a small
security adapter that teaches the model to resist multimodal jailbreak attacks
while preserving normal visual question answering through a 1:4 attack-to-benign
training mixture. The resulting adapter and metrics provide evidence that the
model has been hardened against the recorded threats.

## 8. Important Thesis Notes

The completed local experiment used:

```text
DeepSeek-VL 1.3B chat model
LoRA fine-tuning
2,000 attack samples
8,000 benign samples
heuristic judge
```

The 7B command and QLoRA path are implemented, but 7B training did not complete
on the local RTX 3060 machine because of hardware limits.

Careful thesis wording:

```text
The full Phase 4 pipeline was implemented and validated locally using
DeepSeek-VL-1.3B. The same scripts target DeepSeek-VL-7B on larger cloud/Linux
GPU hardware.
```

Do not claim:

```text
The final 7B model was successfully trained locally.
```

Do claim:

```text
The LoRA hardening pipeline reduced attack success from 14.5% to 0.0% on the
held-out test split in the completed 1.3B local experiment.
```

