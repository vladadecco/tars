"""TARS – creează o voce nouă amestecând timbrul mai multor vorbitori din modelul antrenat.

Exemplu:  python amesteca_voce.py model.onnx --mix fleurs_m003:0.5,fleurs_m007:0.3,swara_bas:0.2
          --grav 0.15 --out ro_RO-tars-medium.onnx
Rezultat: un model Piper cu o singură voce („tars”), gata de pus în TARS (modele/piper).
"""
import argparse
import json
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper


def find_speaker_table(model, n_speakers):
    best = None
    for init in model.graph.initializer:
        if len(init.dims) == 2 and init.dims[0] == n_speakers and init.dims[1] >= 64:
            if best is None or "emb_g" in init.name:
                best = init
    if best is None:
        raise SystemExit("Nu găsesc tabelul de vorbitori în model (e un model cu un singur vorbitor?)")
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", type=Path)
    ap.add_argument("--mix", required=True, help="vorbitor:pondere,vorbitor:pondere (nume sau număr)")
    ap.add_argument("--grav", type=float, default=0.0,
                    help="împinge timbrul spre media vocilor bărbătești (0…0.5) – mai grav, mai calm")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    cfg = json.loads(Path(str(args.model) + ".json").read_text(encoding="utf-8"))
    ids = cfg.get("speaker_id_map") or {}
    n = int(cfg.get("num_speakers", len(ids)))
    model = onnx.load(str(args.model))
    table = find_speaker_table(model, n)
    emb = numpy_helper.to_array(table).astype(np.float32)

    mix = np.zeros(emb.shape[1], dtype=np.float32)
    total = 0.0
    for part in args.mix.split(","):
        name, w = part.rsplit(":", 1)
        name = name.strip()
        sid = int(name) if name.isdigit() else ids[name]
        mix += float(w) * emb[sid]
        total += float(w)
    mix /= max(total, 1e-6)
    if args.grav:
        males = [i for k, i in ids.items() if "_m" in k or k.startswith("m")]
        if males:
            mix = (1 - args.grav) * mix + args.grav * emb[males].mean(0)
    # aceeași „mărime” ca vocile reale, ca să nu iasă distorsionat
    mix *= np.linalg.norm(emb, axis=1).mean() / max(np.linalg.norm(mix), 1e-6)

    # modelul final are o singură voce: înlocuim tot tabelul cu vocea nouă
    new = np.repeat(mix[None, :], emb.shape[0], axis=0).astype(np.float32)
    table.CopyFrom(numpy_helper.from_array(new, table.name))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(args.out))

    cfg["speaker_id_map"] = {"tars": 0}
    cfg["num_speakers"] = emb.shape[0]  # forma tabelului rămâne aceeași; orice număr dă vocea nouă
    cfg.setdefault("inference", {}).update({"length_scale": 1.1, "noise_scale": 0.6, "noise_w": 0.7})
    Path(str(args.out) + ".json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gata: {args.out}")


if __name__ == "__main__":
    main()
