import re

def preprocess_markdown(text: str) -> str:
    """
    Normalizes Markdown text to fix common OCR/PDF artifacts.
    """
    if not text:
        return ""

    # 1. Fix spaced years (e.g., "2 0 1 8" -> "2018")
    # Look for 4 digits separated by spaces
    text = re.sub(r'\b(\d)\s+(\d)\s+(\d)\s+(\d)\b', r'\1\2\3\4', text)
    
    # 2. Fix spaced keywords (e.g., "D E P U I S" -> "DEPUIS")
    keywords = ["DEPUIS", "PRESENT", "ACTUEL", "CURRENT", "TODAY", "AUJOURD'HUI", "MAINTENANT"]
    for kw in keywords:
        # Create regex for spaced keyword (e.g., D\s+E\s+P\s+U\s+I\s+S)
        spaced_kw = r'\s+'.join(list(kw))
        text = re.sub(fr'\b{spaced_kw}\b', kw, text, flags=re.IGNORECASE)

    # 3. Normalize dashes
    text = re.sub(r'[–—−]', '-', text)
    text = re.sub(r'\s+to\s+', ' - ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+au\s+', ' - ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+à\s+', ' - ', text, flags=re.IGNORECASE)

    # 4. Reduce multiple spaces (but keep newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    
    # 5. Fix "Month Year" spaced (e.g. "J u i l l e t 2 0 1 8" -> "Juillet 2018")
    # This is harder without a dictionary, but we can try for months
    months = ["JANVIER", "FEVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOUT", "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DECEMBRE",
              "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
    
    for m in months:
        if len(m) > 3: # Skip short ones to avoid false positives
             spaced_m = r'\s+'.join(list(m))
             text = re.sub(fr'\b{spaced_m}\b', m, text, flags=re.IGNORECASE)

    return text.strip()
