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
    id: str
    raw: str
    start: str # YYYY-MM or YYYY
    end: Optional[str] # YYYY-MM or YYYY or None
    type: str # "range", "range_present", "single_year", "month_year", "since"
    is_current: bool
    context: str # Surrounding text snippet
    likely_type: str # "education", "experience", "project", "unknown"
    start_idx: int
    end_idx: int

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

def classify_context(text: str) -> str:
    """Simple keyword-based classification of context."""
    text_lower = text.lower()
    
    edu_keywords = ["université", "university", "école", "school", "college", "diplôme", "degree", "bac", "master", "phd", "certificat", "certification", "formation", "bacc"]
    exp_keywords = ["expérience", "experience", "emploi", "work", "job", "poste", "role", "senior", "junior", "manager", "développeur", "ingénieur", "consultant", "inc.", "ltd", "s.a.", "corp", "groupe"]
    
    edu_score = sum(1 for k in edu_keywords if k in text_lower)
    exp_score = sum(1 for k in exp_keywords if k in text_lower)
    
    if edu_score > exp_score:
        return "education"
    if exp_score > edu_score:
        return "experience"
    return "unknown"

def extract_date_anchors(text: str) -> List[DateAnchor]:
    """
    Finds rich date anchors in text.
    Returns sorted list of DateAnchors.
    """
    anchors = []
    anchor_count = 0
    
    # Regex Patterns
    
    # Components
    year_pat = r'(?:19|20)[0-9O]{2}' # 1990-2099
    month_pat = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|janv|fév|mars|avr|mai|juin|juil|août|sept|oct|nov|déc)[a-z]*\.?'
    month_digit_pat = r'(?:0?[1-9]|1[0-2])'
    
    # Date Part: "Jan 2020", "01/2020", "2020"
    # We remove inner named groups to avoid redefinition error when used multiple times
    date_part_pat = fr'(?:(?:{month_pat}|{month_digit_pat})[\s/]+)?(?:{year_pat})'
    
    # 1. Ranges: "Date - Date" or "Date - Present"
    # Separators: " - ", " – ", " to ", " à "
    separator = r'\s*(?:-|–|to|à)\s*'
    present_pat = r'(?i)(?:present|aujourd\'hui|now|actuel|current|en cours)'
    
    range_regex = fr'(?P<start>{date_part_pat}){separator}(?P<end>{date_part_pat}|{present_pat})'
    
    # 2. Since: "Depuis Date"
    since_regex = fr'(?i)(?:depuis|since)\s+(?P<start>{date_part_pat})'
    
    # 3. Single Dates (Isolated) - We'll do a separate pass or careful regex
    # For now, let's capture them if they haven't been captured by ranges
    
    # --- PASS 1: RANGES ---
    for match in re.finditer(range_regex, text, flags=re.IGNORECASE):
        raw = match.group(0)
        start_str = match.group('start')
        end_str = match.group('end')
        
        # Parse Start
        start_dt = dateparser.parse(start_str, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'first'})
        if not start_dt: continue
        
        start_is_year = len(start_str.strip()) <= 4
        
        # Parse End
        is_current = False
        end_dt = None
        end_is_year = False
        
        if re.match(present_pat, end_str):
            is_current = True
            anchor_type = "range_present"
        else:
            end_dt = dateparser.parse(end_str, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'last'})
            if end_dt:
                anchor_type = "range"
                end_is_year = len(end_str.strip()) <= 4
            else:
                continue # Invalid end date
                
        # Context
        start_idx = match.start()
        end_idx = match.end()
        context_start = max(0, start_idx - 50)
        context_end = min(len(text), end_idx + 50)
        context = text[context_start:context_end].replace('\n', ' ').strip()
        
        anchor_count += 1
        anchors.append(DateAnchor(
            id=f"d{anchor_count}",
            raw=raw,
            start=normalize_date(start_dt, start_is_year),
            end=normalize_date(end_dt, end_is_year) if end_dt else None,
            type=anchor_type,
            is_current=is_current,
            context=context,
            likely_type=classify_context(context),
            start_idx=start_idx,
            end_idx=end_idx
        ))

    # --- PASS 2: SINCE ---
    for match in re.finditer(since_regex, text, flags=re.IGNORECASE):
        # Check overlap
        if any(a.start_idx <= match.start() < a.end_idx for a in anchors):
            continue
            
        raw = match.group(0)
        start_str = match.group('start')
        start_dt = dateparser.parse(start_str, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'first'})
        
        if start_dt:
            start_is_year = len(start_str.strip()) <= 4
            
            start_idx = match.start()
            end_idx = match.end()
            context_start = max(0, start_idx - 50)
            context_end = min(len(text), end_idx + 50)
            context = text[context_start:context_end].replace('\n', ' ').strip()
            
            anchor_count += 1
            anchors.append(DateAnchor(
                id=f"d{anchor_count}",
                raw=raw,
                start=normalize_date(start_dt, start_is_year),
                end=None,
                type="since",
                is_current=True,
                context=context,
                likely_type=classify_context(context),
                start_idx=start_idx,
                end_idx=end_idx
            ))

    # --- PASS 3: SINGLE DATES (Careful) ---
    # We look for Month Year or Year
    single_regex = fr'\b{date_part_pat}\b'
    
    for match in re.finditer(single_regex, text, flags=re.IGNORECASE):
        start_pos = match.start()
        end_pos = match.end()
        
        # Check overlap
        if any(a.start_idx <= start_pos < a.end_idx for a in anchors):
            continue
            
        raw = match.group(0)
        
        # Filter out noise (phone numbers, etc.)
        # If it's just a year (4 digits), be strict
        if re.match(r'^\d{4}$', raw):
            # Check boundaries (not part of a longer number)
            if re.search(r'\d', text[start_pos-1:start_pos]) or re.search(r'\d', text[end_pos:end_pos+1]):
                continue
            # Check context (avoid "ISO 9001", "T4", etc.)
            line_start = text.rfind('\n', 0, start_pos) + 1
            line_end = text.find('\n', end_pos)
            if line_end == -1: line_end = len(text)
            line = text[line_start:line_end]
            if "iso" in line.lower() or "code" in line.lower():
                continue
                
        start_dt = dateparser.parse(raw, languages=['fr', 'en'])
        if start_dt:
            # Determine type
            is_year_only = bool(re.match(r'^\d{4}$', raw.strip()))
            anchor_type = "single_year" if is_year_only else "month_year"
            
            context_start = max(0, start_pos - 50)
            context_end = min(len(text), end_pos + 50)
            context = text[context_start:context_end].replace('\n', ' ').strip()
            
            anchor_count += 1
            anchors.append(DateAnchor(
                id=f"d{anchor_count}",
                raw=raw,
                start=normalize_date(start_dt, is_year_only),
                end=normalize_date(start_dt, is_year_only), # Single date = start/end same
                type=anchor_type,
                is_current=False,
                context=context,
                likely_type=classify_context(context),
                start_idx=start_pos,
                end_idx=end_pos
            ))

    # Sort anchors by position
    anchors.sort(key=lambda x: x.start_idx)
    
    return anchors
