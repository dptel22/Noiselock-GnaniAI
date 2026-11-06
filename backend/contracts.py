from typing import TypedDict, Optional, List, Dict, Any

class TranscriptResult(TypedDict):
    transcript: str
    detected_language_code: str
    latency_ms: int
    stage: str               # "stage0_default" | "stage1_context" | "stage2_ws_vad"
    raw_events: List[Dict[str, Any]]

class ExtractedEntities(TypedDict, total=False):
    plate: Optional[str]      # e.g. "KA 01 MJ 4821" or "KA01MJ4821"
    amount: Optional[int]     # e.g. 14500 or 4500
    date: Optional[str]       # e.g. "15-11" or "15/11/2026"
    raw_amount_str: Optional[str]

class VerificationOutcome(TypedDict):
    clip_id: str
    snr_label: str           # "real_street" | "15dB" | "5dB" | "0dB"
    ground_truth: Dict[str, Any]       # {"plate": "KA01MJ4821", "amount": 14500, "date": "15-11"}
    default_extracted: ExtractedEntities
    pass1_extracted: ExtractedEntities
    pass2_extracted: ExtractedEntities
    status: str              # "VERIFIED_GREEN" | "UNCERTAIN_AMBER" | "FAILED_RED"
    readback_prompt: Optional[str]
    readback_audio_path: Optional[str]
    readback_confirmed_amount: Optional[int]
    notes: Optional[str]
