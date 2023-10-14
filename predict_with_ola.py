import math
import os

import time

import hydra
import torch
import logging
from pathlib import Path
import numpy as np
import torchaudio
from torchaudio.functional import resample

from src.enhance import write
from src.models import modelFactory
from src.model_serializer import SERIALIZE_KEY_MODELS, SERIALIZE_KEY_BEST_STATES, SERIALIZE_KEY_STATE
from src.utils import bold
import soundfile as sf
logger = logging.getLogger(__name__)


def overlap_and_add(chunks, overlap=256, window_len=1024):
    W = window_len
    win_left_side = np.hanning(2 * overlap)[:overlap]
    win_right_side = np.hanning(2 * overlap)[overlap:]
    window = np.concatenate((win_left_side, np.ones(W - 2 * overlap), win_right_side))
    left_window = np.concatenate((np.ones(W - overlap), win_right_side))
    right_window = np.concatenate((win_left_side, np.ones(W - overlap)))    
    n_chunks = len(chunks)
    for i in range(n_chunks):
        if i == 0:
            y = (chunks[i] * left_window).reshape(-1,)
        else:
            x_chunk = chunks[i].reshape(-1,)
            if len(x_chunk) < W:
                x_chunk = np.pad(x_chunk, (0, W - len(x_chunk)), 'constant', constant_values=0)
                x_ola = x_chunk * right_window
            else:
                x_ola = x_chunk * window
            y = np.pad(y, (0, W - overlap), 'constant', constant_values=0)
            x_ola = np.pad(x_ola, (len(y) - len(x_ola), 0), 'constant', constant_values=0)
            y += x_ola
    return y

SEGMENT_DURATION_SEC = 1

def _load_model(args):
    model_name = args.experiment.model
    checkpoint_file = Path(args.checkpoint_file)
    model = modelFactory.get_model(args)['generator']
    package = torch.load(checkpoint_file, 'cpu')
    load_best = args.continue_best
    if load_best:
        logger.info(bold(f'Loading model {model_name} from best state.'))
        model.load_state_dict(
            package[SERIALIZE_KEY_BEST_STATES][SERIALIZE_KEY_MODELS]['generator'][SERIALIZE_KEY_STATE])
    else:
        logger.info(bold(f'Loading model {model_name} from last state.'))
        model.load_state_dict(package[SERIALIZE_KEY_MODELS]['generator'][SERIALIZE_KEY_STATE])

    return model


@hydra.main(config_path="conf", config_name="main_config")  # for latest version of hydra=1.0
def main(args):
    global __file__
    __file__ = hydra.utils.to_absolute_path(__file__)

    print(args)
    model = _load_model(args)
    device = torch.device('cuda')
    model.cuda()
    filename = args.filename
    file_basename = Path(filename).stem
    output_dir = args.output
    lr_sig, sr = torchaudio.load(str(filename))
    if lr_sig.shape[1] > 1:
        lr_sig = torch.mean(lr_sig, dim=0, keepdim=True)
    if args.experiment.upsample:
        lr_sig = resample(lr_sig, sr, args.experiment.hr_sr)
        sr = args.experiment.hr_sr

    logger.info(f'lr wav shape: {lr_sig.shape}')

    segment_duration_samples = sr * SEGMENT_DURATION_SEC
    W = segment_duration_samples
    overlap_scale = 4
    overlap = segment_duration_samples//overlap_scale
    win_left_side = np.hanning(W)[:2*overlap:2]
    win_right_side = np.hanning(W)[-2*overlap::2]
    window = np.concatenate((win_left_side, np.ones(W - 2*overlap), win_right_side))
    left_window = np.concatenate((np.ones(W - overlap), win_right_side))
    right_window = np.concatenate((win_left_side, np.ones(W - overlap))) 
    n_chunks = math.ceil(lr_sig.shape[-1] / (W - overlap))
    logger.info(f'number of chunks: {n_chunks}')

    chunks_dir = output_dir + "/chunks/"
    os.makedirs(chunks_dir, exist_ok=True)



    lr_chunks = []
    for i in range(n_chunks):
        start = i * (W - overlap)
        end = min(start + W, lr_sig.shape[-1])
        lr_chunks.append(lr_sig[:, start:end])
        #chunk_filename = chunks_dir + file_basename+ 'chunk_' + str(i) + '_lr.wav'
        #write(lr_sig[:, start:end], chunk_filename, args.experiment.lr_sr)
    pr_chunks = []

    model.eval()
    pred_start = time.time()

    with torch.no_grad():
        for i, lr_chunk in enumerate(lr_chunks):
            pr_chunk = model(lr_chunk.unsqueeze(0).to(device)).squeeze(0)
            #logger.info(f'lr chunk {i} shape: {lr_chunk.shape}')
            #logger.info(f'pr chunk {i} shape: {pr_chunk.shape}')
            chunk_filename = chunks_dir + file_basename+ 'chunk_' + str(i) + '_pr.wav'
            #write(pr_chunk, chunk_filename, args.experiment.hr_sr)
            pr_chunks.append(pr_chunk.cpu())

    pred_duration = time.time() - pred_start
    logger.info(f'prediction duration: {pred_duration}')

    #pr = torch.concat(pr_chunks, dim=-1)
    pr_ola = overlap_and_add(pr_chunks, overlap=44100//overlap_scale, window_len=44100)
    #Debug para avaliar reconstrução do original
    lr = overlap_and_add(lr_chunks, overlap=11025//overlap_scale, window_len=11025)

    logger.info(f'pr wav shape: {pr_ola.shape}')

    #out_filename = os.path.join(output_dir, file_basename + '_pr.wav')
    out_filename_ola = os.path.join(output_dir, file_basename + '_pr_ola.wav')
    out_lr_filename = os.path.join(output_dir, file_basename + '_lr_ola.wav')
    os.makedirs(output_dir, exist_ok=True)

    #logger.info(f'saving to: {out_filename}, with sample_rate: {args.experiment.hr_sr}')
    logger.info(f'saving to: {out_filename_ola}, with sample_rate: {args.experiment.hr_sr}')

    #write(pr, out_filename, args.experiment.hr_sr)
    sf.write(out_filename_ola, pr_ola, args.experiment.hr_sr)
    
    #Debug
    sf.write(out_lr_filename, lr, args.experiment.lr_sr)


"""
Need to add filename and output to args.
Usage: python predict.py <dset> <experiment> +filename=<path to input file> +output=<path to output dir>
"""
if __name__ == "__main__":
    main()