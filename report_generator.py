import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
import re

logger = logging.getLogger(__name__)

def parse_date(date_str: str) -> Optional[datetime]:
    """Parses a date string (YYYY-MM or YYYY) into a datetime object."""
    if not date_str:
        return None
    try:
        # Try YYYY-MM
        return datetime.strptime(date_str, "%Y-%m")
    except ValueError:
        try:
            # Try YYYY
            return datetime.strptime(date_str, "%Y")
        except ValueError:
            return None

def calculate_total_experience(experiences: List[Dict[str, Any]]) -> float:
    """
    Calculates total years of experience from a list of experience entries.
    Handles overlaps by merging time ranges.
    """
    if not experiences:
        return 0.0

    ranges = []
    for exp in experiences:
        start_str = exp.get('date_start')
        end_str = exp.get('date_end')
        is_current = exp.get('is_current', False)

        start_date = parse_date(start_str)
        if not start_date:
            continue

        if is_current:
            end_date = datetime.now()
        else:
            end_date = parse_date(end_str)
            if not end_date:
                # If start exists but end is missing and not current, assume 1 month duration?
                # Or skip? Let's assume it ends same month to be safe (0 duration) or skip.
                # Better to use start_date as end_date for minimal duration.
                end_date = start_date

        if end_date < start_date:
            end_date = start_date

        ranges.append((start_date, end_date))

    if not ranges:
        return 0.0

    # Sort ranges by start date
    ranges.sort(key=lambda x: x[0])

    # Merge overlapping ranges
    merged_ranges = []
    if ranges:
        curr_start, curr_end = ranges[0]
        for next_start, next_end in ranges[1:]:
            if next_start <= curr_end:
                # Overlap, extend current end if needed
                curr_end = max(curr_end, next_end)
            else:
                # No overlap, push current and start new
                merged_ranges.append((curr_start, curr_end))
                curr_start, curr_end = next_start, next_end
        merged_ranges.append((curr_start, curr_end))

    # Calculate total duration
    total_days = 0
    for start, end in merged_ranges:
        total_days += (end - start).days

    # Convert to years (365.25 days)
    total_years = total_days / 365.25
    return round(total_years, 1)

def get_latest_experience(experiences: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Identifies the latest experience based on end date.
    Returns the experience dict or empty dict.
    """
    if not experiences:
        return {}

    # Sort by end date (descending). 
    # Current roles (no end date or future) should be first.
    
    def sort_key(exp):
        if exp.get('is_current'):
            return datetime.max
        d = parse_date(exp.get('date_end'))
        return d if d else datetime.min

    sorted_exps = sorted(experiences, key=sort_key, reverse=True)
    return sorted_exps[0]

def format_candidate_row(json_data: Dict[str, Any], md_link: str, emplacement: str = "Processed", json_link: str = "", cv_link: str = "") -> List[str]:
    """
    Formats the extracted JSON data into a list of strings for the Excel row.
    Columns: 
    1. First Name
    2. Last Name
    3. Email
    4. Phone
    5. Address
    6. Languages
    7. Total Experience (Years)
    8. Latest Job Title
    9. Latest Location
    10. MD Source Link
    11. Action
    12. Emplacement
    """
    # The JSON structure has changed. It now uses 'basics' for contact info.
    basics = json_data.get('basics', {})
    experiences = json_data.get('experience', []) # Note: 'experience' not 'experiences' in the new JSON

    # 1-2. Name (Split into First/Last if possible, otherwise put full name in First Name)
    full_name = basics.get('name', '')
    if ' ' in full_name:
        # Simple split
        parts = full_name.split(' ', 1)
        first_name = parts[0]
        last_name = parts[1]
    else:
        first_name = full_name
        last_name = ""

    # 3-6. Contact Info
    email = basics.get('email', '')
    phone = basics.get('phone', '')
    # Fix for Excel interpreting + as formula
    if phone and phone.strip().startswith('+'):
        phone = f"'{phone}"
        
    address = basics.get('address', '')
    
    langs = basics.get('languages', [])
    if isinstance(langs, list):
        languages = ", ".join(langs)
    else:
        languages = str(langs)

    # 7. Total Experience
    # Use the pre-calculated value from JSON if available, otherwise calculate
    total_exp = basics.get('total_experience')
    if total_exp is None:
        total_exp = calculate_total_experience(experiences)

    # 8-9. Latest Role
    # Check is_cv flag
    is_cv = json_data.get('is_cv', True)
    
    if not is_cv:
        latest_title = "NON-CV"
        latest_location = ""
    else:
        latest_exp = get_latest_experience(experiences)
        latest_title = latest_exp.get('job_title', '')
        latest_location = latest_exp.get('location', '')

    return [
        first_name,
        last_name,
        email,
        phone,
        address,
        languages,
        total_exp,
        latest_title,
        latest_location,
        md_link,
        "", # Action Column (Empty by default)
        emplacement, # Emplacement Column
        json_link, # Lien JSON Column
        cv_link # Lien CV Column
    ]
