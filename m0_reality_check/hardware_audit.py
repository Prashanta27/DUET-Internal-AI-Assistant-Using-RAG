#!/usr/bin/env python3
"""
M0 / Step 1 -- Hardware audit for the DUET local RAG assistant.

Reports what this machine actually is, then maps that to which local
models are realistically runnable. Run this on BOTH machines:

    python hardware_audit.py                 # your laptop (dev)
    python hardware_audit.py --label server  # the university machine

Save both outputs. You will need them in the meeting with your adviser --
"what GPU do we have" is the single question that decides the model tier.

No third-party dependencies required (psutil / torch are used if present).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Model tiers: what fits in how much VRAM (4-bit quantised, Ollama defaults)
# These are planning estimates, not promises. Always confirm by running.
# --------------------------------------------------------------------------
LLM_TIERS = [
    (0, 6, "CPU-only / <6 GB VRAM",
     "1B-4B q4 (e.g. a small Qwen/Llama instruct). Usable for plumbing tests only. "
     "Bangla generation will be poor -- do NOT judge the project by this tier."),
    (6, 10, "6-10 GB VRAM",
     "7B-8B q4. This is the realistic minimum for acceptable Bangla/English RAG answers."),
    (10, 18, "10-18 GB VRAM",
     "12B-14B q4, or 8B at higher precision + a reranker resident at the same time."),
    (18, 26, "18-26 GB VRAM",
     "24B-32B q4. Comfortable: LLM + embedding model + reranker all resident."),
    (26, 10_000, ">26 GB VRAM",
     "32B+ at q4/q8. Also enough headroom for a vision-language model for OCR fallback."),
]

EMBED_NOTE = (
    "bge-m3 is ~568M params (~2.3 GB fp32 / ~1.2 GB fp16). It runs on CPU, but expect "
    "roughly 0.5-3 s per chunk on a slow laptop CPU. Measure it -- ingestion time for the "
    "whole corpus is a number your adviser will ask for."
)


def run(cmd: list[str], timeout: int = 15) -> str | None:
    """Run a command, return stdout or None if unavailable/failed."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def get_ram_gb() -> float | None:
    try:
        import psutil  # type: ignore

        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:
        pass
    # Linux fallback
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024**2, 1)
    except Exception:
        pass
    # Windows fallback
    if platform.system() == "Windows":
        out = run(["wmic", "computersystem", "get", "TotalPhysicalMemory"])
        if out:
            for tok in out.split():
                if tok.isdigit():
                    return round(int(tok) / 1024**3, 1)
    return None


def get_cpu() -> dict:
    info = {
        "processor": platform.processor() or platform.machine(),
        "logical_cores": os.cpu_count(),
        "physical_cores": None,
    }
    try:
        import psutil  # type: ignore

        info["physical_cores"] = psutil.cpu_count(logical=False)
    except Exception:
        pass
    # Nicer model name on Linux
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if "model name" in line:
                    info["processor"] = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    return info


def get_gpus() -> list[dict]:
    gpus: list[dict] = []
    out = run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if out:
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                try:
                    vram = round(float(parts[1]) / 1024, 1)
                except ValueError:
                    vram = None
                gpus.append({
                    "name": parts[0],
                    "vram_gb": vram,
                    "driver": parts[2] if len(parts) > 2 else None,
                    "vendor": "nvidia",
                })
    if not gpus:
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    props = torch.cuda.get_device_properties(i)
                    gpus.append({
                        "name": props.name,
                        "vram_gb": round(props.total_memory / 1024**3, 1),
                        "driver": None,
                        "vendor": "cuda(torch)",
                    })
        except Exception:
            pass
    return gpus


def get_disk_free_gb(path: str = ".") -> float:
    return round(shutil.disk_usage(path).free / 1024**3, 1)


def cpu_microbenchmark() -> float:
    """Rough single-thread float throughput. Lower seconds = faster CPU.

    Not a real benchmark -- just a relative number so you can compare your
    laptop against the server without installing anything.
    """
    start = time.perf_counter()
    total = 0.0
    for i in range(1, 3_000_001):
        total += (i * 1.0000001) / (i + 1)
    return round(time.perf_counter() - start, 2)


