import re
from typing import Optional, Dict, Any, Tuple
from backend.contracts import ExtractedEntities, VerificationOutcome

# Canonical formatting helpers for Timbre v3.0 Text Normalization (TN)
def format_plate_for_timbre(plate: str) -> str:
    """
    Timbre TN known limitation: unspaced plates ('KA01MJ4821') fail to spell out correctly.
    Must format as spaced components: 'KA 01 MJ 4821'.
    """
    clean = re.sub(r'[^A-Za-z0-9]', '', plate).upper()
    # Standard format: State(2) + RTO(1-2 digits) + Series(1-2 chars) + Number(4 digits)
    m1 = re.match(r'^([A-Z]{2})(\d{1,2})([A-Z]{1,2})(\d{4})$', clean)
    if m1:
        return f"{m1.group(1)} {m1.group(2).zfill(2)} {m1.group(3)} {m1.group(4)}"
    # Delhi format: DL 3C AB 1234
    m2 = re.match(r'^([A-Z]{2})(\d{1,2}[A-Z])([A-Z]{1,2})(\d{4})$', clean)
    if m2:
        rto = m2.group(2)
        if len(rto) == 2 and rto[0].isdigit():
            rto = f"0{rto}"
        return f"{m2.group(1)} {rto} {m2.group(3)} {m2.group(4)}"
    return plate

def format_date_for_timbre(date_str: str) -> str:
    """
    Timbre TN known limitation: two-part dates like '15/11' are misread as fractions
    ('fifteen elevenths') unless written as 3-part dates like '15/11/2026'.
    """
    clean = date_str.strip()
    match = re.match(r'^(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?$', clean)
    if match:
        day = match.group(1).zfill(2)
        month = match.group(2).zfill(2)
        year = match.group(3) if match.group(3) else "2026"
        if len(year) == 2:
            year = "20" + year
        return f"{day}/{month}/{year}"
    return date_str

def extract_entities(transcript: str) -> ExtractedEntities:
    """
    Deterministic regex parser that extracts:
    1. Vehicle Plate (e.g. KA 01 MJ 4821)
    2. Date (e.g. 15th Nov, 15/11/2026)
    3. Amount (₹14,500 / 14500 / 4500)
    CRITICAL: Filters out the 4-digit plate suffix (e.g. 4821) before searching for currency amounts.
    """
    entities: ExtractedEntities = {
        "plate": None,
        "amount": None,
        "date": None,
        "raw_amount_str": None
    }
    
    if not transcript:
        return entities

    text = transcript.strip()
    
    # 1. Extract Vehicle Plate
    # Pattern e.g. KA 01 MJ 4821, DL 3C AB 1234, KA01MJ4821
    plate_pattern = r'\b([A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,2}\s*\d{4})\b'
    plate_match = re.search(plate_pattern, text, re.IGNORECASE)
    plate_suffix = None
    if plate_match:
        raw_plate = plate_match.group(1)
        normalized_plate = re.sub(r'\s+', '', raw_plate).upper()
        entities["plate"] = normalized_plate
        # Suffix is the last 4 digits (e.g. 4821)
        plate_suffix = normalized_plate[-4:]

    # Mask plate in text to avoid capturing the plate suffix as currency
    masked_text = text
    if plate_match:
        masked_text = text[:plate_match.start()] + " [PLATE] " + text[plate_match.end():]

    # 2. Extract Date
    # Pattern e.g. 15th Nov, 15th November, 15/11, 15-11-2026, 15 Nov
    date_pattern = r'\b(\d{1,2}(?:st|nd|rd|th)?\s*(?:[-/]\s*\d{1,2}(?:[-/]\s*\d{2,4})?|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?:\s*\d{2,4})?))\b'
    date_match = re.search(date_pattern, masked_text, re.IGNORECASE)
    if date_match:
        entities["date"] = date_match.group(1).strip()
        masked_text = masked_text[:date_match.start()] + " [DATE] " + masked_text[date_match.end():]

    # 3. Extract Amount
    # First look for currency-prefixed amounts: ₹14,500, Rs. 14500, Rs 4500
    curr_pattern = r'(?:₹|Rs\.?|INR)\s*(\d{1,3}(?:,\d{3})+|\d+)'
    curr_match = re.search(curr_pattern, masked_text, re.IGNORECASE)
    
    amount_val = None
    raw_str = None
    if curr_match:
        raw_str = curr_match.group(1)
        amount_val = int(raw_str.replace(',', ''))
    else:
        # Fallback to standalone numbers with 'rupees' or isolated 3-6 digit numbers
        rupee_pattern = r'\b(\d{1,3}(?:,\d{3})+|\d{3,6})\s*(?:rupees?|rs|/-)?\b'
        rupee_match = re.search(rupee_pattern, masked_text, re.IGNORECASE)
        if rupee_match:
            cand = rupee_match.group(1).replace(',', '')
            # Ensure it is not the plate suffix
            if cand != plate_suffix:
                raw_str = rupee_match.group(1)
                amount_val = int(cand)

    entities["amount"] = amount_val
    entities["raw_amount_str"] = raw_str

    return entities

