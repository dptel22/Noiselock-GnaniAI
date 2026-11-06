import asyncio
import os
import json
import time
import requests
import websockets
from typing import Optional, Dict, Any, List
from backend.contracts import TranscriptResult

# Global single-flight lock to guarantee at most 1 active API call across the entire process
# This is mandatory to prevent HTTP 429 RATE_LIMITED: "Only 1 active request is allowed at a time."
GLOBAL_GNANI_LOCK = asyncio.Lock()

GNANI_REST_URL = os.environ.get("GNANI_REST_URL", "https://api.vachana.ai/stt/v3")
GNANI_WS_URL = os.environ.get("GNANI_WS_URL", "wss://api.vachana.ai/stt/v3/stream")
GNANI_TTS_URL = os.environ.get("GNANI_TTS_URL", "https://api.vachana.ai/api/v1/tts/inference")

class GnaniClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GNANI_API_KEY", "")
        self.lock = GLOBAL_GNANI_LOCK

    async def transcribe_rest(
        self,
        audio_bytes: bytes,
        language_code: str = "indic-auto",
        format_mode: str = "transcribe",
        topic: Optional[str] = None,
        previous_text: Optional[str] = None,
        keyterms: Optional[List[str]] = None,
        substitution_map: Optional[List[Dict[str, Any]]] = None,
        stage: str = "stage0_default"
    ) -> TranscriptResult:
        """
        Runs REST STT with exponential backoff under the global lock.
        """
        async with self.lock:
            start_time = time.time()
            headers = {
                "x-api-key-id": self.api_key,
            }
            data: Dict[str, Any] = {
                "model": "prisma-v3.0",
                "language_code": language_code,
                "format": format_mode,
            }
            if topic:
                data["topic"] = topic
            if previous_text:
                data["previous_text"] = previous_text
            if keyterms:
                data["keyterms"] = json.dumps(keyterms)
            if substitution_map:
                data["enable_substitution"] = "true"
                data["substitution_map"] = json.dumps(substitution_map)

            files = {
                "audio": ("audio.wav", audio_bytes, "audio/wav")
            }

            max_retries = 3
            backoffs = [0.5, 1.0, 2.0]
            last_err = None

            for attempt in range(max_retries):
                try:
                    # Run synchronous requests in thread pool to not block asyncio loop
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(
                        None,
                        lambda: requests.post(GNANI_REST_URL, headers=headers, data=data, files=files, timeout=10)
                    )
                    if response.status_code == 200:
                        res_json = response.json()
                        latency_ms = int((time.time() - start_time) * 1000)
                        transcript = res_json.get("transcript", "") or res_json.get("text", "")
                        det_lang = res_json.get("detected_language_code", language_code)
                        return {
                            "transcript": transcript,
                            "detected_language_code": det_lang,
                            "latency_ms": latency_ms,
                            "stage": stage,
                            "raw_events": [res_json]
                        }
                    elif response.status_code in [429, 503, 504]:
                        await asyncio.sleep(backoffs[attempt])
                        continue
                    else:
                        response.raise_for_status()
                except Exception as e:
                    last_err = e
                    await asyncio.sleep(backoffs[attempt])

            # If API fails or mock/offline mode
            latency_ms = int((time.time() - start_time) * 1000)
            return {
                "transcript": f"[REST Error/Fallback: {last_err}]",
                "detected_language_code": language_code,
                "latency_ms": latency_ms,
                "stage": stage,
                "raw_events": [{"error": str(last_err)}]
            }

    async def transcribe_stream_ws(
        self,
        pcm_bytes: bytes,
        language_code: str = "kn-IN,en-IN,hi-IN",
        format_mode: str = "transcribe",
        topic: Optional[str] = "Auto finance EMI collection",
        previous_text: Optional[str] = None,
        keyterms: Optional[List[str]] = None,
        substitution_map: Optional[List[Dict[str, Any]]] = None,
        vad_threshold: str = "0.75",
        min_speech_ms: str = "250",
        min_silence_ms: str = "700",
        sample_rate: int = 8000,
        inject_mid_call: bool = True,
        stage: str = "stage2_ws_vad"
    ) -> TranscriptResult:
        """
        Runs WebSocket streaming STT:
        - 1024-byte frames sent every 64ms for 8kHz (or 32ms for 16kHz)
        - Injects mid-call update_settings control frame
        - Sends {"type": "eos"} and gathers final transcript
        """
        async with self.lock:
            start_time = time.time()
            headers = {
                "x-api-key-id": self.api_key,
                "model": "prisma-v3.0",
                "language_code": language_code,
                "format": format_mode,
                "x-sample-rate": str(sample_rate),
                "x-vad-threshold": vad_threshold,
                "x-min-speech-ms": min_speech_ms,
                "x-min-silence-ms": min_silence_ms,
            }
            if topic:
                headers["topic"] = topic
            if previous_text:
                headers["previous_text"] = previous_text
            if keyterms:
                headers["keyterms"] = json.dumps(keyterms)
            if substitution_map:
                headers["enable_substitution"] = "true"
                headers["substitution_map"] = json.dumps(substitution_map)

            events: List[Dict[str, Any]] = []
            final_transcript = ""
            det_lang = language_code

            try:
                async with websockets.connect(
                    GNANI_WS_URL,
                    additional_headers=headers,
                    ping_interval=10,
                    ping_timeout=10,
                    close_timeout=5
                ) as ws:
                    # Audio chunk pacing: 1024 bytes per frame = 512 samples
                    frame_size = 1024
                    frame_delay = 0.064 if sample_rate == 8000 else 0.032
                    mid_call_sent = False
                    total_bytes = len(pcm_bytes)

                    for offset in range(0, total_bytes, frame_size):
                        chunk = pcm_bytes[offset:offset+frame_size]
                        await ws.send(chunk)

                        # Mid-call context injection at 30% through the call
                        if inject_mid_call and not mid_call_sent and offset > total_bytes * 0.3:
                            mid_call_frame = {
                                "type": "update_settings",
                                "format": format_mode,
                                "bias": {
                                    "list": keyterms or ["KA 01 MJ 4821", "EMI", "NACH", "November"]
                                },
                                "substitution": {
                                    "enabled": bool(substitution_map),
                                    "map": substitution_map or [
                                        {"patterns": ["ಕೆ ಎ ಜೀರೋ ಒನ್", "K A 0 1 N N"], "replacement": "KA 01 MJ"}
                                    ]
                                }
                            }
                            await ws.send(json.dumps(mid_call_frame))
                            mid_call_sent = True

                        await asyncio.sleep(frame_delay)

                    # Send EOS frame
                    await ws.send(json.dumps({"type": "eos"}))

                    # Collect responses until server closes socket
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=4.0)
                            if isinstance(msg, str):
                                data = json.loads(msg)
                                events.append(data)
                                if "transcript" in data:
                                    final_transcript = data["transcript"]
                                if "detected_language_code" in data:
                                    det_lang = data["detected_language_code"]
                                if data.get("is_final", False) or data.get("type") == "final":
                                    final_transcript = data.get("transcript", final_transcript)
                                    break
                        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                            break

            except Exception as e:
                events.append({"error": str(e)})
                if not final_transcript:
                    final_transcript = f"[WS Fallback: {e}]"

            latency_ms = int((time.time() - start_time) * 1000)
            return {
                "transcript": final_transcript,
                "detected_language_code": det_lang,
                "latency_ms": latency_ms,
                "stage": stage,
                "raw_events": events
            }

    async def synthesize_readback_timbre(
        self,
        text: str,
        voice: str = "Avinash",
        detected_language: str = "kn-IN",
        output_filepath: str = "data_audio/wavs/readback_output.wav"
    ) -> Optional[str]:
        """
        Synthesizes omnilingual readback audio using Timbre v3.0.
        Executed strictly under the global lock to prevent 429 collision with active STT sockets.
        """
        async with self.lock:
            headers = {
                "x-api-key-id": self.api_key,
                "Content-Type": "application/json"
            }
            body = {
                "text": text,
                "voice": voice,
                "model": "timbre-v3.0",
                "language": detected_language if detected_language != "indic-auto" else "auto",
                "speed": 1.0,
                "audio_config": {
                    "sample_rate": 8000,
                    "num_channels": 1,
                    "sample_width": 2,
                    "encoding": "linear_pcm",
                    "container": "wav"
                }
            }

            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: requests.post(GNANI_TTS_URL, headers=headers, json=body, timeout=12)
                )
                if response.status_code == 200:
                    os.makedirs(os.path.dirname(os.path.abspath(output_filepath)), exist_ok=True)
                    with open(output_filepath, "wb") as f:
                        f.write(response.content)
                    return output_filepath
                else:
                    return None
            except Exception:
                return None