def check_tooling() -> dict:
    ollama_version = run(["ollama", "--version"])
    ollama_models = run(["ollama", "list"])
    tesseract = run(["tesseract", "--version"])
    tess_langs = run(["tesseract", "--list-langs"])
    has_ben = bool(tess_langs and "ben" in tess_langs.split())
    return {
        "python": sys.version.split()[0],
        "ollama_installed": bool(ollama_version),
        "ollama_version": (ollama_version or "").splitlines()[0] if ollama_version else None,
        "ollama_models": ollama_models,
        "tesseract_installed": bool(tesseract),
        "tesseract_version": (tesseract or "").splitlines()[0] if tesseract else None,
        "tesseract_has_bengali": has_ben,
        "docker_installed": shutil.which("docker") is not None,
        "postgres_client": shutil.which("psql") is not None,
    }


def tier_for(vram_gb: float | None) -> tuple[str, str]:
    v = vram_gb or 0.0
    for lo, hi, label, advice in LLM_TIERS:
        if lo <= v < hi:
            return label, advice
    return LLM_TIERS[-1][2], LLM_TIERS[-1][3]


def main() -> int:
    ap = argparse.ArgumentParser(description="Hardware audit for the DUET RAG project")
    ap.add_argument("--label", default="this-machine",
                    help="A name for this machine, e.g. 'laptop' or 'lab-server'")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="Also write the raw report to a JSON file")
    ap.add_argument("--skip-benchmark", action="store_true",
                    help="Skip the CPU micro-benchmark (saves ~5s)")
    args = ap.parse_args()

    gpus = get_gpus()
    ram = get_ram_gb()
    cpu = get_cpu()
    tooling = check_tooling()
    best_vram = max((g["vram_gb"] or 0 for g in gpus), default=0.0)
    tier_label, tier_advice = tier_for(best_vram)

    report = {
        "label": args.label,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu,
        "ram_gb": ram,
        "disk_free_gb": get_disk_free_gb(),
        "gpus": gpus,
        "tooling": tooling,
        "cpu_benchmark_seconds": None if args.skip_benchmark else cpu_microbenchmark(),
        "llm_tier": tier_label,
    }

    w = 74
    print("=" * w)
    print(f"  HARDWARE AUDIT -- {args.label}")
    print(f"  {report['timestamp_utc']}")
    print("=" * w)
    print(f"OS              : {report['os']}")
    print(f"CPU             : {cpu['processor']}")
    print(f"Cores           : {cpu['logical_cores']} logical"
          + (f" / {cpu['physical_cores']} physical" if cpu["physical_cores"] else ""))
    print(f"RAM             : {ram if ram else 'unknown'} GB")
    print(f"Disk free       : {report['disk_free_gb']} GB")
    if report["cpu_benchmark_seconds"] is not None:
        print(f"CPU bench       : {report['cpu_benchmark_seconds']} s "
              f"(lower is faster; compare laptop vs server)")

    print("-" * w)
    if gpus:
        for g in gpus:
            print(f"GPU             : {g['name']}  |  {g['vram_gb']} GB VRAM"
                  + (f"  |  driver {g['driver']}" if g.get("driver") else ""))
    else:
        print("GPU             : NONE DETECTED (CPU-only)")
        print("                  -> ask your adviser whether a GPU machine exists.")
        print("                  -> if not, this changes the entire model strategy.")

    print("-" * w)
    print("TOOLING")
    print(f"  Python        : {tooling['python']}")
    print(f"  Ollama        : {tooling['ollama_version'] or 'NOT INSTALLED'}")
    print(f"  Tesseract     : {tooling['tesseract_version'] or 'NOT INSTALLED'}")
    print(f"  Bengali OCR   : {'YES (ben traineddata found)' if tooling['tesseract_has_bengali'] else 'NO -- install ben traineddata'}")
    print(f"  Docker        : {'yes' if tooling['docker_installed'] else 'no'}")
    print(f"  psql          : {'yes' if tooling['postgres_client'] else 'no'}")
    if tooling["ollama_models"]:
        print("  Ollama models :")
        for line in tooling["ollama_models"].splitlines()[1:6]:
            print(f"      {line}")

    print("=" * w)
    print("VERDICT")
    print("=" * w)
    print(f"LLM tier        : {tier_label}")
    print(f"Recommendation  : {tier_advice}")
    print()
    print(f"Embeddings      : {EMBED_NOTE}")
    print()
    if best_vram < 6:
        print("WARNING: below 6 GB VRAM you can build and test the FULL pipeline, but you")
        print("         cannot fairly judge answer quality. Do all plumbing here, then run")
        print("         the M5 model benchmark on the server. Never report laptop-tier")
        print("         answer quality as the project's result.")
    if ram and ram < 16:
        print("WARNING: under 16 GB RAM, running Postgres + vector index + embedding model +")
        print("         Ollama at once will swap. Close everything else while ingesting.")
    print()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Raw report written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
