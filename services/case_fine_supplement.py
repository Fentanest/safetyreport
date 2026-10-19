"""Supplement an unknown disposition after the existing parser finishes.

Only observed case-decision patterns are recognized. Existing dispositions,
explicit money, lifecycle states and the category-based estimate remain intact.
Keep the pure Dart port and contracts/case-fine-supplement-vectors.json aligned.
"""
import re

_FINAL = frozenset({"수용", "일부수용", "불수용", "기타", "답변완료"})
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}
_ENTITY = re.compile(r"&(#x[0-9a-fA-F]+|#X[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos|nbsp);")
# Explicit whitespace set shared with Dart (union of Python and Dart \s).
_SPACE_SET = r"\t\n\x0b\x0c\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_SPACE = re.compile("[" + _SPACE_SET + "]+")
_S = "[" + _SPACE_SET + "]"
_BOUNDARY = re.compile(r"(?<=[다요])[.。!?]|\n" + _S + "*\n|(?<=\n)(?=" + _S + "*(?:[0-9]+|[가-하])[.)]" + _S + ")|(?<=\n)(?=" + _S + "*[○※])")
# 2026-10-19: 과태료 (부과) 사전통지서 발송(예정·완료)도 조치로 본다(서울시 전용차로 답변 "확인되어 … 과태료 사전통지서를 발송할 예정").
_ACTION = re.compile(r"과태료(?:를)?부과(?:할예정|처리|로결정|되었|하였|절차를진행|및징수절차진행|하도록하겠|하겠습니다|합니다)"
                     r"|과태료(?:부과)?사전통지서?(?:를|을)?(?:발송|통지|발부)(?:할예정|예정|하였|했|되었|하겠|합니다|하도록하겠)")
_PROOF = re.compile(r"불법주[·ㆍ]?정차로판단되어|불법행위\(충전방해행위\)에해당하여|(?:확인|검토)(?:한)?결과.{0,350}(?:확인되어|확인됨에따라|파악되어|위반차량으로)|위반행위가확인되어|위반(?:한)?사실이확인되어|위반한행위로확인되어|위반에해당되어|이중주차행위\(주차위반\)에해당되어")
# Conditional or future wording ("다시 적발될 시", "적발되면", "~할 시", "~하면").
_CONDITION = re.compile(r"미이행|불이행|불응|않을경우|않는경우|않을시|재위반|재차|경우|최대|이하|상한"
                        r"|다시적발|재적발|적발시|적발될시|적발되면|위반시|위반하면|될시|할시|되면|하면|않으면")
# The answer already reports a warning/guidance action for this case ("발부하지 않았습니다" is not one).
_WARNED = re.compile(r"경고(?:조치|처분|처리)?(?:를|을)?(?:하였|했|되었|됨|함)|경고장(?:을|를)?(?:발부|부착|발송|교부)(?!하지|되지)"
                     r"|계도(?:조치|처분|처리)?(?:를|을)?(?:하였|했|되었|됨|함)|계도장(?:을|를)?(?:발부|부착|발송|교부)(?!하지|되지)"
                     r"|교통질서안내장(?:을|를)?발송(?!하지|되지)")
_NEGATIVE = re.compile(r"과태료.{0,30}(?:부과(?:가|하기|에)?(?:어려|곤란|불가)|부과할수없|부과하지|처분이불가|처분은하지)|과태료미부과")
_CASE_NEGATIVE = re.compile(r"(?:자료|영상|사진).{0,25}확인|처리결과|식별되지않|확증할수없|통고처분이나과태료처분")
_STRUCTURED = re.compile(r"처리결과[:：]수용\(과태료부과\)")
_NOTICE = re.compile(r"위반행위에따른과태료[0-9,.]*원을부과하고자")
_SENT = re.compile(r"과태료부과사전통지서[’”'\"]*를발송하였")


def _normalize(text: str) -> str:
    def decode(match):
        value = match[1]
        if not value.startswith("#"):
            return _ENTITIES[value]
        try:
            number = int(value[2:], 16) if value[1:2].lower() == "x" else int(value[1:])
        except ValueError:
            return match[0]
        return chr(number) if 0 < number <= 0x10FFFF and not 0xD800 <= number <= 0xDFFF else match[0]
    text = _ENTITY.sub(decode, text)
    # Explicit shared normalization profile, not a platform-specific Unicode library.
    text = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in text)
    return _SPACE.sub("", text)


def supplement_case_fine(status: str, penalty: str, answer: str) -> tuple[str, str]:
    if status not in _FINAL or penalty not in ("", "미확인"):
        return status, penalty
    if _WARNED.search(_normalize(answer or "")):
        return status, penalty
    positive = negative = structured = False
    for sentence in _BOUNDARY.split(answer or ""):
        text = _normalize(sentence)
        if "과태료" not in text or _CONDITION.search(text):
            continue
        if _NEGATIVE.search(text):
            negative |= bool(_CASE_NEGATIVE.search(text))
            continue
        if _STRUCTURED.search(text):
            positive = structured = True
            continue
        proof, action = _PROOF.search(text), _ACTION.search(text)
        if proof and action and proof.start() < action.start():
            positive = True
            continue
        bare = re.sub(r"^[가-하]\.", "", text).strip(" .-")
        if bare == "이에해당차량에대하여과태료를부과할예정입니다":
            positive = True
        elif _NOTICE.search(text) and _SENT.search(text):
            positive = True
    if positive and not negative:
        return ("수용" if structured else status), "과태료"
    return status, penalty
