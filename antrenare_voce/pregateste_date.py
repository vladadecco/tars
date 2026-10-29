"""TARS – pregătirea datelor pentru antrenarea vocii (Piper, mai mulți vorbitori).

Ia înregistrări românești libere (Google FLEURS, CC BY 4.0) și, opțional, SWARA,
recunoaște automat vorbitorii după timbru (WavLM) și scrie metadata.csv:
    fisier.wav|vorbitor|Text.
"""
import argparse
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
import torch


def load_fleurs(root: Path):
    """root/<split>.tsv + root/<split>/*.wav"""
    rows = []
    for split in ("train", "dev", "test"):
        tsv = root / f"{split}.tsv"
        if not tsv.exists():
            continue
        for line in tsv.read_text(encoding="utf-8").splitlines():
            c = line.split("\t")
            if len(c) < 7:
                continue
            wav = root / split / c[1]
            if wav.exists():
                rows.append({"wav": wav, "text": c[2].strip(), "gender": c[6].strip().upper(), "src": "fleurs"})
    return rows


def load_swara(root: Path):
    """SWARA: un dosar pe vorbitor; textul în .txt/.lab cu același nume sau într-o listă „id text”."""
    rows = []
    for spk in sorted(p for p in root.iterdir() if p.is_dir()):
        texts = {}
        for lst in spk.rglob("*.txt"):
            for line in lst.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    texts[parts[0]] = parts[1]
        for wav in spk.rglob("*.wav"):
            t = None
            for ext in (".txt", ".lab"):
                f = wav.with_suffix(ext)
                if f.exists():
                    t = f.read_text(encoding="utf-8", errors="ignore").strip()
            t = t or texts.get(wav.stem)
            if t and len(t.split()) > 1:
                rows.append({"wav": wav, "text": t, "speaker": f"swara_{spk.name}", "src": "swara"})
    return rows


@torch.no_grad()
def embed(rows, device):
    from transformers import AutoFeatureExtractor, WavLMForXVector
    fe = AutoFeatureExtractor.from_pretrained("microsoft/wavlm-base-plus-sv")
    model = WavLMForXVector.from_pretrained("microsoft/wavlm-base-plus-sv").to(device).eval()
    out = []
    for i, r in enumerate(rows):
        a, sr = sf.read(r["wav"], dtype="float32", always_2d=False)
        if a.ndim > 1:
            a = a.mean(1)
        if sr != 16000:
            import librosa
            a = librosa.resample(a, orig_sr=sr, target_sr=16000)
        a = a[: 16000 * 12]
        x = fe(a, sampling_rate=16000, return_tensors="pt").to(device)
        e = model(**x).embeddings[0]
        out.append(torch.nn.functional.normalize(e, dim=-1).cpu().numpy())
        if i % 200 == 0:
            print(f"  amprente de voce: {i}/{len(rows)}", flush=True)
    return np.stack(out)


def cluster(emb, threshold):
    from sklearn.cluster import AgglomerativeClustering
    cl = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                 distance_threshold=threshold)
    return cl.fit_predict(emb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleurs", type=Path)
    ap.add_argument("--swara", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--min-utt", type=int, default=40, help="minim de fraze ca să fie vorbitor separat")
    ap.add_argument("--threshold", type=float, default=0.05)
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    rows = []
    if args.fleurs:
        rows += load_fleurs(args.fleurs)
    if args.swara and args.swara.exists():
        rows += load_swara(args.swara)
    print(f"Fraze găsite: {len(rows)}")

    # FLEURS nu spune cine vorbește: grupăm după timbru, separat pe bărbați și femei
    fl = [r for r in rows if r["src"] == "fleurs"]
    for g in ("MALE", "FEMALE"):
        part = [r for r in fl if r["gender"] == g]
        if not part:
            continue
        cache = args.out / f"amprente_{g.lower()}.npy"
        args.out.mkdir(parents=True, exist_ok=True)
        if cache.exists() and len(np.load(cache)) == len(part):
            emb = np.load(cache)
        else:
            emb = embed(part, device)
            np.save(cache, emb)
        labels = cluster(emb, args.threshold)
        for r, lab in zip(part, labels):
            r["speaker"] = f"fleurs_{g[0].lower()}{lab:03d}"

    counts = {}
    for r in rows:
        counts[r.get("speaker")] = counts.get(r.get("speaker"), 0) + 1
    keep = [r for r in rows if r.get("speaker") and counts[r["speaker"]] >= args.min_utt]

    wav_dir = args.out / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, r in enumerate(keep):
        name = f"{r['speaker']}_{i:05d}{r['wav'].suffix}"
        dst = wav_dir / name
        if not dst.exists():
            shutil.copy(r["wav"], dst)
        text = " ".join(r["text"].replace("|", " ").split())
        meta.append((name, r["speaker"], text))
    with open(args.out / "metadata.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_NONE, escapechar="\\")
        w.writerows(meta)

    spk = sorted({m[1] for m in meta})
    info = {s: sum(1 for m in meta if m[1] == s) for s in spk}
    (args.out / "vorbitori.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(f"Vorbitori păstrați: {len(spk)} | fraze: {len(meta)}")
    for s, n in info.items():
        print(f"  {s}: {n} fraze")


if __name__ == "__main__":
    main()
