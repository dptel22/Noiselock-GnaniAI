import os
import sys
import unittest
import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from data_audio.mixer import (
    butter_bandpass,
    apply_telephony_filter,
    compute_rms,
    mix_at_snr,
    generate_synthetic_speech_tone,
    generate_synthetic_street_noise
)
from backend.verifier import (
    extract_entities,
    format_plate_for_timbre,
    format_date_for_timbre,
    build_readback_prompt,
    verify_two_pass
)
from backend.gnani_client import GLOBAL_GNANI_LOCK, GnaniClient

class TestNoiseLockPipeline(unittest.TestCase):

    def test_butter_bandpass_telephony_filter(self):
        fs = 8000
        b, a = butter_bandpass(300, 3400, fs, order=4)
        self.assertTrue(len(b) > 0 and len(a) > 0)
        
        # Test signal with 50 Hz rumble (should be attenuated)
        t = np.linspace(0, 1.0, fs, endpoint=False)
        sig_50hz = np.sin(2 * np.pi * 50 * t)
        filtered = apply_telephony_filter(sig_50hz, fs=fs)
        
        rms_before = compute_rms(sig_50hz)
        rms_after = compute_rms(filtered)
        # 50 Hz should be substantially attenuated by 300 Hz highpass cut
        self.assertLess(rms_after, rms_before * 0.2)

    def test_snr_mixer(self):
        fs = 8000
        speech = generate_synthetic_speech_tone(1.0, fs=fs)
        noise = generate_synthetic_street_noise(1.0, fs=fs)
        
        mixed_15 = mix_at_snr(speech, noise, target_snr_db=15.0)
        mixed_0 = mix_at_snr(speech, noise, target_snr_db=0.0)
        
        self.assertEqual(len(mixed_15), len(speech))
        self.assertEqual(len(mixed_0), len(speech))
        self.assertLessEqual(np.max(np.abs(mixed_15)), 1.0)
        self.assertLessEqual(np.max(np.abs(mixed_0)), 1.0)

    def test_extract_entities_plate_suffix_masking(self):
        # Suffix of DL 03 CA 1234 is 1234. It must NOT be treated as amount!
        transcript = "Vehicle DL 03 CA 1234 paying Rs 14,500 on 15th November."
        entities = extract_entities(transcript)
        
        self.assertEqual(entities["plate"], "DL03CA1234")
        self.assertEqual(entities["amount"], 14500)
        self.assertIn("15th November", entities["date"])

    def test_extract_entities_with_kannada_prefix(self):
        transcript = "Haudu sir, KA 01 MJ 4821 amount 4500 rupees date 15/11."
        entities = extract_entities(transcript)
        self.assertEqual(entities["plate"], "KA01MJ4821")
        self.assertEqual(entities["amount"], 4500)
        self.assertIn("15/11", entities["date"])

    def test_format_plate_for_timbre(self):
        self.assertEqual(format_plate_for_timbre("KA01MJ4821"), "KA 01 MJ 4821")
        self.assertEqual(format_plate_for_timbre("DL03CA1234"), "DL 03 CA 1234")
        self.assertEqual(format_plate_for_timbre("KA 01 MJ 4821"), "KA 01 MJ 4821")

    def test_format_date_for_timbre(self):
        self.assertEqual(format_date_for_timbre("15/11"), "15/11/2026")
        self.assertEqual(format_date_for_timbre("15-11-2026"), "15/11/2026")
        self.assertEqual(format_date_for_timbre("5/11"), "05/11/2026")

    def test_build_readback_prompt(self):
        prompt_kn = build_readback_prompt("kn-IN", 4500, 14500, "KA01MJ4821", "15/11")
        self.assertIn("<pauses>", prompt_kn)
        self.assertIn("KA 01 MJ 4821", prompt_kn)
        self.assertIn("₹4,500", prompt_kn)
        self.assertIn("₹14,500", prompt_kn)

        prompt_hi = build_readback_prompt("hi-IN", 4500, 14500, "KA01MJ4821", "15/11")
        self.assertIn("<pauses>", prompt_hi)
        self.assertIn("KA 01 MJ 4821", prompt_hi)

    def test_verify_two_pass_agreement(self):
        gt = {"plate": "KA01MJ4821", "amount": 14500, "date": "15-11"}
        
        # Case 1: High confidence match
        p1 = {"plate": "KA01MJ4821", "amount": 14500, "date": "15-11"}
        p2 = {"plate": "KA01MJ4821", "amount": 14500, "date": "15-11"}
        outcome = verify_two_pass("test_1", "15dB", gt, p1, p1, p2, "kn-IN")
        self.assertEqual(outcome["status"], "VERIFIED_GREEN")
        self.assertIsNone(outcome["readback_prompt"])

        # Case 2: Discrepancy (Pass 1 dropped digit)
        p1_noisy = {"plate": "KA01MJ4821", "amount": 4500, "date": "15-11"}
        p2_context = {"plate": "KA01MJ4821", "amount": 14500, "date": "15-11"}
        outcome_disc = verify_two_pass("test_2", "0dB", gt, p1_noisy, p1_noisy, p2_context, "kn-IN")
        self.assertEqual(outcome_disc["status"], "UNCERTAIN_AMBER")
        self.assertIsNotNone(outcome_disc["readback_prompt"])

    def test_gnani_client_lock_singleton(self):
        client1 = GnaniClient()
        client2 = GnaniClient()
        self.assertIs(client1.lock, GLOBAL_GNANI_LOCK)
        self.assertIs(client2.lock, GLOBAL_GNANI_LOCK)

if __name__ == "__main__":
    unittest.main(verbosity=2)
