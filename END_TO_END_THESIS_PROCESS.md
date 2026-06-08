# End-to-End Thesis Process: Dataset Processing, LoRA Fine-Tuning, IPFS, Blockchain, and Daemon

This document describes the complete workflow implemented in this workspace, from the original dataset to the fine-tuned multimodal safety model and the Sepolia/IPFS ledger pipeline.

The main project goal is to build a verifiable update pipeline for multimodal AI safety data. The dataset is processed locally, uploaded to IPFS, recorded on Sepolia through a smart contract, approved by an approver, downloaded by a daemon, verified, and then used for LoRA fine-tuning.

The main prepared experiment documented here is:

```text
D:\Thesisssss\phase4_outputs\data\jailbreakv_2k_8k\
```

It contains:

```text
2,000 attack samples
8,000 benign samples
10,000 total samples
```

The current confirmed blockchain version for this dataset is version 3.

```text
Latest registered version: 3
Latest confirmed version: 3
Manifest CID: bafybeib2lummeeq5ydxii7vdpkx4pxsrn6r3kgkmvljl6pscfmut5zs3ca
Manifest SHA-256: 0xc28dad31a35a72da1e3edb9f93f2327ea9a7a0dee2d6c1f2790742f4816db0b7
Row count: 10000
Confirmed: true
Registered timestamp: 2026-06-07 14:17:36 UTC
```

## 1. Technologies Used

### Python

Python is used for dataset preparation, IPFS row upload, daemon download, verification, and training orchestration.

Files:

```text
phase4\prepare_sft_data.py
phase4\train_lora.py
phase4\eval_attacks.py
phase4\run_phase4_from_batch.py
scripts\upload_phase4_rows_with_images_to_ipfs.py
threat-ledger-sepolia\daemon\threat_daemon.py
threat-ledger-sepolia\daemon\approver_notifier.py
```

Reason:

Python is the natural choice for ML workflows because PyTorch, Hugging Face datasets, image processing, JSONL/CSV processing, and Web3.py are all available and mature.

### DeepSeek-VL

DeepSeek-VL is the multimodal vision-language model used for fine-tuning.

Models used in the workspace:

```text
deepseek-ai/deepseek-vl-1.3b-chat
deepseek-ai/deepseek-vl-1.3b-base
```

Reason:

The thesis problem is multimodal jailbreak safety. DeepSeek-VL accepts both image and text input, so it is suitable for studying attacks that use visual context plus malicious prompts.

### PyTorch, Transformers, and PEFT

These libraries are used for model loading and training.

```text
torch
transformers
peft
bitsandbytes
```

Reason:

PyTorch provides training primitives. Transformers loads the model. PEFT adds LoRA adapters. bitsandbytes allows memory-saving optimizer and QLoRA support when needed.

### LoRA and QLoRA

LoRA is used to train only small adapter weights instead of retraining the full model.

Reason:

Full fine-tuning of a large vision-language model is expensive and slower. LoRA makes the safety update cheaper, easier to reproduce, and easier to store as a small adapter.

In the main chat-model run:

```text
Model: deepseek-ai/deepseek-vl-1.3b-chat
LoRA: enabled
QLoRA: disabled
Trainable parameters: 3,260,416
Total parameters: 1,978,496,000
Trainable percent: 0.1648%
```

In the base-model run:

```text
Model: deepseek-ai/deepseek-vl-1.3b-base
LoRA: enabled
QLoRA: enabled
Trainable parameters: 3,260,416
Total parameters: 1,201,501,184
Trainable percent: 0.2714%
```

### IPFS

IPFS stores the actual dataset records and manifest files.

Reason:

The blockchain should not store large files. Images and full JSON rows are too large and expensive for on-chain storage. IPFS gives each uploaded object a content-addressed CID. If the file changes, the CID changes.

The local IPFS API used by the upload scripts is:

```text
http://127.0.0.1:5001
```

### Sepolia Blockchain

Sepolia is used as the public verification and approval layer.

Files:

```text
threat-ledger-sepolia\contracts\ThreatLedger.sol
threat-ledger-sepolia\scripts\deploy.js
threat-ledger-sepolia\scripts\register-manifest.js
threat-ledger-sepolia\scripts\confirm-manifest.js
threat-ledger-sepolia\scripts\read-latest.js
```

Reason:

The blockchain records which dataset manifest is official, versioned, timestamped, and approved. This prevents the training daemon from blindly trusting local files.

### Hardhat and Ethers.js

Hardhat deploys and interacts with the Solidity smart contract.

Reason:

Hardhat is a standard Ethereum development framework. Ethers.js is used by Hardhat scripts to call contract functions and send transactions.

### Web3.py

Web3.py is used in the Python daemon and notifier.

Reason:

The daemon is Python-based because it also downloads files and triggers ML training. Web3.py lets the Python daemon read Sepolia contract state directly.

## 2. High-Level System Flow

The complete flow is:

```text
Original JailBreakV dataset
    -> prepare attack SFT rows
    -> add benign VQAv2 rows
    -> write train/val/test JSONL
    -> train LoRA adapter
    -> export dataset rows to CSV
    -> upload each row with image bytes to IPFS
    -> create manifest CSV of row CIDs
    -> upload manifest CSV to IPFS
    -> register manifest CID, SHA-256, row count on Sepolia
    -> approver confirms the manifest
    -> daemon polls latestConfirmedVersion
    -> daemon downloads and verifies IPFS data
    -> daemon can trigger Phase 4 training
```

