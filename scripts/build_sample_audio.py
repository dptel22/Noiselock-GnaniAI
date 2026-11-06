import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import json
import numpy as np
from data_audio.mixer import (
    generate_synthetic_speech_tone,
    generate_synthetic_street_noise,
    mix_at_snr,
    apply_telephony_filter,
    save_wav_pcm16
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(ROOT_DIR, "data_audio", "corpus_manifest.json")
WAVS_DIR = os.path.join(ROOT_DIR, "data_audio", "wavs")

def build_corpus_audio():
    os.makedirs(WAVS_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        clips = json.load(f)

    fs = 8000
    duration = 4.5

    for clip in clips:
        clip_id = clip["clip_id"]
        snr_label = clip["snr_label"]
        out_path = os.path.join(ROOT_DIR, clip["audio_path"])

        if os.path.exists(out_path):
            continue

        # Synthesize audio with target noise
        speech = generate_synthetic_speech_tone(duration, fs=fs)
        noise = generate_synthetic_street_noise(duration, fs=fs)

        if snr_label == "real_street":
            # Simulate real street call with 5 dB SNR and heavy ambient
            snr_db = 5.0
        elif snr_label == "15dB":
            snr_db = 15.0
        elif snr_label == "5dB":
            snr_db = 5.0
        elif snr_label == "0dB":
            snr_db = 0.0
        else:
            snr_db = 10.0

        mixed = mix_at_snr(speech, noise, target_snr_db=snr_db)
        filtered = apply_telephony_filter(mixed, fs=fs)
        save_wav_pcm16(out_path, filtered, fs=fs)
        print(f"Synthesized: {clip_id} -> {out_path} ({snr_label})")

if __name__ == "__main__":
    build_corpus_audio()
