import sys
from unittest.mock import MagicMock

# Mock weasyprint to avoid installation issues
sys.modules["weasyprint"] = MagicMock()

from formatters import format_experience_entry

entry = {
    "job_title": "Développeur Java",
    "company": "SAP",
    "dates": "2021-Present",
    "skills": ["Java", "Spring Boot", "Angular"]
}

html = format_experience_entry(entry)
print(html)

if "Environnement Technologique" in html and "display: inline-block" in html:
    print("\n[SUCCESS] Formatting verified.")
else:
    print("\n[FAILURE] Formatting incorrect.")