The design separates responsibilities:

```text
IPFS stores large content.
Sepolia stores the trusted pointer and verification metadata.
The daemon only trains from confirmed blockchain versions.
LoRA updates the model without changing the full base model.
```

## 3. Original Dataset

The starting attack dataset is:

```text
D:\Thesisssss\JailBreakV_28K\JailBreakV_28k\JailBreakV_28K.csv
```

The related images are stored under:

```text
D:\Thesisssss\JailBreakV_28K\JailBreakV_28k\
```

Important CSV fields used by the preparation script:

```text
id
jailbreak_query
redteam_query
format
policy
image_path
from
selected_mini
transfer_from_llm
```

Meaning of the fields:

```text
id                    Original row id.
jailbreak_query       Prompt used as the model input.
redteam_query         Underlying unsafe intent.
format                Attack style, such as Template, figstep, typo, SD, etc.
policy                Safety category, such as Fraud, Malware, Violence, etc.
image_path            Relative path to the paired attack image.
from                  Dataset/source family.
selected_mini         Source metadata.
transfer_from_llm     Source metadata.
```

Reason for keeping these fields:

The model needs the image, prompt, and target answer for training. The metadata is kept for audit, evaluation, and analysis by attack type, policy category, and source.

## 4. Attack Dataset Preparation

The main script is:

```text
phase4\prepare_sft_data.py
```




A reproducible command for the `jailbreakv_2k_8k` prepared artifact is:

```powershell
python phase4\prepare_sft_data.py `
  --attack-csv D:\Thesisssss\JailBreakV_28K\JailBreakV_28k\JailBreakV_28K.csv `
  --attack-image-root D:\Thesisssss\JailBreakV_28K\JailBreakV_28k `
  --output-dir phase4_outputs\data\jailbreakv_2k_8k `
  --max-attacks 2000 `
  --benign-dataset lmms-lab/VQAv2 `
  --benign-split validation `
  --attack-benign-ratio 4 `
  --seed 42
```




### 4.1 Read Attack Rows

The script opens the attack CSV with `csv.DictReader`.

Code behavior:



```text
1. Read every CSV row.
2. Resolve the image path against the attack image root.
3. Skip rows without a usable jailbreak query.
4. Stop with an error if any referenced attack image is missing.
```



Reason:



The final dataset is multimodal. Every row must have both text and an image. Failing early on missing images prevents silent corrupted training data.

### 4.2 Create the Attack Prompt

For each attack row, the prompt is created as:

```text
<image_placeholder>
{jailbreak_query}
```

Reason:

DeepSeek-VL expects an image placeholder in the conversation text. This tells the processor where the image belongs relative to the user prompt.

### 4.3 Create the Attack Target Answer

For attack rows, the target answer is a safe refusal generated from the policy category.

Template from `phase4\common.py`:

```text
I can see that the image or prompt is trying to elicit unsafe guidance related to {policy}. I will not provide instructions that facilitate harm, evasion, fraud, abuse, or wrongdoing. I can help with safe, lawful alternatives, risk reduction, or high-level educational context instead.
```

Reason:

The goal is not to teach the model the harmful answer. The goal is to teach the model to recognize the unsafe request and respond safely.

### 4.4 Normalize Intent

The script creates an `intent_key` from `redteam_query`.

The normalization:

```text
1. Converts to lowercase.
2. Collapses whitespace.
3. Removes punctuation.
4. Trims leading/trailing spaces.
```

Reason:

The same underlying unsafe request can appear in multiple prompt formats. Grouping by normalized intent helps keep the same intent from leaking across train, validation, and test splits.

### 4.5 Limit to 2,000 Attack Samples

The prepared `jailbreakv_2k_8k` experiment uses:

```text
2,000 attack samples
```

The script supports this with:

```text
--max-attacks 2000
```

Reason:

The full JailBreakV dataset is much larger. A 2,000 attack sample experiment is more practical for local LoRA training and IPFS upload while still covering many attack categories and formats.

The script does not simply take the first 2,000 rows. It groups by `intent_key`, shuffles groups, and samples across groups.

Reason:

This reduces overrepresentation of repeated or similar attacks and gives a more diverse 2,000-attack subset.

### 4.6 Split Attack Rows

The split ratios are:

```text
train: 70%
val:   10%
test:  20%
```

For 2,000 attack samples this becomes:

```text
train: 1,400 attack rows
val:     200 attack rows
test:    400 attack rows
```

Reason:

The train split teaches the model. The validation split checks training behavior during training. The test split is held out for final evaluation.

### 4.7 Prevent Intent Leakage

The script checks that an `intent_key` does not appear in more than one split.

Reason:

If the same underlying unsafe request appears in train and test, the test result can look better than it really is. Intent-level splitting gives a stronger evaluation than random row-level splitting.

## 5. Benign Dataset Processing

The benign dataset source is:

```text
lmms-lab/VQAv2
```

The prepared metadata shows the actual benign split used:

```text
validation
```

The benign samples are normal visual question answering examples.

