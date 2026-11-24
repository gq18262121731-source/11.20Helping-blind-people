"""Fetch the pinned official Depth Anything V2 source and Small checkpoint."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import urllib.request


DEPTH_REPOSITORY = "https://github.com/DepthAnything/Depth-Anything-V2.git"
DEPTH_CODE_REVISION = "a561b849ebae10a6f5ef49e26c83cbbcd36c71bf"
DEPTH_WEIGHTS_URL = "https://huggingface.co/depth-anything/Depth-Anything-V2-Small/resolve/main/depth_anything_v2_vits.pth"
DEPTH_WEIGHTS_SHA256 = "715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_depth_assets(code_path, model_path, *, progress=None):
    code = Path(code_path).resolve()
    weights = Path(model_path).resolve()
    weights.parent.mkdir(parents=True, exist_ok=True)
    code.parent.mkdir(parents=True, exist_ok=True)

    if not (code / "depth_anything_v2" / "dpt.py").is_file():
        if code.exists() and any(code.iterdir()):
            raise RuntimeError(f"{code} exists but is not an official Depth Anything V2 checkout")
        if progress:
            progress("cloning_official_code")
        completed = subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", DEPTH_REPOSITORY, str(code)],
            capture_output=True, text=True, timeout=180,
        )
        if completed.returncode:
            raise RuntimeError(f"official model code download failed: {completed.stderr.strip()}")
        subprocess.run(["git", "-C", str(code), "fetch", "--quiet", "--depth", "1", "origin", DEPTH_CODE_REVISION],
                       check=True, capture_output=True, text=True, timeout=180)
        subprocess.run(["git", "-C", str(code), "checkout", "--quiet", "--detach", DEPTH_CODE_REVISION],
                       check=True, capture_output=True, text=True, timeout=60)
    current = subprocess.run(["git", "-C", str(code), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    if current.returncode or current.stdout.strip() != DEPTH_CODE_REVISION:
        raise RuntimeError(f"Depth Anything code is not at the verified revision {DEPTH_CODE_REVISION}")

    if weights.is_file() and _sha256(weights) == DEPTH_WEIGHTS_SHA256:
        return {"code_revision": DEPTH_CODE_REVISION, "weights_sha256": DEPTH_WEIGHTS_SHA256,
                "weights_path": str(weights), "downloaded": False}
    if weights.exists():
        raise RuntimeError(f"checkpoint checksum mismatch; existing file was preserved: {weights}")
    temporary = weights.with_name(weights.name + ".part")
    if progress:
        progress("downloading_verified_small_checkpoint")
    request = urllib.request.Request(DEPTH_WEIGHTS_URL, headers={"User-Agent": "Morning-Star/Depth-Anything-V2"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            sha = hashlib.sha256()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                sha.update(chunk)
        if sha.hexdigest() != DEPTH_WEIGHTS_SHA256:
            raise RuntimeError(f"checkpoint SHA-256 verification failed: {sha.hexdigest()}")
        os.replace(temporary, weights)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"code_revision": DEPTH_CODE_REVISION, "weights_sha256": DEPTH_WEIGHTS_SHA256,
            "weights_path": str(weights), "downloaded": True}
