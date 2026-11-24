"""Small, lazy adapter for the official Depth Anything V2 Small model."""
from __future__ import annotations

import sys
from pathlib import Path
import time

import numpy as np


class DepthModelError(RuntimeError):
    pass


class DepthAnythingEstimator:
    def __init__(self, code_path, model_path, *, input_size=518, prefer_fp16=True):
        self.code_path = Path(code_path).resolve()
        self.model_path = Path(model_path).resolve()
        self.input_size = input_size
        self.prefer_fp16 = prefer_fp16
        self.model = None
        self.device = "unavailable"
        self.precision = "fp32"
        self._configured_input_size = input_size
        self._oom_count = 0
        self._cpu_fallback_due_to_oom = False
        self._last_cuda_retry = 0.0

    def load(self):
        if self.model is not None:
            return self
        if not (self.code_path / "depth_anything_v2" / "dpt.py").is_file():
            raise DepthModelError(f"官方模型代码不存在：{self.code_path}；运行 python tools/prepare_depth_model.py")
        if not self.model_path.is_file():
            raise DepthModelError(f"Small 权重不存在：{self.model_path}；运行 python tools/prepare_depth_model.py")
        if str(self.code_path) not in sys.path:
            sys.path.insert(0, str(self.code_path))
        try:
            import torch
            from depth_anything_v2.dpt import DepthAnythingV2
            config = {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]}
            model = DepthAnythingV2(**config)
            state = torch.load(self.model_path, map_location="cpu", weights_only=True)
            model.load_state_dict(state, strict=True)
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            if self.device == "cuda" and self.prefer_fp16:
                model = model.half()
                self.precision = "fp16"
            model = model.to(self.device).eval()
            self.model = model
            return self
        except Exception as error:
            self.model = None
            raise DepthModelError(f"Depth Anything V2 Small 初始化失败：{error}") from error

    def predict(self, bgr_image):
        if self.model is None:
            self.load()
        if not isinstance(bgr_image, np.ndarray) or bgr_image.ndim != 3 or bgr_image.shape[2] != 3:
            raise DepthModelError("深度模型需要 H×W×3 BGR 图像")
        import cv2
        import torch
        if self._cpu_fallback_due_to_oom and self.device == "cpu" and time.monotonic() - self._last_cuda_retry >= 60:
            self._last_cuda_retry = time.monotonic()
            try:
                self.model.half().to("cuda").eval()
                self.device, self.precision, self.input_size = "cuda", "fp16", self._configured_input_size
                self._oom_count = 0
            except Exception:
                self.model.float().to("cpu").eval()
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
        try:
            from torchvision.transforms import Compose
            from depth_anything_v2.util.transform import NormalizeImage, PrepareForNet, Resize
            height, width = bgr_image.shape[:2]
            rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB) / 255.0
            transform = Compose([
                Resize(width=self.input_size, height=self.input_size, resize_target=False,
                       keep_aspect_ratio=True, ensure_multiple_of=14,
                       resize_method="lower_bound", image_interpolation_method=cv2.INTER_CUBIC),
                NormalizeImage(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                PrepareForNet(),
            ])
            tensor = torch.from_numpy(transform({"image": rgb})["image"]).unsqueeze(0).to(self.device)
            with torch.inference_mode():
                if self.device == "cuda" and self.precision == "fp16":
                    with torch.autocast("cuda", dtype=torch.float16):
                        depth = self.model(tensor)
                else:
                    depth = self.model(tensor)
                depth = torch.nn.functional.interpolate(
                    depth[:, None], (height, width), mode="bilinear", align_corners=True,
                )[0, 0]
            depth = depth.detach().float().cpu().numpy().astype(np.float32, copy=False)
            if depth.shape != bgr_image.shape[:2] or not np.isfinite(depth).any():
                raise DepthModelError("深度模型输出尺寸或数值无效")
            return depth
        except Exception as error:
            if isinstance(error, DepthModelError):
                raise
            message = str(error)
            if "out of memory" in message.lower() and torch.cuda.is_available():
                torch.cuda.empty_cache()
                self._oom_count += 1
                self.input_size = max(196, (int(self.input_size * 0.8) // 14) * 14)
                if self._oom_count >= 2:
                    self.model.float().to("cpu").eval()
                    self.device, self.precision = "cpu", "fp32"
                    self._cpu_fallback_due_to_oom = True
                    self._last_cuda_retry = time.monotonic()
                raise DepthModelError("CUDA 显存不足，深度模型本帧降级") from error
            self._oom_count = 0
            raise DepthModelError(f"深度推理失败：{message}") from error
