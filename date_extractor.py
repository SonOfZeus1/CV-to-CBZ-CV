import re
import logging
import dateparser
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

@dataclass
class DateAnchor:
    raw: str
    start: str # YYYY-MM or YYYY
    end: Optional[str] # YYYY-MM or YYYY or None
    is_current: bool
    precision: str # "year", "month"
    start_idx: int
    end_idx: int
    start_is_year_only: bool = False
    end_is_year_only: bool = False

def compute_duration_months(start_str: str, end_str: Optional[str], is_current: bool) -> int:
    """Calculates duration in months."""
    if not start_str or len(start_str) == 4: # Year only -> No duration calculation
        return 0
        
    try:
        start_date = datetime.strptime(start_str, "%Y-%m")
    except ValueError:
        return 0 

    end_date = datetime.now()
    if not is_current and end_str:
        if len(end_str) == 4: # End is year only -> No duration
            return 0
        try:
            end_date = datetime.strptime(end_str, "%Y-%m")
        except ValueError:
            return 0
            
    diff = relativedelta(end_date, start_date)
    total_months = diff.years * 12 + diff.months
    
    if total_months == 0:
        total_months = 1
        
    return total_months

def normalize_date(date_obj: datetime, is_year_only: bool = False) -> str:
    """Returns YYYY-MM or YYYY string."""
    if not date_obj:
        return ""
    if is_year_only:
        return date_obj.strftime("%Y")
    return date_obj.strftime("%Y-%m")

def extract_date_anchors(text: str) -> List[DateAnchor]:
    """
    Finds date ranges and single dates in text.
    Returns sorted list of DateAnchors.
    """
    anchors = []
    
    # Regex Patterns
    # 1. Ranges: "YYYY - YYYY", "MM/YYYY - MM/YYYY", "Month YYYY - Month YYYY"
    # We look for the separator " - " (normalized)
    
    # Pattern for a single date component (Month Year or Year)
    # Year: 1990-2029
    year_pat = r'(?:19|20)\d{2}'
    # Month: 01-12, Jan-Dec (FR/EN)
    month_pat = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|janv|fév|mars|avr|mai|juin|juil|août|sept|oct|nov|déc)[a-z]*\.?'
    month_digit_pat = r'(?:0?[1-9]|1[0-2])'
    
    # Allow optional opening parenthesis before the date part
    date_part_pat = fr'(?:[\(\s]*)(?:(?:{month_pat}|{month_digit_pat})[\s/]+)?{year_pat}(?:[\)\s]*)'
    
    # Full Range Pattern
    range_pat = fr'({date_part_pat})\s*-\s*({date_part_pat}|present|aujourd\'hui|now|actuel|current)'
    
    # Find Ranges first
    for match in re.finditer(range_pat, text, flags=re.IGNORECASE):
        raw = match.group(0)
        start_raw = match.group(1)
        end_raw = match.group(2)
        
        start_dt = dateparser.parse(start_raw, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'first'})
        start_is_year_only = len(start_raw) <= 4
        
        is_current = False
        end_dt = None
        end_is_year_only = False
        
        if re.match(r'(?i)^(present|aujourd\'hui|now|actuel|current)$', end_raw):
            is_current = True
        else:
            end_dt = dateparser.parse(end_raw, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'last'})
            end_is_year_only = len(end_raw) <= 4
            
        if start_dt:
            anchor = DateAnchor(
                raw=raw,
                start=normalize_date(start_dt, start_is_year_only),
                end=normalize_date(end_dt, end_is_year_only) if end_dt else None,
                is_current=is_current,
                precision="month" if not start_is_year_only else "year",
                start_idx=match.start(),
                end_idx=match.end(),
                start_is_year_only=start_is_year_only,
                end_is_year_only=end_is_year_only
            )
            anchors.append(anchor)

    # Find "Since" / "Depuis" patterns
    since_pat = fr'(?:depuis|since)\s+({date_part_pat})'
    for match in re.finditer(since_pat, text, flags=re.IGNORECASE):
        # Check if overlap with existing anchors
        if any(a.start_idx <= match.start() < a.end_idx for a in anchors):
            continue
            
        raw = match.group(0)
        start_raw = match.group(1)
        start_dt = dateparser.parse(start_raw, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'first'})
        start_is_year_only = len(start_raw) <= 4
        
        if start_dt:
            anchor = DateAnchor(
                raw=raw,
                start=normalize_date(start_dt, start_is_year_only),
                end=None,
                is_current=True,
                precision="month" if not start_is_year_only else "year",
                start_idx=match.start(),
                end_idx=match.end(),
                start_is_year_only=start_is_year_only,
                end_is_year_only=False
            )
            anchors.append(anchor)

    # Find Single Years (isolated) -> Potential single year experience or start date
    # We look for lines that contain a year but are NOT part of an existing range.
    # Regex: Line start or newline, optional text, Year, optional text, newline or end
    
    # Find Single Years (isolated) -> Potential single year experience or start date
    # We look for lines that contain a year but are NOT part of an existing range.
    # Regex: Line start or newline, optional text, Year, optional text, newline or end
    
    # Simpler: Find all years, check if they fall into existing anchor ranges
    for match in re.finditer(year_pat, text):
        start_pos = match.start()
        end_pos = match.end()
        
        # Check overlap
        if any(a.start_idx <= start_pos < a.end_idx for a in anchors):
            continue
            
        # Context check: Is it likely a date? 
        # Avoid phone numbers, postal codes (though 4 digits is rare for postal in CA/US, common elsewhere)
        # Check if surrounded by digits
        if re.search(r'\d', text[start_pos-1:start_pos]) or re.search(r'\d', text[end_pos:end_pos+1]):
            continue
            
        # STRICTER CHECK:
        # A valid single year anchor should be:
        # 1. At the start of a line (ignoring whitespace/bullets)
        # 2. OR followed by a newline shortly after
        # 3. NOT preceded by words like "in", "en", "depuis", "since" (unless it's a start date)
        # 4. NOT part of a sentence (e.g. "completed in 2022")
        
        line_start = text.rfind('\n', 0, start_pos) + 1
        line_end = text.find('\n', end_pos)
        if line_end == -1: line_end = len(text)
        
        line_content = text[line_start:line_end].strip()
        
        # If the year is in the middle of a long sentence, skip it.
        # Heuristic: if line length > 50 chars and year is not at start/end, skip.
        if len(line_content) > 60:
             # Check if year is at start
             if not re.match(r'^[\W_]*' + re.escape(match.group(0)), line_content):
                 continue

        # Check for preceding text that indicates it's NOT an anchor
        preceding_text = text[line_start:start_pos].lower()
        if any(w in preceding_text for w in ["date du", "en date du", "dated", "completed", "version"]):
            continue

        raw = match.group(0)
        start_dt = dateparser.parse(raw)
        
        if start_dt:
            anchor = DateAnchor(
                raw=raw,
                start=normalize_date(start_dt, is_year_only=True),
                end=normalize_date(start_dt, is_year_only=True), # Single year = start and end same year roughly
                is_current=False,
                precision="year",
                start_idx=start_pos,
                end_idx=end_pos,
                start_is_year_only=True,
                end_is_year_only=True
            )
            anchors.append(anchor)

    # Sort anchors by position
    anchors.sort(key=lambda x: x.start_idx)
    
    return anchors