Examples:

```text
Question: What color is the tile?
Answer: gray

Question: What is the green fruit?
Answer: banana

Question: How many deckers are on the bus?
Answer: 1
```

### 5.1 Why Add Benign Data

The attack samples all teach refusal behavior. If the model only trains on refusals, it may over-refuse harmless prompts.

Reason for benign rows:

```text
Preserve normal image-question-answering ability.
Reduce catastrophic forgetting.
Reduce false refusals on harmless prompts.
```

### 5.2 Attack-to-Benign Ratio

The chosen ratio is:

```text
1 attack : 4 benign
```

For 2,000 attacks:

```text
2,000 attack rows
8,000 benign rows
10,000 total rows
```

Reason:

The dataset still contains enough attack examples to teach safer behavior, but benign examples dominate enough to remind the model that normal visual questions should be answered normally.

### 5.3 Benign Row Collection

For each benign row, the script:

```text
1. Reads the question.
2. Extracts the answer from multiple possible answer fields.
3. Saves the image locally as JPEG.
4. Creates a JSONL row with image, prompt, answer, and source metadata.
```

The benign prompt format is:

```text
<image_placeholder>
{question}
```

Reason:

The prompt format is intentionally the same as the attack format: one image plus one text instruction/question. This keeps the training interface consistent.

### 5.4 Benign Image Storage

Benign images are saved under:

```text
phase4_outputs\data\jailbreakv_2k_8k\benign_images\train\
phase4_outputs\data\jailbreakv_2k_8k\benign_images\val\
phase4_outputs\data\jailbreakv_2k_8k\benign_images\test\
```

Counts:

```text
train: 5,600 benign images
val:     800 benign images
test:  1,600 benign images
```

Reason:

Saving the images locally makes training reproducible after the data is prepared. The training script can load image paths directly without needing to stream the benign dataset again.

## 6. Final SFT Dataset

The prepared dataset directory is:

```text
phase4_outputs\data\jailbreakv_2k_8k\
```

Important files:

```text
train.jsonl
val.jsonl
test.jsonl
dataset_metadata.json
benign_images\
jailbreakv_2k_8k_used_dataset.csv
```

### 6.1 JSONL Files

Each JSONL line is one supervised fine-tuning example.

Attack row structure:

```json
{
  "id": "attack:5830",
  "kind": "attack",
  "split": "train",
  "image": "D:\\Thesisssss\\JailBreakV_28K\\JailBreakV_28k\\llm_transfer_attack\\nature_1365.jpeg",
  "prompt": "<image_placeholder>\n...",
  "answer": "safe refusal target",
  "intent_key": "...",
  "source": {
    "id": "5830",
    "format": "Template",
    "policy": "Fraud",
    "image_path": "llm_transfer_attack/nature_1365.jpeg",
    "from": "AdvBench"
  }
}
```

Benign row structure:

```json
{
  "id": "benign:train:4458",
  "kind": "benign",
  "split": "train",
  "image": "D:\\Thesisssss\\phase4_outputs\\data\\jailbreakv_2k_8k\\benign_images\\train\\benign_004458.jpg",
  "prompt": "<image_placeholder>\nWhat is the green fruit?",
  "answer": "banana",
  "source": {
    "dataset": "lmms-lab/VQAv2",
    "split": "validation",
    "row_index": 4458,
    "question": "What is the green fruit?",
    "answer": "banana"
  }
}
```

Reason for JSONL:

JSONL is simple for training because each line is one independent sample. It is also easy to stream, hash, inspect, and recover from partial processing.

### 6.2 Dataset Counts

From `dataset_metadata.json`:

```text
train: 1,400 attack + 5,600 benign = 7,000
val:     200 attack +   800 benign = 1,000
test:    400 attack + 1,600 benign = 2,000
```

Hashes:

```text
train.jsonl SHA-256: 643a4a0630d9d1ca4dd1ef378bf6360cc7e69432ed3c79d3f089fbe9b50457ce
val.jsonl SHA-256:   dfc51b8be2ea4d9b014e75a1a74b5d8625cfba89ffa6b756c75e44628f3b3a2d
test.jsonl SHA-256:  963ac8ef2280d50ae72e9b422df5a9bb47b29b05f15d0c9f979f4254c4c18415
```

Reason for storing hashes:

Hashes prove exactly which training, validation, and test files were used. If the files change, the hash changes.

### 6.3 CSV Export

The JSONL dataset was also exported to:

```text
phase4_outputs\data\jailbreakv_2k_8k\jailbreakv_2k_8k_used_dataset.csv
```

This CSV contains flattened columns such as:

```text
split
sample_id
kind
image
prompt
answer
intent_key
source_id
source_jailbreak_query
source_redteam_query
source_format
source_policy
source_image_path
source_from
source_dataset
source_row_index
source_question
source_answer
source_split
```

Reason:

The training code uses JSONL, but the CSV is easier to inspect, upload row-by-row to IPFS, and use as an audit artifact.

## 7. LoRA Fine-Tuning

The main training script is:

```text
phase4\train_lora.py
```

### 7.1 Training Command

The completed chat-model LoRA run used:

