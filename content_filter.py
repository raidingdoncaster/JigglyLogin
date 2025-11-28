"""Shared content filter used by both trainer bios and bulletin comments."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional


LEET_TABLE = str.maketrans({
    "@": "a",
    "4": "a",
    "8": "b",
    "3": "e",
    "1": "i",
    "!": "i",
    "|": "i",
    "0": "o",
    "$": "s",
    "5": "s",
    "7": "t",
    "+": "t",
})


@dataclass
class FilterDecision:
    """Represents a blocked match detected by the filter."""

    category: str
    severity: str
    label: str
    match: str
    rule_id: str

    def to_dict(self) -> dict:
        return asdict(self)


class ContentFilter:
    """Lightweight text filter catching profanity, self-harm, hate speech, and phone numbers."""

    def __init__(self) -> None:
        self._rules = self._build_rules()
        self._phone_pattern = re.compile(r"(?:\+?\d[\s().-]*){7,}\d")

    def _build_rules(self) -> list[dict]:
        rules: list[dict] = [
            {
                "id": "self-harm",
                "label": "Self-harm references",
                "category": "self_harm",
                "severity": "critical",
                "patterns": [
                    r"\bkill\s+(?:myself|ourselves|yourself|himself|herself)\b",
                    r"\b(?:commit|consider|planning)\s+suicide\b",
                    r"\bself[-\s]?harm\b",
                    r"\b(?:end|take)\s+(?:my|your|their)\s+life\b",
                    r"\bkms\b",
                    r"\bunalive\b",
                    r"\bsuicidal\b",
                ],
            },
            {
                "id": "violent-threats",
                "label": "Violent threats",
                "category": "violence",
                "severity": "critical",
                "patterns": [
                    r"\bi['’]m\s+going\s+to\s+(?:kill|murder|hurt|stab|beat)\s+you\b",
                    r"\b(?:kill|murder|shoot|stab|beat)\s+(?:you|him|her|them)\b",
                    r"\bbeat\s+you\s+to\s+death\b",
                    r"\b(?:set|light)\s+you\s+on\s+fire\b",
                ],
            },
            {
                "id": "hate-speech",
                "label": "Hate speech",
                "category": "hate_speech",
                "severity": "critical",
                "patterns": [
                    r"\b(?:kill|hurt|eliminate|erase|attack)\b[^.]{0,40}\b(?:jews?|muslims?|christians?|asians?|blacks?|latinos?|immigrants?|gays?|lesbians?|trans|transgender|women|men)\b",
                    r"\b(?:i|we)\s+hate\s+(?:jews?|muslims?|asians?|blacks?|latinos?|gays?|lesbians?|trans|immigrants?)\b",
                    r"\b(?:go back to where you came from)\b",
                ],
            },
            {
                "id": "hate-slurs-ethnic",
                "label": "Hate speech (ethnic slur)",
                "category": "hate_speech",
                "severity": "critical",
                "patterns": [
                    r"\bnigg(?:a|er)s?\b",
                    r"\bspic(?:s|es)?\b",
                    r"\bkike?s?\b",
                    r"\bchink?s?\b",
                    r"\bgook?s?\b",
                    r"\bwetback?s?\b",
                    r"\bsandnigg(?:a|er)s?\b",
                    r"\braghead?s?\b",
                    r"\bcoon?s?\b",
                    r"\btowelhead?s?\b",
                    r"\bporch\s*monkey\b",
                    r"\bzipper\s*head\b",
                ],
            },
            {
                "id": "hate-slurs-lgbt",
                "label": "Hate speech (LGBTQ+ slur)",
                "category": "hate_speech",
                "severity": "critical",
                "patterns": [
                    r"\bfag+(?:ot)?s?\b",
                    r"\bdyke?s?\b",
                    r"\btrann(?:y|ies)\b",
                    r"\bshemale?s?\b",
                    r"\bno\s+homo\b",
                ],
            },
            {
                "id": "hate-slurs-ableist",
                "label": "Hate speech (ableist slur)",
                "category": "hate_speech",
                "severity": "critical",
                "patterns": [
                    r"\bretard(?:ed|s)?\b",
                    r"\bree?tard\b",
                    r"\bmongoloid\b",
                    r"\bspaz\b",
                    r"\bcripple\b",
                ],
            },
            {
                "id": "explicit-profanity",
                "label": "Severe profanity",
                "category": "profanity",
                "severity": "high",
                "patterns": [
                    r"\bf+u+c+k+\b",
                    r"\bmotherf+u+c+ker\b",
                    r"\bshit+\b",
                    r"\bbitch(?:es)?\b",
                    r"\bba?stard\b",
                    r"\basshole\b",
                    r"\bdumbass\b",
                    r"\bjackass\b",
                    r"\bdickhead\b",
                    r"\bdick\b",
                    r"\bcock\b",
                    r"\bcunt\b",
                    r"\bprick\b",
                    r"\bpuss(?:y|ies)\b",
                    r"\bwhore\b",
                    r"\bslut\b",
                    r"\bskank\b",
                    r"\bpiss(?:ed|ing)?\b",
                    r"\bgoddamn\b",
                    r"\bhell\s+no\b",
                ],
            },
            {
                "id": "sexual-content",
                "label": "Sexual or explicit content",
                "category": "inappropriate",
                "severity": "high",
                "patterns": [
                    r"\b(?:send|share)\s+nudes\b",
                    r"\bonlyfans\b",
                    r"\b(?:horny|sexting)\b",
                    r"\bblowjob\b",
                    r"\bhandjob\b",
                    r"\bfellatio\b",
                    r"\bcum(?:shot|ming)?\b",
                    r"\bjizz\b",
                    r"\btit(?:ty)?fuck\b",
                    r"\bbutt\s+plug\b",
                ],
            },
            {
                "id": "mild-profanity",
                "label": "Inappropriate language",
                "category": "profanity",
                "severity": "medium",
                "patterns": [
                    r"\bcrap\b",
                    r"\bdamn\b",
                    r"\bhell\b",
                ],
            },
        ]
        for rule in rules:
            rule["compiled"] = [re.compile(pattern, re.IGNORECASE) for pattern in rule["patterns"]]
        return rules

    @staticmethod
    def _normalize(value: str) -> str:
        lowered = value.lower().translate(LEET_TABLE)
        return re.sub(r"\s+", " ", lowered)

    def _detect_phone_number(self, value: str) -> Optional[str]:
        match = self._phone_pattern.search(value)
        if not match:
            return None
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 7:
            return None
        return match.group(0).strip()

    def scan(self, value: str) -> Optional[FilterDecision]:
        """Return a FilterDecision if the text violates policy."""
        if not value:
            return None

        normalized = self._normalize(value)
        for rule in self._rules:
            for pattern in rule["compiled"]:
                found = pattern.search(normalized)
                if found:
                    return FilterDecision(
                        category=rule["category"],
                        severity=rule["severity"],
                        label=rule["label"],
                        match=found.group(0).strip(),
                        rule_id=rule["id"],
                    )

        phone_match = self._detect_phone_number(value)
        if phone_match:
            return FilterDecision(
                category="contact_sharing",
                severity="critical",
                label="Phone numbers are not allowed here.",
                match=phone_match,
                rule_id="phone-number",
            )

        return None
