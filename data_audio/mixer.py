import numpy as np
import scipy.signal as signal
import wave
import os
from typing import Tuple

def butter_bandpass(lowcut: float = 300.0, highcut: float = 3400.0, fs: int = 8000, order: int = 4):
    """
    4th-order Butterworth bandpass filter representing the standard 300-3400 Hz
    PSTN / 2G-3G cellular telephony voice channel.
    """
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = signal.butter(order, [low, high], btype='band')
    return b, a

def apply_telephony_filter(audio: np.ndarray, fs: int = 8000) -> np.ndarray:
    """
    Applies the 300-3400 Hz bandpass filter to input audio array.
    """
    b, a = butter_bandpass(300.0, 3400.0, fs, order=4)
    filtered = signal.lfilter(b, a, audio)
    return filtered

def compute_rms(audio: np.ndarray) -> float:
    """
    Computes root mean square (RMS) energy of an audio array.
    """
    return float(np.sqrt(np.mean(audio ** 2) + 1e-12))

def mix_at_snr(signal_audio: np.ndarray, noise_audio: np.ndarray, target_snr_db: float) -> np.ndarray:
    """
    Mixes signal and noise at a calibrated target RMS SNR in dB.
    Formula: target_noise_rms = signal_rms / (10 ** (target_snr_db / 20))
    """
    # Ensure noise matches signal length
    sig_len = len(signal_audio)
    if len(noise_audio) < sig_len:
        reps = int(np.ceil(sig_len / len(noise_audio)))
        noise_audio = np.tile(noise_audio, reps)[:sig_len]
    else:
        noise_audio = noise_audio[:sig_len]

    sig_rms = compute_rms(signal_audio)
    noise_rms = compute_rms(noise_audio)

    if noise_rms == 0:
        return signal_audio

    # Target noise RMS
    target_noise_rms = sig_rms / (10.0 ** (target_snr_db / 20.0))
    gain = target_noise_rms / noise_rms
    scaled_noise = noise_audio * gain

    mixed = signal_audio + scaled_noise

    # Peak normalization to prevent 16-bit integer clipping
    peak = np.max(np.abs(mixed))
    if peak > 0.95:
        mixed = (mixed / peak) * 0.95

    return mixed

def generate_synthetic_street_noise(duration_sec: float, fs: int = 8000) -> np.ndarray:
    """
    Generates synthetic Bengaluru street noise profile:
    - Low-frequency engine rumble (pink noise / filtered noise at 40-200 Hz)
    - Distant traffic hiss (500-1500 Hz)
    - Intermittent short auto-rickshaw / horn bursts (<150 ms)
    """
    num_samples = int(duration_sec * fs)
    t = np.linspace(0, duration_sec, num_samples, endpoint=False)
    
    # 1. Base traffic rumble
    white = np.random.randn(num_samples)
    b_low, a_low = signal.butter(2, 400.0 / (0.5 * fs), btype='low')
    rumble = signal.lfilter(b_low, a_low, white) * 0.4

    # 2. Add 2 short horn bursts (<150ms) to test VAD resilience
    horn = np.zeros(num_samples)
    if duration_sec > 2.0:
        # Horn 1 at t=1.2s (duration 120ms at 850 Hz + 1100 Hz dual tone)
        idx_start = int(1.2 * fs)
        burst_len = int(0.12 * fs)
        t_burst = t[idx_start:idx_start+burst_len]
        burst = (np.sin(2 * np.pi * 850 * t_burst) + np.sin(2 * np.pi * 1100 * t_burst)) * 0.5
        horn[idx_start:idx_start+burst_len] = burst

    combined_noise = rumble + horn
    norm = np.max(np.abs(combined_noise))
    if norm > 0:
        combined_noise = combined_noise / norm
    return combined_noise

def generate_synthetic_speech_tone(duration_sec: float, fs: int = 8000) -> np.ndarray:
    """
    Generates speech-like harmonic carrier for testing DSP pipeline when no live mic is attached.
    Formants at 500 Hz, 1500 Hz, 2500 Hz with speech amplitude envelope.
    """
    num_samples = int(duration_sec * fs)
    t = np.linspace(0, duration_sec, num_samples, endpoint=False)
    
    # Natural syllabic envelope (~3 syllables/sec)
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 3.5 * t))
    # Pauses inside dictated number: pause at t=1.5s to 2.1s (600ms pause)
    pause_mask = np.ones(num_samples)
    p_start, p_end = int(1.5 * fs), int(2.1 * fs)
    if p_end < num_samples:
        pause_mask[p_start:p_end] = 0.02
    envelope = envelope * pause_mask

    carrier = (
        0.5 * np.sin(2 * np.pi * 180 * t) +
        0.3 * np.sin(2 * np.pi * 500 * t) +
        0.2 * np.sin(2 * np.pi * 1500 * t) +
        0.1 * np.sin(2 * np.pi * 2500 * t)
    )
    speech = carrier * envelope
    return speech

def save_wav_pcm16(filepath: str, audio: np.ndarray, fs: int = 8000):
    """
    Saves float audio array (-1.0 to 1.0) as 16-bit signed PCM WAV file.
    """
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(filepath, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2) # 16-bit
        wf.setframerate(fs)
        wf.writeframes(audio_int16.tobytes())

def load_wav_pcm16(filepath: str) -> Tuple[np.ndarray, int]:
    """
    Loads 16-bit signed PCM WAV file to float numpy array.
    """
    with wave.open(filepath, 'rb') as wf:
        fs = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, fs