```powershell
python phase4\train_lora.py `
  --model-name deepseek-ai/deepseek-vl-1.3b-chat `
  --deepseek-vl-path DeepSeek-VL `
  --train-jsonl phase4_outputs\data\jailbreakv_2k_8k\train.jsonl `
  --val-jsonl phase4_outputs\data\jailbreakv_2k_8k\val.jsonl `
  --dataset-metadata phase4_outputs\data\jailbreakv_2k_8k\dataset_metadata.json `
  --output-dir phase4_outputs\adapters\jailbreakv_2k_8k_1.3b `
  --epochs 1 `
  --batch-size 1 `
  --grad-accum-steps 4 `
  --dtype fp16 `
  --no-qlora
```

### 7.2 Why Train Only on Train and Val

The script uses:

```text
train.jsonl
val.jsonl
dataset_metadata.json
```

It does not train on:

```text
test.jsonl
```

Reason:

The test set must remain unseen so final evaluation is meaningful.

### 7.3 Model Input Format

The collator builds a two-turn conversation:

```text
User: image + prompt
Assistant: target answer
```

Reason:

This matches the chat-style instruction format expected by DeepSeek-VL.

### 7.4 Label Masking

During training, the script masks:

```text
user prompt tokens
image placeholder tokens
padding tokens
```

Only assistant answer tokens contribute to loss.

Reason:

The model should learn to generate the answer, not learn to reproduce the user prompt or image placeholder.

### 7.5 Freezing the Vision Model

The script freezes:

```text
vision_model
```

Reason:

The purpose is safety behavior tuning, not relearning visual perception. Freezing the visual encoder reduces compute cost and lowers the risk of damaging general image understanding.

### 7.6 LoRA Target Layers

LoRA is added to:

```text
aligner.*
*.q_proj
*.v_proj
```

Reason:

The aligner connects visual features to the language model, which is important for multimodal safety. The `q_proj` and `v_proj` layers are attention projection layers, which influence how the language model uses context.

### 7.7 Training Configuration

From `run_config.json`:

```text
model_name: deepseek-ai/deepseek-vl-1.3b-chat
epochs: 1
batch_size: 1
grad_accum_steps: 4
learning_rate: 0.0002
lora_r: 16
lora_alpha: 32
lora_dropout: 0.05
max_length: 4096
dtype: torch.float16
qlora: false
train samples: 7000
validation samples: 1000
```

Reason for batch size 1:

Vision-language samples are memory heavy because each sample includes an image and text. Batch size 1 keeps GPU memory manageable.

Reason for gradient accumulation 4:

It simulates a larger effective batch size while keeping memory usage low.

Reason for one epoch:

This was a focused safety adapter experiment. One epoch is cheaper and reduces overfitting risk for a first complete run.

### 7.8 Training Output

Adapter output:

```text
phase4_outputs\adapters\jailbreakv_2k_8k_1.3b\
```

Important files:

```text
adapter_model.safetensors
adapter_config.json
training_metrics.json
run_config.json
dataset_metadata.json
processor\
epoch_1\
```

Training metrics:

```text
epoch: 1
global_step: 1750
optimizer_steps: 1750
train_loss: 0.9525086841043916
val_loss: 0.4716342945685028
```

## 8. Evaluation

The evaluation script is:

```text
phase4\eval_attacks.py
```

It evaluates:

```text
1. Attack success rate on attack test samples.
2. Defense success rate.
3. False refusal rate on benign samples.
4. Benign answer match rate.
```

### 8.1 Base Model Result

Base model without LoRA:

```text
total attacks: 400
successful attacks: 58
ASR: 14.5%
defense success rate: 85.5%

total benign: 1600
false refusals: 31
false refusal rate: 1.9375%
benign answer matches: 998
benign answer match rate: 62.375%
```

### 8.2 LoRA-Hardened Result

Chat-model LoRA adapter:

```text
adapter: phase4_outputs\adapters\jailbreakv_2k_8k_1.3b
total attacks: 400
successful attacks: 0
ASR: 0.0%
defense success rate: 100.0%