def build_readback_prompt(
    detected_lang: str,
    heard_amount: Optional[int],
    expected_amount: int,
    plate: str,
    date_str: str = "15/11/2026"
) -> str:
    """
    Builds the Timbre v3.0 omnilingual readback prompt in the caller's language
    using canonical formatting (spaced plates, 3-part dates, <pauses> tags).
    """
    spaced_plate = format_plate_for_timbre(plate)
    formatted_date = format_date_for_timbre(date_str)
    heard_str = f"₹{heard_amount:,}" if heard_amount is not None else "an amount"
    expected_str = f"₹{expected_amount:,}"

    lang_lower = (detected_lang or "kn-IN").lower()

    if "kn" in lang_lower:
        # Kannada-English / Kanglish
        return (
            f"ಕ್ಷಮಿಸಿ, ಟ್ರಾಫಿಕ್ ಗದ್ದಲ ಇತ್ತು. <pauses> "
            f"ವಾಹನ {spaced_plate} ಗಾಗಿ ನಾನು {heard_str} ಕೇಳಿದೆ. "
            f"ನೀವು {formatted_date} ದಿನಾಂಕಕ್ಕೆ {heard_str} ಅಥವಾ ಪೂರ್ಣ {expected_str} ದೃಢೀಕರಿಸುತ್ತಿದ್ದೀರಾ?"
        )
    elif "hi" in lang_lower:
        # Hindi-English / Hinglish
        return (
            f"माफ़ कीजिए, ट्रैफ़िक का शोर था. <pauses> "
            f"गाड़ी {spaced_plate} के लिए मुझे {heard_str} सुनाई दिया. "
            f"क्या आप {formatted_date} के लिए {heard_str} या पूरे {expected_str} की पुष्टि कर रहे हैं?"
        )
    elif "ta" in lang_lower:
        # Tamil-English / Tanglish
        return (
            f"மன்னிக்கவும், அதிக இரைச்சல் இருந்தது. <pauses> "
            f"வாகனம் {spaced_plate} க்கு {heard_str} கேட்டேன். "
            f"நீங்கள் {formatted_date} தேதிக்கு {heard_str} அல்லது முழு {expected_str} உறுதிப்படுத்துகிறீர்களா?"
        )
    else:
        # English default
        return (
            f"Excuse me, there was traffic noise. <pauses> "
            f"For vehicle {spaced_plate}, I heard {heard_str}. "
            f"Are you confirming {heard_str} or the full {expected_str} due on {formatted_date}?"
        )

def verify_two_pass(
    clip_id: str,
    snr_label: str,
    ground_truth: Dict[str, Any],
    default_extracted: ExtractedEntities,
    pass1_extracted: ExtractedEntities,
    pass2_extracted: ExtractedEntities,
    detected_lang: str = "kn-IN"
) -> VerificationOutcome:
    """
    Evaluates Stage 3 Two-Pass Agreement:
    - Pass 1: Unbiased Acoustic
    - Pass 2: Context-Grounded Primed
    Compares against expected CRM ledger (ground_truth).
    """
    expected_amount = ground_truth.get("amount", 14500)
    expected_plate = ground_truth.get("plate", "KA01MJ4821")
    expected_date = ground_truth.get("date", "15-11")

    p1_amt = pass1_extracted.get("amount")
    p2_amt = pass2_extracted.get("amount")
    p1_plt = pass1_extracted.get("plate")
    p2_plt = pass2_extracted.get("plate")

    status = "FAILED_RED"
    readback_prompt = None
    notes = ""

    # Rule 1: High confidence agreement
    if p1_amt == p2_amt and p1_amt == expected_amount:
        status = "VERIFIED_GREEN"
        notes = "Pass 1 and Pass 2 in 100% agreement with CRM ledger."
    # Rule 2: Pass 1 and Pass 2 disagree, or Pass 2 recovered context but Pass 1 missed digit,
    # or amount is a negotiated partial payment (e.g. 4500)
    elif (p1_amt is not None or p2_amt is not None):
        status = "UNCERTAIN_AMBER"
        heard_amt = p2_amt if p2_amt is not None else p1_amt
        notes = f"Discrepancy detected: Pass1={p1_amt}, Pass2={p2_amt}, Expected={expected_amount}."
        readback_prompt = build_readback_prompt(
            detected_lang=detected_lang,
            heard_amount=heard_amt,
            expected_amount=expected_amount,
            plate=expected_plate,
            date_str=expected_date
        )
    else:
        status = "FAILED_RED"
        notes = "Both passes failed to extract amount entity."

    outcome: VerificationOutcome = {
        "clip_id": clip_id,
        "snr_label": snr_label,
        "ground_truth": ground_truth,
        "default_extracted": default_extracted,
        "pass1_extracted": pass1_extracted,
        "pass2_extracted": pass2_extracted,
        "status": status,
        "readback_prompt": readback_prompt,
        "readback_audio_path": None,
        "readback_confirmed_amount": expected_amount if status == "VERIFIED_GREEN" else None,
        "notes": notes
    }
    return outcome
