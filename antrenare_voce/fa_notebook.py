"""Generează notebook-ul Colab „TARS_antrenare_voce.ipynb”."""
import json
import sys

REPO_RAW = "https://raw.githubusercontent.com/vladadecco/tars/main/antrenare_voce"
PIPER_COMMIT = "74ac24e"

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)})


def code(s, title=None):
    meta = {"cellView": "form"} if title else {}
    src = (f"#@title {title}\n" if title else "") + s.strip("\n")
    cells.append({"cell_type": "code", "metadata": meta, "execution_count": None, "outputs": [],
                  "source": src.splitlines(True)})


md("""
# 🎙️ TARS – antrenarea vocii proprii

**O singură dată:** sus, *Runtime → Change runtime type → T4 GPU → Save*.

**De fiecare dată:** *Runtime → Run all* (Ctrl+F9). Totul se salvează în Google Drive, în dosarul **TARS_voce**.
Dacă Colab se oprește (după câteva ore pe varianta gratuită), apeși din nou **Run all**: continuă de unde a rămas.

Pași: 1️⃣ pregătire → 2️⃣ date → 3️⃣ antrenare → 4️⃣ ascultă vocile → 5️⃣ amestecă vocea TARS.
""")

code(r'''
from google.colab import drive
drive.mount('/content/drive')
import os, pathlib, subprocess, json, glob
W = pathlib.Path('/content/drive/MyDrive/TARS_voce'); W.mkdir(parents=True, exist_ok=True)
for d in ('set', 'antrenare', 'model', 'final', 'swara'):
    (W/d).mkdir(exist_ok=True)
import torch
assert torch.cuda.is_available(), '⚠️ Nu ai placa video: Runtime → Change runtime type → T4 GPU'
print('✅ Drive conectat, placa video:', torch.cuda.get_device_name(0))
''', "1️⃣ Pregătire: Drive + placa video")

code(rf'''
%cd /content
if not os.path.exists('/content/piper1-gpl'):
    !git clone -q https://github.com/OHF-Voice/piper1-gpl.git && cd piper1-gpl && git checkout -q {PIPER_COMMIT}
%cd /content/piper1-gpl
!apt-get -qq install -y build-essential cmake ninja-build > /dev/null
!pip -q install -e '.[train]' transformers scikit-learn soundfile > /dev/null
!pip -q install cython > /dev/null && ./build_monotonic_align.sh > /dev/null 2>&1 && python3 setup.py -q build_ext --inplace > /dev/null 2>&1
for f in ('pregateste_date.py', 'amesteca_voce.py', 'antreneaza.py', 'exporta.py'):
    !wget -q -O /content/{{f}} {REPO_RAW}/{{f}}
print('✅ Unelte instalate')
''', "1️⃣ Instalare unelte (≈5 min)")

code(r'''
meta = W/'set'/'metadata.csv'
if meta.exists():
    print('✅ Datele sunt deja pregătite:', sum(1 for _ in open(meta)), 'fraze')
else:
    F = pathlib.Path('/content/fleurs'); F.mkdir(exist_ok=True)
    base = 'https://huggingface.co/datasets/google/fleurs/resolve/main/data/ro_ro'
    for s in ('train', 'dev', 'test'):
        if not (F/s).exists():
            !wget -q -O /content/fleurs/{s}.tsv {base}/{s}.tsv
            !wget -q -O /content/{s}.tar.gz {base}/audio/{s}.tar.gz && tar xzf /content/{s}.tar.gz -C /content/fleurs && rm /content/{s}.tar.gz
    sw = ['--swara', str(W/'swara')] if any((W/'swara').iterdir()) else []
    subprocess.run(['python3', '/content/pregateste_date.py', '--fleurs', str(F), '--out', str(W/'set'), *sw], check=True)
print(open(W/'set'/'vorbitori.json').read())
''', "2️⃣ Date: înregistrări românești libere (FLEURS) + SWARA dacă e pus în Drive (≈20 min prima dată)")