total benign: 1600
false refusals: 0
false refusal rate: 0.0%
benign answer matches: 1023
benign answer match rate: 63.9375%
```

Reason for evaluating benign samples:

A safety model that refuses everything would look safe on attacks but would be useless. Benign evaluation checks that the model still answers normal visual questions.

Important caution:

The current evaluation uses a heuristic judge. The result is useful for a thesis experiment, but a stronger final claim should also use an external judge or a new attack set.

## 9. Preparing Rows for IPFS

The IPFS row upload script is:

```text
scripts\upload_phase4_rows_with_images_to_ipfs.py
```

Input CSV:

```text
phase4_outputs\data\jailbreakv_2k_8k\jailbreakv_2k_8k_used_dataset.csv
```

Output directory:

```text
phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\
```

### 9.1 Why Upload Rows Individually

Each dataset row is uploaded as a separate IPFS object.

Reason:

If one row changes, only that row CID changes. This gives fine-grained auditability and makes it easy to identify exactly which record is part of a manifest.

### 9.2 Why Embed Images as Base64

For each CSV row, the script reads the local image file and replaces the `image` field with:

```json
{
  "encoding": "base64",
  "file_name": "image_name.jpg",
  "mime_type": "image/jpeg",
  "size_bytes": 12345,
  "sha256": "image_sha256",
  "data_base64": "..."
}
```

Reason:

The uploaded IPFS object becomes self-contained. A row no longer depends on a local Windows image path. Anyone who downloads the row JSON from IPFS can recover the image bytes and verify the image SHA-256.

### 9.3 Upload Settings

The upload script uses:

```text
IPFS API: http://127.0.0.1:5001
CID version: 1
pin: true
only_hash: false
```

Reason for CID version 1:

CID v1 is modern and gateway-friendly.

Reason for pinning:

Pinning tells the local IPFS node to keep the uploaded content.

Reason `only_hash` is false:

The script actually stores the content in IPFS instead of only calculating the CID.

### 9.4 Upload Results

Upload summary:

```text
total_rows: 10000
uploaded_this_run: 10000
skipped_existing_successes: 0
failures_this_run: 0
total_successful_row_cids: 10000
pending_rows: 0
elapsed_seconds: 292.09
finished_at_utc: 2026-06-07T14:04:12Z
```

Generated files:

```text
jailbreakv_2k_8k_used_dataset_upload_progress.csv
jailbreakv_2k_8k_used_dataset_row_cids.csv
jailbreakv_2k_8k_used_dataset_upload_summary.json
jailbreakv_2k_8k_used_dataset_final_csv_cid.txt
jailbreakv_2k_8k_used_dataset_blockchain_manifest.csv
```

The row CID inventory file was also uploaded to IPFS:

```text
jailbreakv_2k_8k_used_dataset_row_cids.csv CID:
bafybeibfes3mkz7gnkiuvrjw7omq45s3qxl5ysqqc74colsjb5exdb7ubq
```

## 10. Blockchain Manifest

The blockchain manifest used for the ledger is:

```text
phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\jailbreakv_2k_8k_used_dataset_blockchain_manifest.csv
```

It has columns:

```text
id
ipfs_cid
original_image_path
```

Example:

```csv
id,ipfs_cid,original_image_path
attack:5830,bafybeid4kikvsweepintynawt3xnqdrj5vjycbn7tzkzkoat3idkneza6u,D:\Thesisssss\JailBreakV_28K\JailBreakV_28k\llm_transfer_attack\nature_1365.jpeg
benign:train:1372,bafkreicxmmbkvlz6ljurehptph6cym4jeutpd7xcvmm7lresasx5ohymk4,D:\Thesisssss\phase4_outputs\data\jailbreakv_2k_8k\benign_images\train\benign_001372.jpg
```

Manifest info:

```text
rowCount: 10000
sha256Hex: c28dad31a35a72da1e3edb9f93f2327ea9a7a0dee2d6c1f2790742f4816db0b7
sha256Bytes32: 0xc28dad31a35a72da1e3edb9f93f2327ea9a7a0dee2d6c1f2790742f4816db0b7
```

Reason for a separate blockchain manifest:

The full upload-progress CSV contains operational fields such as status, errors, timestamps, and row indexes. The blockchain manifest is slimmer and contains only what the daemon needs: dataset id, row CID, and an audit path.

## 11. Uploading the Manifest to IPFS

Tool:

```text
threat-ledger-sepolia\tools\upload_manifest_to_ipfs.py
```

Command pattern:

```powershell
cd D:\Thesisssss\threat-ledger-sepolia
python tools\upload_manifest_to_ipfs.py D:\Thesisssss\phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\jailbreakv_2k_8k_used_dataset_blockchain_manifest.csv
```

The script:

```text
1. Reads the manifest CSV bytes.
2. Verifies the CSV has id and ipfs_cid columns.
3. Computes SHA-256.
4. Counts rows.
5. Uploads the manifest CSV to the local IPFS node.
6. Prints manifest_cid, manifest_sha256, and row_count.
```

The manifest CID recorded on Sepolia for version 3 is:

```text
bafybeib2lummeeq5ydxii7vdpkx4pxsrn6r3kgkmvljl6pscfmut5zs3ca
```

Reason:

The blockchain does not need to store all 10,000 row CIDs. It stores the CID of one manifest CSV plus the SHA-256 and row count. This is much cheaper and still verifiable.

## 12. Smart Contract Design

The smart contract is:

```text
threat-ledger-sepolia\contracts\ThreatLedger.sol
```

The core data structure is:

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

The contract also stores:

```solidity
address public owner;
address public approver;
uint256 public latestVersion;
uint256 public latestConfirmedVersion;
mapping(uint256 => Manifest) private manifests;
mapping(bytes32 => bool) public manifestHashUsed;
```

### 12.1 Why Store `latestVersion`

`latestVersion` tracks the newest registered manifest, whether approved or not.

Reason:

The notifier can detect newly registered manifests that still need approval.

### 12.2 Why Store `latestConfirmedVersion`

`latestConfirmedVersion` tracks the newest approved manifest.

Reason:

The training daemon should only trust confirmed manifests, not merely registered manifests.

### 12.3 Why Use Owner and Approver

Only the owner can register a manifest:

```solidity
modifier onlyOwner()
```

Only the approver can confirm a manifest:

```solidity
modifier onlyApprover()
```

Reason:

This creates a simple two-step approval process. Registration and approval are separated so a dataset update is not automatically trusted the moment it is proposed.

### 12.4 Why Require Sequential Versions

Registration requires:

```solidity
version == latestVersion + 1
```

Confirmation requires:

```solidity
version == latestConfirmedVersion + 1
```

Reason:

This prevents version gaps and keeps the history ordered. The daemon can reason about versions cleanly.

### 12.5 Why Prevent Duplicate Manifest Hashes

The contract tracks:

```solidity
manifestHashUsed[manifestSha256]
```

Reason:

The same manifest should not be registered repeatedly as a different version.

## 13. Deploying the Contract

Deployment script:

```text
threat-ledger-sepolia\scripts\deploy.js
```

Hardhat config:

```text
threat-ledger-sepolia\hardhat.config.js
```

Sepolia settings:

```text
chainId: 11155111
RPC URL: SEPOLIA_RPC_URL
signer: PRIVATE_KEY
```

Deployment command:

```powershell
cd D:\Thesisssss\threat-ledger-sepolia
npm run deploy:sepolia
```

The deploy script:

```text
1. Reads the deployer wallet from PRIVATE_KEY.
2. Reads APPROVER_ADDRESS or defaults to the deployer.
3. Deploys ThreatLedger(initialApprover).
4. Prints THREAT_LEDGER_ADDRESS for .env.
```

Reason:

The deployed address is needed by every later script and daemon process.

## 14. Registering a Manifest on Sepolia

Registration script:

```text
threat-ledger-sepolia\scripts\register-manifest.js
```

Command pattern:

```powershell
cd D:\Thesisssss\threat-ledger-sepolia
$env:MANIFEST_CSV="D:\Thesisssss\phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\jailbreakv_2k_8k_used_dataset_blockchain_manifest.csv"
$env:MANIFEST_CID="bafybeib2lummeeq5ydxii7vdpkx4pxsrn6r3kgkmvljl6pscfmut5zs3ca"
$env:MANIFEST_VERSION="3"
npm run register:sepolia
```

The script:

```text
1. Reads THREAT_LEDGER_ADDRESS.
2. Reads MANIFEST_CID.
3. Reads MANIFEST_CSV.
4. Calculates manifest SHA-256 and row count.
5. Calls registerManifest(version, manifestCid, manifestSha256, rowCount).
```

Reason:

The blockchain stores enough metadata to verify the manifest later:

```text
CID tells where to download it.
SHA-256 proves the exact manifest bytes.
rowCount proves expected number of rows.
version gives ordered history.
```

## 15. Confirming a Manifest

Confirmation script:

```text
threat-ledger-sepolia\scripts\confirm-manifest.js
```

Command pattern:

```powershell
$env:MANIFEST_VERSION="3"
npm run confirm:sepolia
```

The script:

```text
1. Reads THREAT_LEDGER_ADDRESS.
2. Reads MANIFEST_VERSION or defaults to latestVersion.
3. Calls confirmManifest(version).
4. Waits for the transaction.
```

Reason:

The daemon trusts only confirmed manifests. This approval step prevents accidental or unreviewed dataset updates from automatically entering training.

## 16. Reading Latest Blockchain State

Read script:

```text
threat-ledger-sepolia\scripts\read-latest.js
```

Command:

```powershell
npm run latest:sepolia
```

Current verified output:

```json
{
  "version": "3",
  "manifestCid": "bafybeib2lummeeq5ydxii7vdpkx4pxsrn6r3kgkmvljl6pscfmut5zs3ca",
  "manifestSha256": "0xc28dad31a35a72da1e3edb9f93f2327ea9a7a0dee2d6c1f2790742f4816db0b7",
  "rowCount": "10000",
  "timestamp": "1780841856",
  "proposer": "0x774389F22753F169a0DebE7D3c0Df3773070Cc17",
  "confirmed": true
}
```

Reason:

This script provides a simple audit command to prove which dataset version is currently trusted on-chain.

## 17. Approver Notifier

Notifier file:

```text
threat-ledger-sepolia\daemon\approver_notifier.py
```

Purpose:

It watches for manifests that are registered but not confirmed.

It reads:

```python
latestVersion()
latestConfirmedVersion()
getManifest(version)
```

Core logic:

```text
1. Read latestVersion from blockchain.
2. Read latestConfirmedVersion from blockchain.
3. For each version between latestConfirmedVersion + 1 and latestVersion:
   - read manifest
   - if confirmed is false, print approval notification
