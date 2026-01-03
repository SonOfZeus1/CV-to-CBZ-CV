import logging
from typing import Dict, Any, List
from dataclasses import asdict

logger = logging.getLogger(__name__)

def validate_extraction(cv_data: Dict[str, Any], anchor_map: Dict[str, Any]) -> List[str]:
    """
    Validates the extracted CV data against the Anchor Map.
    Returns a list of warnings/errors.
    """
    issues = []
    
    # 1. Validate Experiences
    experiences = cv_data.get("experience", [])
    blocks = {b["id"]: b for b in anchor_map.get("blocks", [])}
    anchors_dates = {a["id"]: a for a in anchor_map.get("anchors", {}).get("dates", [])}
    
    for i, exp in enumerate(experiences):
        # Check Block ID
        block_id = exp.get("block_id")
        if not block_id:
            issues.append(f"Experience #{i+1} ({exp.get('job_title')}) has no block_id.")
            continue
            
        if block_id not in blocks:
            issues.append(f"Experience #{i+1} references non-existent block_id '{block_id}'.")
            continue
            
        block = blocks[block_id]
        
        # Check Date Anchor
        anchor_ids = exp.get("anchor_ids", [])
        date_anchor_found = False
        
        for aid in anchor_ids:
            if aid in anchors_dates:
                date_anchor_found = True
                anchor = anchors_dates[aid]
                # Validate Date Match (Loose check)
                # If JSON says "2018-01" and Anchor says "2018-01", good.
                # If JSON says "2018" and Anchor says "2018-01", acceptable.
                # If JSON says "2019" and Anchor says "2018", BAD.
                
                json_start = exp.get("date_start", "")
                anchor_start = anchor.get("start", "")
                
                if json_start and anchor_start:
                    if not json_start.startswith(anchor_start[:4]): # Year mismatch
                        issues.append(f"Experience #{i+1} Date Mismatch: JSON '{json_start}' vs Anchor '{anchor_start}'")
                        
        if not date_anchor_found:
             # It's okay if no date anchor is linked, but suspicious if the block HAS date anchors
             block_anchors = block.get("anchors", [])
             block_date_anchors = [a for a in block_anchors if a in anchors_dates]
             if block_date_anchors:
                 issues.append(f"Experience #{i+1} ignores available date anchors in its block ({block_date_anchors}).")

        # Check Text Overlap (Hallucination Check)
        # We check if tasks are somewhat present in the block text
        block_text_lower = block.get("text", "").lower()
        tasks = exp.get("tasks", [])
        
        for task in tasks:
            # Simple check: do at least 50% of significant words appear in block?
            words = [w for w in task.lower().split() if len(w) > 3]
            if not words: continue
            
            found_count = sum(1 for w in words if w in block_text_lower)
            ratio = found_count / len(words)
            
            if ratio < 0.5:
                issues.append(f"Experience #{i+1} Potential Hallucination: Task '{task[:30]}...' not found in source block (Match: {ratio:.0%}).")

    return issues
