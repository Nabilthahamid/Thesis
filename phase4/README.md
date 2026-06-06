# Phase 4: Adversarial LoRA Fine-Tuning

This package adds the training side of the threat-ledger workflow:

1. Prepare attack plus benign SFT JSONL.
2. Train a DeepSeek-VL LoRA/QLoRA adapter.
3. Evaluate attack success rate before and after the adapter.
4. Let the Sepolia/IPFS daemon trigger the same flow for confirmed batches.

## Setup

On the Linux/cloud training machine:

```bash
pip install -r phase4/requirements.txt
pip install -e DeepSeek-VL
```

Use a GPU with at least 24 GB VRAM for the 7B path. The scripts default to
`deepseek-ai/deepseek-vl-7b-chat`, 4-bit NF4 QLoRA, LoRA rank 16, and a 1:4
attack-to-benign ratio.

## Prepare Data

Full JailBreakV run:

```bash
python phase4/prepare_sft_data.py \
  --attack-csv JailBreakV_28K/JailBreakV_28k/JailBreakV_28K.csv \
  --attack-image-root JailBreakV_28K/JailBreakV_28k \
  --output-dir phase4_outputs/data/jailbreakv_full
```

Smoke data, matching the thesis test plan:

```bash
python phase4/prepare_sft_data.py \
  --attack-csv JailBreakV_28K/JailBreakV_28k/JailBreakV_28K.csv \
  --attack-image-root JailBreakV_28K/JailBreakV_28k \
  --output-dir phase4_outputs/data/smoke \
  --max-attacks 50
```

The generated `dataset_metadata.json` records split counts, hashes, seed, source
CSV hash, and the benign dataset used.

## Train

```bash
python phase4/train_lora.py \
  --train-jsonl phase4_outputs/data/jailbreakv_full/train.jsonl \
  --val-jsonl phase4_outputs/data/jailbreakv_full/val.jsonl \
  --dataset-metadata phase4_outputs/data/jailbreakv_full/dataset_metadata.json \
  --output-dir phase4_outputs/adapters/ledger_vinitial \
  --epochs 1 \
  --batch-size 1 \
  --grad-accum-steps 8
```

The trainer freezes `vision_model`, applies LoRA to `aligner` linear layers and
language-model `q_proj`/`v_proj`, then calls DeepSeek-VL's
`prepare_inputs_embeds()` before `language_model(...)`.

## Evaluate

Baseline:

```bash
python phase4/eval_attacks.py \
  --attack-jsonl phase4_outputs/data/jailbreakv_full/test.jsonl \
  --benign-jsonl phase4_outputs/data/jailbreakv_full/test.jsonl \
  --output-dir phase4_outputs/eval/baseline
```

Adapter:

```bash
python phase4/eval_attacks.py \
  --adapter-path phase4_outputs/adapters/ledger_vinitial \
  --attack-jsonl phase4_outputs/data/jailbreakv_full/test.jsonl \
  --benign-jsonl phase4_outputs/data/jailbreakv_full/test.jsonl \
  --output-dir phase4_outputs/eval/ledger_vinitial
```

By default the evaluator uses `meta-llama/Llama-Guard-3-8B` over the harmful
intent and model response. For a quick local smoke evaluation, add
`--judge heuristic`.

## Ledger Integration

Set this in `threat-ledger-sepolia/.env` after the training environment is ready:

```env
TRAINING_COMMAND=python D:\Thesisssss\phase4\run_phase4_from_batch.py
```

Useful environment overrides:

```env
ATTACK_IMAGE_ROOT=D:\Thesisssss\JailBreakV_28K\JailBreakV_28k
PHASE4_MODEL_NAME=deepseek-ai/deepseek-vl-7b-chat
DEEPSEEK_VL_PATH=D:\Thesisssss\DeepSeek-VL
PHASE4_PREVIOUS_ADAPTER=
PHASE4_MAX_ATTACKS=0
PHASE4_EPOCHS=1
PHASE4_BATCH_SIZE=1
PHASE4_GRAD_ACCUM_STEPS=8
PHASE4_RUN_EVAL=0
```

For Windows PowerShell, quote the command if paths contain spaces.