4. Save notified versions in approver_notifier_state.json.
5. Sleep poll interval and repeat.
```

Run once:

```powershell
python daemon\approver_notifier.py --once
```

Run continuously:

```powershell
python daemon\approver_notifier.py --poll-interval 60 --beep
```

Reason:

This is not required for training, but it improves workflow. The approver does not have to manually check the chain all the time.

## 18. Threat Daemon

Daemon file:

```text
threat-ledger-sepolia\daemon\threat_daemon.py
```

Purpose:

It watches Sepolia for newly confirmed manifest versions, downloads the IPFS dataset, verifies it, and optionally runs training.

### 18.1 How It Connects to Blockchain

The daemon loads:

```text
SEPOLIA_RPC_URL
THREAT_LEDGER_ADDRESS
```

Then it creates:

```python
web3 = Web3(Web3.HTTPProvider(rpc_url))
contract = web3.eth.contract(address=contract_address, abi=abi)
```

Reason:

The daemon must read the live Sepolia contract state from Python.

### 18.2 How It Checks Version Every Time

The main loop is:

```python
while True:
    state = load_state(output_dir)
    last_processed = int(state.get("last_processed_version", 0))
    latest_confirmed = int(contract.functions.latestConfirmedVersion().call())

    versions = versions_to_process(last_processed, latest_confirmed, args.all_confirmed)
    ...
    time.sleep(args.poll_interval)
