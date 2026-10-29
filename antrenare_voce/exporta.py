"""TARS – exportă vocea antrenată în ONNX (același lucru ca piper.train.export_onnx).

Torch nou folosește implicit exportatorul „dynamo”, care nu merge pe modelul VITS;
aici forțăm exportatorul clasic.
Folosire: python exporta.py --checkpoint last.ckpt --output-file model.onnx
"""
import functools

import torch

_orig = torch.onnx.export


@functools.wraps(_orig)
def _export(*args, **kwargs):
    kwargs.setdefault("dynamo", False)
    return _orig(*args, **kwargs)


torch.onnx.export = _export

from piper.train.export_onnx import main  # noqa: E402

if __name__ == "__main__":
    main()
