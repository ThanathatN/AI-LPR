"""Thai licence-plate grammar: normalisation, validation and province matching."""
import difflib
import re

THAI_CONSONANTS = "กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"
DIGITS = "0123456789"
REG_ALLOWLIST = THAI_CONSONANTS + DIGITS
THAI_ALL = "".join(chr(c) for c in range(0x0E01, 0x0E4F))

PROVINCES = [
    "กรุงเทพมหานคร", "กระบี่", "กาญจนบุรี", "กาฬสินธุ์", "กำแพงเพชร", "ขอนแก่น", "จันทบุรี", "ฉะเชิงเทรา",
    "ชลบุรี", "ชัยนาท", "ชัยภูมิ", "ชุมพร", "เชียงราย", "เชียงใหม่", "ตรัง", "ตราด", "ตาก", "นครนายก",
    "นครปฐม", "นครพนม", "นครราชสีมา", "นครศรีธรรมราช", "นครสวรรค์", "นนทบุรี", "นราธิวาส", "น่าน",
    "บึงกาฬ", "บุรีรัมย์", "ปทุมธานี", "ประจวบคีรีขันธ์", "ปราจีนบุรี", "ปัตตานี", "พระนครศรีอยุธยา",
    "พะเยา", "พังงา", "พัทลุง", "พิจิตร", "พิษณุโลก", "เพชรบุรี", "เพชรบูรณ์", "แพร่", "ภูเก็ต",
    "มหาสารคาม", "มุกดาหาร", "แม่ฮ่องสอน", "ยโสธร", "ยะลา", "ร้อยเอ็ด", "ระนอง", "ระยอง", "ราชบุรี",
    "ลพบุรี", "ลำปาง", "ลำพูน", "เลย", "ศรีสะเกษ", "สกลนคร", "สงขลา", "สตูล", "สมุทรปราการ",
    "สมุทรสงคราม", "สมุทรสาคร", "สระแก้ว", "สระบุรี", "สิงห์บุรี", "สุโขทัย", "สุพรรณบุรี", "สุราษฎร์ธานี",
    "สุรินทร์", "หนองคาย", "หนองบัวลำภู", "อ่างทอง", "อำนาจเจริญ", "อุดรธานี", "อุตรดิตถ์", "อุทัยธานี",
    "อุบลราชธานี", "เบตง",
]

# OCR look-alikes that should be digits on a Thai registration line
_TO_DIGIT = {"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "i": "1",
             "Z": "2", "z": "2", "S": "5", "s": "5", "B": "8", "G": "6", "T": "7", "g": "9", "q": "9",
             "๐": "0", "๑": "1", "๒": "2", "๓": "3", "๔": "4", "๕": "5", "๖": "6", "๗": "7", "๘": "8", "๙": "9"}

_PAT_PRIVATE = re.compile(r"^(\d?)([ก-ฮ]{1,2})(\d{1,4})$")
_PAT_NUMERIC = re.compile(r"^(\d{2})(\d{4})$")


def plate_key(text: str) -> str:
    """Canonical key used for list matching (no spaces / dashes)."""
    return re.sub(r"[\s\-\.]", "", text or "").upper()


def clean_registration(raw: str) -> str:
    out = []
    for ch in raw or "":
        ch = _TO_DIGIT.get(ch, ch)
        if ch in REG_ALLOWLIST:
            out.append(ch)
    return "".join(out)


def parse_registration(raw: str):
    """Return (display_text, key, format_score 0..1).

    Plate frames and screws are often read as an extra character at either end,
    so when the full string does not fit the grammar we try trimming 1-2 edge chars.
    """
    s = clean_registration(raw)
    best = _parse_exact(s)
    if best[2] >= 0.85:
        return best
    for cut_l in range(0, 3):
        for cut_r in range(0, 3):
            if cut_l + cut_r == 0 or len(s) - cut_l - cut_r < 3:
                continue
            cand = _parse_exact(s[cut_l:len(s) - cut_r])
            penalty = 0.08 * (cut_l + cut_r)
            if cand[2] - penalty > best[2]:
                best = (cand[0], cand[1], round(cand[2] - penalty, 2))
    return best


def _parse_exact(s: str):
    m = _PAT_PRIVATE.match(s)
    if m:
        lead, letters, num = m.groups()
        score = 1.0 if len(letters) == 2 else 0.85
        if len(num) == 4 or (len(num) >= 1 and not num.startswith("0")):
            pass
        else:
            score -= 0.2
        return f"{lead}{letters} {num}", f"{lead}{letters}{num}", score
    m = _PAT_NUMERIC.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}", s, 0.9
    # partial reads still have value for voting but score low
    return s, s, 0.3 if len(s) >= 3 else 0.0


_PAT_D1 = re.compile(r"^\d[ก-ฮ]\d{1,4}$")


def plate_prior(key: str):
    """Log prior of a registration under Thai plate grammar; None = impossible plate.

    Most private plates are [digit]? + 2 consonants + 1-4 digits. Older single-consonant and
    commercial numeric plates exist but are rarer; digit + single consonant is very rare and is
    usually a misread of a consonant as a digit (ธ→5, ห→1).
    """
    m = _PAT_PRIVATE.match(key)
    if m:
        lead, letters, num = m.groups()
        p = 0.0 if len(letters) == 2 else -1.0
        if lead and len(letters) == 1:
            p = -2.5
        if num.startswith("0"):
            p -= 2.0
        return p
    if _PAT_NUMERIC.match(key):
        return -1.5
    return None


def display(key: str) -> str:
    return parse_registration(key)[0] if key else ""


def match_province(raw: str):
    text = "".join(ch for ch in (raw or "") if "฀" <= ch <= "๿")
    if len(text) < 2:
        return "", 0.0
    best = difflib.get_close_matches(text, PROVINCES, n=1, cutoff=0.45)
    if not best:
        return "", 0.0
    return best[0], difflib.SequenceMatcher(None, text, best[0]).ratio()


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
