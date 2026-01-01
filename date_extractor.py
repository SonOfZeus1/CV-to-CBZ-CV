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
    start: str # YYYY-MM or YYYY-01
    end: Optional[str] # YYYY-MM or None
    is_current: bool
    precision: str # "month" or "year"
    start_idx: int
    end_idx: int

def compute_duration_months(start_str: str, end_str: Optional[str], is_current: bool) -> int:
    """Calculates duration in months."""
    if not start_str:
        return 0
        
    try:
        start_date = datetime.strptime(start_str, "%Y-%m")
    except ValueError:
        return 0 # Should not happen if normalized correctly

    end_date = datetime.now()
    if not is_current and end_str:
        try:
            end_date = datetime.strptime(end_str, "%Y-%m")
        except ValueError:
            return 0
            
    # Add 1 month for inclusive calculation (e.g. Jan to Jan = 1 month)
    # But relativedelta does difference.
    # If end_date is same month as start_date, diff is 0. We want 1.
    # So we add 1 month to end_date roughly? Or just use relativedelta + 1?
    
    diff = relativedelta(end_date, start_date)
    total_months = diff.years * 12 + diff.months
    
    # Heuristic: if total_months is 0 (same month), count as 1
    if total_months == 0:
        total_months = 1
        
    return total_months

def normalize_date(date_obj: datetime) -> str:
    """Returns YYYY-MM string."""
    if not date_obj:
        return ""
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
    
    date_part_pat = fr'(?:(?:{month_pat}|{month_digit_pat})[\s/]+)?{year_pat}'
    
    # Full Range Pattern
    range_pat = fr'({date_part_pat})\s*-\s*({date_part_pat}|present|aujourd\'hui|now|actuel|current)'
    
    # Find Ranges first
    for match in re.finditer(range_pat, text, flags=re.IGNORECASE):
        raw = match.group(0)
        start_raw = match.group(1)
        end_raw = match.group(2)
        
        start_dt = dateparser.parse(start_raw, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'first'})
        
        is_current = False
        end_dt = None
        
        if re.match(r'(?i)^(present|aujourd\'hui|now|actuel|current)$', end_raw):
            is_current = True
        else:
            end_dt = dateparser.parse(end_raw, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'last'})
            
        if start_dt:
            anchor = DateAnchor(
                raw=raw,
                start=normalize_date(start_dt),
                end=normalize_date(end_dt) if end_dt else None,
                is_current=is_current,
                precision="month" if len(start_raw) > 4 else "year",
                start_idx=match.start(),
                end_idx=match.end()
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
        
        if start_dt:
            anchor = DateAnchor(
                raw=raw,
                start=normalize_date(start_dt),
                end=None,
                is_current=True,
                precision="month" if len(start_raw) > 4 else "year",
                start_idx=match.start(),
                end_idx=match.end()
            )
            anchors.append(anchor)

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
            
        raw = match.group(0)
        start_dt = dateparser.parse(raw)
        
        if start_dt:
            anchor = DateAnchor(
                raw=raw,
                start=normalize_date(start_dt),
                end=normalize_date(start_dt), # Single year = start and end same year roughly
                is_current=False,
                precision="year",
                start_idx=start_pos,
                end_idx=end_pos
            )
            anchors.append(anchor)

    # Sort anchors by position
    anchors.sort(key=lambda x: x.start_idx)
    
    return anchors
