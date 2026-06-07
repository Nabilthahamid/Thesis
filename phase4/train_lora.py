#!/usr/bin/env python3
"""Train a DeepSeek-VL LoRA adapter with a custom multimodal SFT loop."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import torch
from torch.utils.data import DataLoader, Dataset
from tqdm.auto import tqdm

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from phase4.common import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    add_deepseek_to_path,
    load_rgb_image,
    read_jsonl,
    save_json,
    set_seed,
    sha256_jsonl,
)


@dataclass
class Batch:
    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    pixel_values: torch.Tensor
    images_seq_mask: torch.Tensor
    images_emb_mask: torch.Tensor
    labels: torch.Tensor
    ids: list[str]


class SftJsonlDataset(Dataset):
    def __init__(self, path: Path, max_samples: int = 0):
        self.path = path
        self.rows = read_jsonl(path)
        if max_samples > 0:
            self.rows = self.rows[:max_samples]

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.rows[index]


class DeepSeekVlCollator:
    def __init__(self, processor, max_length: int):
        self.processor = processor
        self.max_length = max_length

    def _conversation(self, sample: dict[str, Any], include_answer: bool) -> list[dict[str, Any]]:
        return [
            {
                "role": "User",
                "content": sample["prompt"],
                "images": [sample["image"]],
            },
            {
                "role": "Assistant",
                "content": sample["answer"] if include_answer else "",
            },
        ]

    def __call__(self, samples: list[dict[str, Any]]) -> Batch:
        prepares = []
        prompt_lengths: list[int] = []
        ids: list[str] = []

        for sample in samples:
            image = load_rgb_image(sample["image"])
            full_prepare = self.processor.process_one(
                conversations=self._conversation(sample, include_answer=True),
                images=[image],
            )
            prompt_prepare = self.processor.process_one(
                conversations=self._conversation(sample, include_answer=False),
                images=[image],
            )

            if len(full_prepare.input_ids) > self.max_length:
                raise ValueError(
                    f"Sample {sample.get('id')} has {len(full_prepare.input_ids)} tokens, "
                    f"above max_length={self.max_length}. Regenerate data with a lower "
                    "--max-prompt-chars or raise --max-length if the model supports it."
                )

            prepares.append(full_prepare)
            prompt_lengths.append(len(prompt_prepare.input_ids))
            ids.append(str(sample.get("id", "")))

        batch = self.processor.batchify(prepares)
        labels = torch.full_like(batch.input_ids, fill_value=-100)
        image_id = self.processor.image_id

        for index, prepare in enumerate(prepares):
            seq_len = len(prepare.input_ids)
            start = batch.input_ids.shape[1] - seq_len
            labels[index, start:] = prepare.input_ids
            prompt_end = start + min(prompt_lengths[index], seq_len)
            labels[index, start:prompt_end] = -100
            labels[index, batch.input_ids[index] == image_id] = -100
            labels[index, batch.attention_mask[index] == 0] = -100

        return Batch(
            input_ids=batch.input_ids,
            attention_mask=batch.attention_mask,
            pixel_values=batch.pixel_values,
            images_seq_mask=batch.images_seq_mask,
            images_emb_mask=batch.images_emb_mask,
            labels=labels,
            ids=ids,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", default="deepseek-ai/deepseek-vl-7b-chat")
    parser.add_argument("--deepseek-vl-path", default="")
    parser.add_argument("--train-jsonl", required=True)
    parser.add_argument("--val-jsonl", required=True)
    parser.add_argument("--dataset-metadata", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT / "adapters" / "ledger_vmanual"))
    parser.add_argument("--resume-adapter", default="")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum-steps", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--max-train-samples", type=int, default=0)
    parser.add_argument("--max-val-samples", type=int, default=0)
    parser.add_argument("--max-val-batches", type=int, default=64)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--dtype", choices=("auto", "bf16", "fp16", "fp32"), default="auto")
    parser.add_argument("--qlora", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--gradient-checkpointing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--save-every-epoch", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def choose_dtype(args: argparse.Namespace) -> torch.dtype:
    if args.dtype == "fp32":
        return torch.float32
    if args.dtype == "fp16":
        return torch.float16
    if args.dtype == "bf16":
        return torch.bfloat16
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    if torch.cuda.is_available():
        return torch.float16
    return torch.float32


def safe_console_text(value: Any) -> str:
    return str(value).encode("ascii", errors="backslashreplace").decode("ascii")


def import_deepseek_and_hf(deepseek_vl_path: str) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    add_deepseek_to_path(deepseek_vl_path or None)
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    deepseek_models = importlib.import_module("deepseek_vl.models")
    VLChatProcessor = cast(Any, deepseek_models).VLChatProcessor

    return AutoModelForCausalLM, BitsAndBytesConfig, LoraConfig, PeftModel, VLChatProcessor, get_peft_model, prepare_model_for_kbit_training


def load_base_model(args: argparse.Namespace, dtype: torch.dtype) -> tuple[Any, Any]:
    (
        AutoModelForCausalLM,
        BitsAndBytesConfig,
        LoraConfig,
        PeftModel,
        VLChatProcessor,
        get_peft_model,
        prepare_model_for_kbit_training,
    ) = import_deepseek_and_hf(args.deepseek_vl_path)

    quantization_config = None
    if args.qlora:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype,
        )

    processor = VLChatProcessor.from_pretrained(args.model_name)
    model = AutoModelForCausalLM.from_pretrained(
        args.model_name,
        trust_remote_code=True,
        torch_dtype=dtype,
        device_map=args.device_map,
        quantization_config=quantization_config,
    )

    core = cast(Any, get_model_core(model))
    if hasattr(core, "config"):
        core.config.use_cache = False
    if hasattr(core, "language_model") and hasattr(core.language_model, "config"):
        core.language_model.config.use_cache = False
    if hasattr(core, "vision_model"):
        core.vision_model.requires_grad_(False)
        core.vision_model.eval()

    if args.qlora:
        try:
            model = prepare_model_for_kbit_training(
                model,
                use_gradient_checkpointing=args.gradient_checkpointing,
            )
        except Exception as exc:
            print(f"Warning: prepare_model_for_kbit_training failed: {safe_console_text(exc)}")

    if args.resume_adapter:
        model = PeftModel.from_pretrained(model, args.resume_adapter, is_trainable=True)
    else:
        target_modules = find_lora_targets(get_model_core(model))
        if not target_modules:
            raise RuntimeError("No LoRA target modules found for aligner/q_proj/v_proj.")
        lora_config = LoraConfig(
            r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            bias="none",
            target_modules=target_modules,
        )
        model = get_peft_model(model, lora_config)

    core = cast(Any, get_model_core(model))
    if args.gradient_checkpointing and hasattr(core, "language_model"):
        try:
            core.language_model.gradient_checkpointing_enable()
        except Exception as exc:
            print(f"Warning: could not enable language gradient checkpointing: {safe_console_text(exc)}")

    return model, processor


def get_model_core(model: Any) -> Any:
    if hasattr(model, "get_base_model"):
        return model.get_base_model()
    if hasattr(model, "base_model") and hasattr(model.base_model, "model"):
        return model.base_model.model
    return model


def is_linear_like(module: torch.nn.Module) -> bool:
    return "Linear" in module.__class__.__name__


def find_lora_targets(model: Any) -> list[str]:
    targets: list[str] = []
    for name, module in model.named_modules():
        if not is_linear_like(module):
            continue
        if name.startswith("aligner.") or name.endswith(".q_proj") or name.endswith(".v_proj"):
            targets.append(name)
    return sorted(set(targets))


def model_device(model: Any) -> torch.device:
    for parameter in model.parameters():
        return parameter.device
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def module_float_dtype(module: Any, fallback: torch.dtype) -> torch.dtype:
    if module is None:
        return fallback
    for parameter in module.parameters():
        if parameter.is_floating_point():
            return parameter.dtype
    for buffer in module.buffers():
        if buffer.is_floating_point():
            return buffer.dtype
    return fallback


def vision_input_dtype(model: Any, fallback: torch.dtype) -> torch.dtype:
    core = cast(Any, get_model_core(model))
    return module_float_dtype(getattr(core, "vision_model", None), fallback)


def move_batch(batch: Batch, device: torch.device, dtype: torch.dtype, image_dtype: torch.dtype) -> dict[str, Any]:
    return {
        "input_ids": batch.input_ids.to(device),
        "attention_mask": batch.attention_mask.to(device),
        "pixel_values": batch.pixel_values.to(device=device, dtype=image_dtype),
        "images_seq_mask": batch.images_seq_mask.to(device),
        "images_emb_mask": batch.images_emb_mask.to(device),
        "labels": batch.labels.to(device),
        "ids": batch.ids,
    }


def create_optimizer(args: argparse.Namespace, model: Any) -> Any:
    trainable_params = [param for param in model.parameters() if param.requires_grad]
    try:
        import bitsandbytes as bnb

        optimizer_cls = getattr(getattr(bnb, "optim"), "PagedAdamW8bit")
        return optimizer_cls(
            trainable_params,
            lr=args.learning_rate,
            weight_decay=args.weight_decay,
        )
    except Exception:
        return torch.optim.AdamW(
            trainable_params,
            lr=args.learning_rate,
            weight_decay=args.weight_decay,
        )


def create_scheduler(args: argparse.Namespace, optimizer: Any, steps_per_epoch: int) -> tuple[Any, int]:
    total_steps = args.max_steps if args.max_steps > 0 else max(1, steps_per_epoch * args.epochs)
    warmup_steps = int(total_steps * args.warmup_ratio)

    def lr_lambda(step: int) -> float:
        if warmup_steps > 0 and step < warmup_steps:
            return max(0.0, float(step) / float(max(1, warmup_steps)))
        progress = float(step - warmup_steps) / float(max(1, total_steps - warmup_steps))
        return max(0.0, 0.5 * (1.0 + math.cos(math.pi * progress)))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda), total_steps


def forward_loss(model: Any, batch: dict[str, Any]) -> torch.Tensor:
    core = cast(Any, get_model_core(model))
    labels = batch.pop("labels")
    batch.pop("ids", None)
    inputs_embeds = core.prepare_inputs_embeds(**batch)
    outputs = core.language_model(
        inputs_embeds=inputs_embeds,
        attention_mask=batch["attention_mask"],
        labels=labels,
    )
    batch["labels"] = labels
    return outputs.loss


@torch.no_grad()
def evaluate_loss(
    model: Any,
    dataloader: Any,
    device: torch.device,
    dtype: torch.dtype,
    image_dtype: torch.dtype,
    max_batches: int,
) -> float:
    model.eval()
    losses: list[float] = []
    for batch_index, batch in enumerate(dataloader):
        if max_batches > 0 and batch_index >= max_batches:
            break
        moved = move_batch(batch, device, dtype, image_dtype)
        loss = forward_loss(model, moved)
        losses.append(float(loss.detach().cpu()))
    model.train()
    return sum(losses) / max(1, len(losses))


def trainable_parameter_summary(model: Any) -> dict[str, int | float]:
    trainable = 0
    total = 0
    for parameter in model.parameters():
        count = parameter.numel()
        total += count
        if parameter.requires_grad:
            trainable += count
    return {
        "trainable_params": trainable,
        "total_params": total,
        "trainable_percent": (100.0 * trainable / total) if total else 0.0,
    }


def copy_metadata(args: argparse.Namespace, output_dir: Path) -> None:
    if args.dataset_metadata:
        source = Path(args.dataset_metadata)
        if source.exists():
            shutil.copyfile(source, output_dir / "dataset_metadata.json")


def main() -> int:
    args = parse_args()
    set_seed(args.seed)
    dtype = choose_dtype(args)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    model, processor = load_base_model(args, dtype)
    device = model_device(get_model_core(model))
    image_dtype = vision_input_dtype(model, dtype)
    param_summary = trainable_parameter_summary(model)
    print(f"Trainable parameters: {param_summary}")
    print(f"Model dtype: {dtype}; image dtype: {image_dtype}")

    train_dataset = SftJsonlDataset(Path(args.train_jsonl), args.max_train_samples)
    val_dataset = SftJsonlDataset(Path(args.val_jsonl), args.max_val_samples)
    collator = DeepSeekVlCollator(processor, max_length=args.max_length)
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collator,
        num_workers=args.num_workers,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collator,
        num_workers=args.num_workers,
    )

    optimizer = create_optimizer(args, model)
    steps_per_epoch = max(1, math.ceil(len(train_loader) / max(1, args.grad_accum_steps)))
    scheduler, total_steps = create_scheduler(args, optimizer, steps_per_epoch)

    run_config = vars(args).copy()
    run_config.update(
        {
            "dtype": str(dtype),
            "image_dtype": str(image_dtype),
            "train_jsonl_sha256": sha256_jsonl(Path(args.train_jsonl)),
            "val_jsonl_sha256": sha256_jsonl(Path(args.val_jsonl)),
            "train_samples": len(train_dataset),
            "val_samples": len(val_dataset),
            "parameter_summary": param_summary,
            "started_at_unix": time.time(),
        }
    )
    save_json(output_dir / "run_config.json", run_config)
    copy_metadata(args, output_dir)

    metrics: list[dict[str, Any]] = []
    global_step = 0
    optimizer.zero_grad(set_to_none=True)
    model.train()

    for epoch in range(1, args.epochs + 1):
        progress = tqdm(train_loader, desc=f"epoch {epoch}", unit="batch")
        running_loss = 0.0
        optimizer_steps = 0

        for batch_index, batch in enumerate(progress, start=1):
            moved = move_batch(batch, device, dtype, image_dtype)
            loss = forward_loss(model, moved) / max(1, args.grad_accum_steps)
            loss.backward()
            running_loss += float(loss.detach().cpu()) * max(1, args.grad_accum_steps)

            if batch_index % args.grad_accum_steps == 0 or batch_index == len(train_loader):
                if args.max_grad_norm > 0:
                    torch.nn.utils.clip_grad_norm_(
                        [param for param in model.parameters() if param.requires_grad],
                        args.max_grad_norm,
                    )
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                global_step += 1
                optimizer_steps += 1
                progress.set_postfix(
                    loss=f"{running_loss / max(1, batch_index):.4f}",
                    step=global_step,
                )

                if args.max_steps > 0 and global_step >= args.max_steps:
                    break

        val_loss = evaluate_loss(model, val_loader, device, dtype, image_dtype, args.max_val_batches)
        epoch_metrics = {
            "epoch": epoch,
            "global_step": global_step,
            "train_loss": running_loss / max(1, len(train_loader)),
            "val_loss": val_loss,
            "optimizer_steps": optimizer_steps,
            "learning_rate": scheduler.get_last_lr()[0],
        }
        metrics.append(epoch_metrics)
        save_json(output_dir / "training_metrics.json", {"metrics": metrics})
        print(f"Epoch {epoch} metrics: {json.dumps(epoch_metrics, indent=2)}")

        if args.save_every_epoch:
            checkpoint_dir = output_dir / f"epoch_{epoch}"
            model.save_pretrained(str(checkpoint_dir))

        if args.max_steps > 0 and global_step >= args.max_steps:
            break

    model.save_pretrained(str(output_dir))
    try:
        processor.save_pretrained(str(output_dir / "processor"))
    except Exception as exc:
        print(f"Warning: processor save failed: {safe_console_text(exc)}")

    save_json(
        output_dir / "training_metrics.json",
        {
            "metrics": metrics,
            "finished_at_unix": time.time(),
            "total_optimizer_steps": global_step,
            "planned_total_steps": total_steps,
        },
    )
    print(f"Saved LoRA adapter to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
