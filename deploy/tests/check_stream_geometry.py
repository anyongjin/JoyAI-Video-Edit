"""Run in the installed JoyAI environment; no model weights or GPU inference needed."""

import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xvideo.models.vae import vae_compile
from xvideo.serving.joyomni_streaming import JoyOmniV2VStreamingSession, StreamingSettings
from xvideo.serving.live_api import model_dimensions


def main():
    session = JoyOmniV2VStreamingSession.__new__(JoyOmniV2VStreamingSession)
    session.runtime = SimpleNamespace(lossless_output=False, output_quality=85)
    session.postprocess_device = torch.device("cpu")
    for width, height in [(360, 480), (480, 640), (768, 1024)]:
        model_width, model_height = model_dimensions(width, height, 24)
        session.settings = StreamingSettings(
            width=model_width, height=model_height, output_width=width, output_height=height
        )
        resized = session._resize_frame(Image.new("RGB", (1200, 800), "red"))
        assert resized.size == (model_width, model_height)
        assert resized.getpixel((width - 1, height - 1)) == (255, 0, 0)
        if model_height > height:
            assert resized.getpixel((0, height)) == (0, 0, 0)
        pixels = torch.ones(1, 3, 1, model_height, model_width)
        pixels[..., :height, :width] = 0
        result = session._pack_decoded_pixels(pixels, chunk_idx=0)[0]
        with Image.open(io.BytesIO(result)) as image:
            assert image.size == (width, height)
            assert image.getpixel((width - 1, height - 1)) == (128, 128, 128)

    seen = []

    def decode(tensor, return_dict):
        seen.append(tensor.dtype)
        return (tensor,)

    with patch.object(vae_compile, "maybe_setup_decode"), patch("torch.cuda.is_available", return_value=False):
        vae_compile.warmup_decode(
            SimpleNamespace(decode=decode), 1, 2, 2, device=torch.device("cpu"),
            dtype=torch.bfloat16, input_dtype=torch.float32,
        )
    assert seen == [torch.float32, torch.float32]
    print("All three portrait crops, JPEG padding removal and graph-input warmup checks passed")


if __name__ == "__main__":
    main()
