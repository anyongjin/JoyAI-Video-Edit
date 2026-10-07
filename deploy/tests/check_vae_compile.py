"""Unwarmed stream shapes must never start compilation in a user session."""

import sys
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xvideo.models.vae import vae_compile


class VAE(torch.nn.Module):
    def _encode(self, tensor):
        return tensor

    def _decode(self, tensor):
        return tensor

    def encode(self, tensor):
        return self._encode(tensor)

    def decode(self, tensor, return_dict=False):
        return (self._decode(tensor),)


def main():
    compiled_calls = []

    def compile_stub(core, **kwargs):
        def compiled(tensor, *args, **kwargs):
            compiled_calls.append((tuple(tensor.shape), tensor.dtype))
            return core(tensor, *args, **kwargs)
        return compiled

    vae = VAE()
    with patch("torch.compile", compile_stub), patch("torch.cuda.is_available", return_value=False):
        vae_compile.warmup_encode(vae, 3, 8, 8, torch.device("cpu"), torch.float32)
        vae_compile.warmup_decode(vae, 3, 8, 8, torch.device("cpu"), torch.float32, autocast=False)
        with torch.no_grad():
            warmed = vae_compile.prep_input(torch.zeros(1, 3, 1, 8, 8))
            before = len(compiled_calls)
            vae.encode(warmed)
            vae.decode(warmed)
            assert len(compiled_calls) == before + 2, "warmed stream shapes keep compiled acceleration"
            before = len(compiled_calls)
            for shape, dtype in [((1, 3, 1, 16, 8), torch.float32), ((1, 3, 1, 8, 8), torch.bfloat16)]:
                cold = vae_compile.prep_input(torch.zeros(shape, dtype=dtype))
                assert torch.equal(vae.encode(cold), cold)
                assert torch.equal(vae.decode(cold)[0], cold)
            assert len(compiled_calls) == before, "cold shapes/dtypes must use eager inference, without live compilation"
    print("Warmed acceleration and cold-shape/dtype eager inference checks passed")


if __name__ == "__main__":
    main()