code(r'''
#@markdown Câte ore să ruleze acum (Colab gratuit oprește după ~3–4 ore):
ORE = 3 #@param {type:"number"}
nspk = len(json.load(open(W/'set'/'vorbitori.json')))
last = sorted(glob.glob(str(W/'antrenare'/'**'/'last.ckpt'), recursive=True), key=os.path.getmtime)
start = ['--ckpt_path', last[-1]] if last else []
if not last:
    mihai = '/content/mihai.ckpt'
    if not os.path.exists(mihai):
        !wget -q -O /content/mihai.ckpt "https://huggingface.co/datasets/rhasspy/piper-checkpoints/resolve/main/ro/ro_RO/mihai/medium/epoch%3D7809-step%3D1558760.ckpt"
    start = ['--model.warmstart_ckpt', mihai]
print('▶️', 'Continui de la' if last else 'Pornesc de la vocea Mihai', last[-1] if last else '')
# frazele foarte lungi umplu memoria plăcii video: le păstrăm pe cele de max. 15 s
import soundfile as sf
mt = W/'set'/'metadata_antrenare.csv'
if not mt.exists():
    keep = []
    for line in open(W/'set'/'metadata.csv', encoding='utf-8'):
        f = line.split('|', 1)[0]
        try:
            if sf.info(str(W/'set'/'wav'/f)).duration <= 15: keep.append(line)
        except Exception: pass
    mt.write_text(''.join(keep), encoding='utf-8')
print('Fraze folosite:', sum(1 for _ in open(mt, encoding='utf-8')))
log = W/'antrenare'/'jurnal.txt'
cmd = ['python3', '/content/antreneaza.py', 'fit',
       '--data.voice_name', 'tars', '--data.csv_path', str(mt),
       '--data.audio_dir', str(W/'set'/'wav'), '--model.sample_rate', '22050',
       '--data.espeak_voice', 'ro', '--data.cache_dir', '/content/cache',
       '--data.config_path', str(W/'model'/'config.json'), '--data.batch_size', '12',
       '--data.num_workers', '2', '--model.num_speakers', str(nspk), '--model.mos_metric', 'none',
       '--trainer.default_root_dir', str(W/'antrenare'), '--trainer.max_time', f'00:{int(ORE):02d}:{int(ORE%1*60):02d}:00',
       '--trainer.check_val_every_n_epoch', '5', '--trainer.log_every_n_steps', '20', *start]
print('Jurnalul se scrie în Drive:', log)
with open(log, 'a', encoding='utf-8') as lf:
    lf.write('\n===== pornire ' + __import__('datetime').datetime.now().isoformat() + '\n'); lf.flush()
    rc = subprocess.run(cmd, cwd='/content/piper1-gpl', stdout=lf, stderr=subprocess.STDOUT).returncode
if rc != 0:
    print('❌ Antrenarea s-a oprit cu eroare. Ultimele rânduri din jurnal:')
    print(''.join(open(log, encoding='utf-8', errors='ignore').readlines()[-40:]))
    raise SystemExit('Spune-i lui Claude: „eroare la antrenare”.')
print('✅ Pauză salvată. Poți asculta acum (pasul 4) sau rula din nou mai târziu.')
''', "3️⃣ Antrenare (se oprește singură după numărul de ore ales)")

code(r'''
import IPython.display as ipd
last = sorted(glob.glob(str(W/'antrenare'/'**'/'last.ckpt'), recursive=True), key=os.path.getmtime)[-1]
onnx_path = W/'model'/'ro_RO-tars_toti-medium.onnx'
subprocess.run(['python3', '/content/exporta.py', '--checkpoint', last, '--output-file', str(onnx_path)],
               cwd='/content/piper1-gpl', check=True)
!cp "{W}/model/config.json" "{onnx_path}.json"
from piper import PiperVoice, SynthesisConfig
import numpy as np
v = PiperVoice.load(str(onnx_path))
TEXT = 'Bună ziua, domnule. Sistemele sunt pregătite, iar cafeaua, din păcate, rămâne în grija dumneavoastră.'
for name, sid in sorted(v.config.speaker_id_map.items(), key=lambda x: x[1]):
    a = np.concatenate([c.audio_float_array for c in v.synthesize(TEXT, SynthesisConfig(speaker_id=sid, length_scale=1.1))])
    print(f'— {name} (nr. {sid})'); ipd.display(ipd.Audio(a, rate=v.config.sample_rate))
''', "4️⃣ Ascultă toate vocile învățate (alege-le pe cele care îți plac)")

code(r'''
#@markdown Scrie vocile alese și cât din fiecare (ex. `fleurs_m003:0.5,fleurs_m001:0.5`):
AMESTEC = "fleurs_m000:1" #@param {type:"string"}
#@markdown Cât de grav (0 = deloc, 0.3 = mult):
GRAV = 0.1 #@param {type:"slider", min:0, max:0.5, step:0.05}
final = W/'final'/'ro_RO-tars-medium.onnx'
subprocess.run(['python3', '/content/amesteca_voce.py', str(W/'model'/'ro_RO-tars_toti-medium.onnx'),
                '--mix', AMESTEC, '--grav', str(GRAV), '--out', str(final)], check=True)
from piper import PiperVoice, SynthesisConfig
import numpy as np, IPython.display as ipd
v = PiperVoice.load(str(final))
for t in ('Bună ziua, domnule. Mâine va fi înnorat, cu cincisprezece grade.',
          'Am verificat mailurile. Nimic urgent, doar o factură și două oferte.'):
    a = np.concatenate([c.audio_float_array for c in v.synthesize(t, SynthesisConfig(length_scale=1.1))])
    ipd.display(ipd.Audio(a, rate=v.config.sample_rate))
print('✅ Vocea TARS e în Drive → TARS_voce/final. Spune-i lui Claude „gata vocea” și o pune în TARS.')
''', "5️⃣ Creează vocea TARS (amestec)")

nb = {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"},
      "kernelspec": {"display_name": "Python 3", "name": "python3"}}, "nbformat": 4, "nbformat_minor": 0}
out = sys.argv[1] if len(sys.argv) > 1 else "TARS_antrenare_voce.ipynb"
json.dump(nb, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(out)
