"""Offline test of SkillMatcher fuzzy matching against garbled OCR samples."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.knowledge.skill_matcher import SkillMatcher

matcher = SkillMatcher()
print(f"Loaded {len(matcher.names)} skill names\n")

# Test cases from previous OCR sessions
test_cases = [
    # Good OCR
    "Behold Thine Emperor's Divine Might",
    "Remove Non-Standard Distance X",
    "Professor of Curvature",
    # Garbled OCR
    "Ei riablly H Lalmg",       # Flashy☆Landing?
    "Aar Daat Dabin",            # Outer Post Proficiency?
    "CI:-Lлl.",                  # Corner Recovery?
    "Cornr Rcvry",               # Corner Recovery
    "Accleraton Boost",          # Acceleration Boost
    "Stamna Keeper",             # Stamina Keeper
    "Fnt Runnr's Instinct",     # Front Runner's Instinct
    "Positon Sense",             # Position Sense
    "Concentraton",              # Concentration
    "Escapé Artist",             # Escape Artist
    "Iron Wil",                  # Iron Will
    "Calm and Collectd",         # Calm and Collected
]

for ocr_text in test_cases:
    result = matcher.match(ocr_text)
    if result:
        name, score = result
        print(f"  '{ocr_text}' → '{name}' ({score}%)")
    else:
        print(f"  '{ocr_text}' → NO MATCH")