```

Default poll interval:

```text
60 seconds
```

Reason:

Polling is simple and reliable. If the daemon is stopped, it can restart later, read the current blockchain value, compare it with local state, and continue.

### 18.3 Why Use `daemon_state.json`

The daemon stores:

```json
{
  "last_processed_version": 3
}
```

Reason:

Without state, the daemon would re-download the same version every time it starts. With state, it only processes new confirmed versions.

### 18.4 Latest-Only Mode

Default behavior:

```text
process only latestConfirmedVersion
```

Historical mode:

```powershell
python daemon\threat_daemon.py --once --all-confirmed --skip-training
```

Reason:

For a new machine, usually the newest confirmed dataset snapshot is enough. Historical replay is optional when old versions need to be audited or rebuilt.

### 18.5 Download and Verification Steps

For each version, the daemon:

```text
1. Calls getManifest(version) on the smart contract.
2. Confirms manifest.confirmed is true.
3. Downloads manifestCid from IPFS gateways.
4. Computes SHA-256 of downloaded manifest.csv.
5. Compares local SHA-256 with manifestSha256 from blockchain.
6. Reads CSV rows.
7. Checks CSV row count equals rowCount from blockchain.
8. Downloads each row CID.
9. Verifies row content against CID when possible.
10. Writes download_report.csv.
11. Runs TRAINING_COMMAND if configured and all rows succeed.
```

Reason:

This protects against:

```text
wrong manifest file
edited manifest file
missing rows
wrong row CIDs
partial downloads
unconfirmed blockchain versions
```

### 18.6 IPFS Gateways

The daemon tries:

```text
https://ipfs.io/ipfs/{cid}
https://dweb.link/ipfs/{cid}
https://gateway.pinata.cloud/ipfs/{cid}
```

Reason:

If one public gateway fails or is slow, another gateway may still work.

### 18.7 Local Downloaded Batch

Observed local batch directory:

```text
threat-ledger-sepolia\training_batches\version_3\
```

It contains:

```text
manifest.csv
download_report.csv
files\
```

The local `manifest.csv` has:

```text
10000 rows
SHA-256: 0xc28dad31a35a72da1e3edb9f93f2327ea9a7a0dee2d6c1f2790742f4816db0b7
```

That matches the blockchain value for version 3.

Important local note:

The observed local `version_3\files\` directory contains 2,726 downloaded row JSON files, while the manifest has 10,000 rows. Since the full set was not present locally, this should be treated as a partial daemon download/run. A full completed daemon run should produce all 10,000 row JSON files and then update `daemon_state.json`.

Reason for not treating partial download as complete:

Training must not start from incomplete data. The daemon only saves the processed version after `process_version` completes successfully.

## 19. Automatic Training from a Ledger Batch

The bridge script is:

```text
phase4\run_phase4_from_batch.py
```

The daemon can call it through:

```text
TRAINING_COMMAND
```

Example from `.env.example`:

```text
TRAINING_COMMAND=python D:\Thesisssss\phase4\run_phase4_from_batch.py
```

The daemon passes:

```text
BATCH_DIR
MANIFEST_PATH
VERSION
```

Reason:

The blockchain/IPFS system should be able to trigger the same Phase 4 training pipeline after it verifies a new approved batch.

### 19.1 Batch Materialization

`run_phase4_from_batch.py` supports two kinds of downloaded IPFS rows:

```text
1. Raw attack rows.
2. Prepared Phase 4 JSON rows with embedded base64 images.
```

For the Phase 4 rows used here, it:

```text
1. Reads JSON row files from BATCH_DIR\files.
2. Decodes embedded base64 image bytes.
3. Verifies embedded image SHA-256.
4. Writes recovered images to a new data directory.
5. Recreates train.jsonl, val.jsonl, and test.jsonl.
6. Writes dataset_metadata.json.
7. Runs train_lora.py if training is enabled.
```

Reason:

The IPFS objects are self-contained. A new machine can reconstruct the dataset from the ledger batch without relying on original local image paths.

## 20. Why the Design Choices Matter

### 20.1 Why Not Store Dataset Directly on Blockchain

Images and dataset rows are too large for efficient on-chain storage.

Decision:

Store large content on IPFS and store only manifest CID, SHA-256, row count, and version on-chain.

### 20.2 Why Use Both CID and SHA-256

IPFS CIDs identify content, but the smart contract also stores the manifest SHA-256.

Decision:

Use CID for retrieval and SHA-256 for explicit byte-level verification by the daemon.

### 20.3 Why Use a Manifest

The manifest groups 10,000 row CIDs into one versioned dataset.

Decision:

Register one manifest per dataset version instead of sending 10,000 blockchain transactions.

### 20.4 Why Use Confirmation

Registration alone does not mean the dataset is approved.

Decision:

Require `confirmManifest(version)` by the approver before the daemon can process it.

### 20.5 Why Poll the Blockchain

The daemon checks:

```python
latestConfirmedVersion().call()
```

every poll interval.

Decision:

Polling is easier to restart and reason about than relying only on live event subscriptions. If the daemon misses time while offline, it can still read the latest confirmed value when it comes back.

### 20.6 Why Use LoRA

The project needs a model safety update, not a full model retrain.

Decision:

Train LoRA adapters so only a tiny percentage of parameters changes.

### 20.7 Why Keep Benign Examples

Safety-only training can make a model refuse harmless prompts.

Decision:

Use a 1:4 attack-to-benign mixture so the model learns both refusal behavior and normal VQA behavior.

### 20.8 Why Split by Intent

Random splits can leak the same underlying harmful intent into train and test.

Decision:

Use normalized `intent_key` grouping so the same intent does not appear across splits.

### 20.9 Why Store Metadata and Hashes

The thesis needs reproducibility and auditability.

Decision:

Store `dataset_metadata.json`, JSONL hashes, upload summaries, manifest hashes, and blockchain version metadata.

## 21. Important Artifacts

### Dataset Artifacts

```text
phase4_outputs\data\jailbreakv_2k_8k\train.jsonl
phase4_outputs\data\jailbreakv_2k_8k\val.jsonl
phase4_outputs\data\jailbreakv_2k_8k\test.jsonl
phase4_outputs\data\jailbreakv_2k_8k\dataset_metadata.json
phase4_outputs\data\jailbreakv_2k_8k\jailbreakv_2k_8k_used_dataset.csv
```

### IPFS Artifacts

```text
phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\jailbreakv_2k_8k_used_dataset_upload_progress.csv
phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\jailbreakv_2k_8k_used_dataset_row_cids.csv
phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\jailbreakv_2k_8k_used_dataset_blockchain_manifest.csv
phase4_outputs\data\jailbreakv_2k_8k\ipfs_row_uploads\jailbreakv_2k_8k_used_dataset_upload_summary.json
```

### Blockchain Artifacts

```text
threat-ledger-sepolia\contracts\ThreatLedger.sol
threat-ledger-sepolia\abi\ThreatLedger.json
threat-ledger-sepolia\scripts\deploy.js
threat-ledger-sepolia\scripts\register-manifest.js
threat-ledger-sepolia\scripts\confirm-manifest.js
threat-ledger-sepolia\scripts\read-latest.js
```

### Daemon Artifacts

```text
threat-ledger-sepolia\daemon\threat_daemon.py
threat-ledger-sepolia\daemon\approver_notifier.py
threat-ledger-sepolia\training_batches\version_3\manifest.csv
threat-ledger-sepolia\training_batches\version_3\download_report.csv
threat-ledger-sepolia\training_batches\version_3\files\
```

### Model Artifacts

```text
phase4_outputs\adapters\jailbreakv_2k_8k_1.3b\
phase4_outputs\adapters\jailbreakv_2k_8k_1.3b_base_lora\
phase4_outputs\eval\base_1.3b_no_lora\summary.json
phase4_outputs\eval\jailbreakv_2k_8k_1.3b\summary.json
phase4_outputs\eval\jailbreakv_2k_8k_1.3b_base_lora\summary.json
```

## 22. Final End-to-End Summary

The complete process is:

```text
1. Start with JailBreakV attack CSV and image folders.
2. Read each attack row and verify the image exists.
3. Convert each attack into a multimodal SFT row:
   image + jailbreak prompt -> safe refusal answer.
