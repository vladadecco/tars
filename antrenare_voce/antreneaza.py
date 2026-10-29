"""TARS – pornește antrenarea Piper (aceleași argumente ca `python -m piper.train fit`).

Diferența față de comanda standard: păstrează punctele de salvare după `val_mel`,
fără să ceară scorul MOS (care se descarcă de pe internet și poate opri antrenarea).
"""
import logging

import torch
from lightning.pytorch.callbacks import ModelCheckpoint

from piper.train.__main__ import VitsLightningCLI
from piper.train.vits.dataset import VitsDataModule
from piper.train.vits.lightning import VitsModel


def main():
    logging.basicConfig(level=logging.INFO)
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    VitsLightningCLI(
        VitsModel,
        VitsDataModule,
        trainer_defaults={
            "max_epochs": -1,
            "callbacks": [ModelCheckpoint(monitor="val_mel", mode="min", save_top_k=3, save_last=True,
                                          filename="epoch={epoch}-val_mel={val_mel:.4f}",
                                          auto_insert_metric_name=False)],
        },
    )


if __name__ == "__main__":
    main()