4. Normalize unsafe intent to prevent train/test leakage.
5. Select 2,000 diverse attack rows.
6. Split attacks into train/val/test using 70/10/20.
7. Add 8,000 benign VQAv2 rows using a 1:4 attack-to-benign ratio.
8. Save benign images locally.
9. Write train.jsonl, val.jsonl, test.jsonl, and dataset_metadata.json.
10. Train a DeepSeek-VL LoRA adapter on train.jsonl and val.jsonl.
11. Evaluate the base model and LoRA adapter on the held-out test set.
12. Export the prepared dataset to a flat CSV for IPFS upload.
13. Upload each row as JSON to IPFS, embedding image bytes as base64.
14. Save row CIDs and upload summaries.
15. Create a slim blockchain manifest CSV containing id and ipfs_cid.
16. Upload the manifest CSV to IPFS.
17. Register manifest CID, SHA-256, row count, and version on Sepolia.
18. Confirm the version with the approver wallet.
19. The daemon polls latestConfirmedVersion from the smart contract.
20. When a new confirmed version appears, the daemon downloads the manifest.
21. The daemon verifies manifest SHA-256 and row count against blockchain.
22. The daemon downloads row CIDs from IPFS and writes a local batch.
23. After a complete verified batch, the daemon can trigger Phase 4 training.
```

The key idea is that model safety updates become reproducible and auditable:

```text
Dataset content is stored by IPFS CIDs.
The approved dataset version is stored on Sepolia.
The daemon uses only confirmed blockchain versions.
Training uses a hashed, documented SFT dataset.
The LoRA adapter is a compact safety update to the base model.
```
