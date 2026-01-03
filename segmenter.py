import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from date_extractor import DateAnchor
from entity_extractor import EntityAnchor

logger = logging.getLogger(__name__)

@dataclass
class Block:
    id: str
    type: str # "HEADER", "SUMMARY", "EXPERIENCE", "EDUCATION", "SKILLS", "PROJECTS", "UNKNOWN"
    text: str
    start_idx: int
    end_idx: int
    sub_blocks: List['Block'] = field(default_factory=list)
    anchors: List[str] = field(default_factory=list) # List of anchor IDs contained in this block

def segment_cv(text: str, date_anchors: List[DateAnchor], entity_anchors: List[EntityAnchor]) -> List[Block]:
    """
    Segments the CV into high-level sections and sub-blocks.
    """
    blocks = []
    
    # 1. Detect Major Sections
    # We use common headers
    headers = {
        "EXPERIENCE": r'(?i)^\s*(?:exp[eé]rience|work history|parcours|emploi|professional experience|exp[eé]rience professionnelle)\s*$',
        "EDUCATION": r'(?i)^\s*(?:education|formation|etudes|études|academic|dipl[ôo]me)\s*$',
        "SKILLS": r'(?i)^\s*(?:skills|comp[eé]tences|aptitudes|technologies|outils)\s*$',
        "PROJECTS": r'(?i)^\s*(?:projects|projets|r[eé]alisations)\s*$',
        "LANGUAGES": r'(?i)^\s*(?:languages|langues)\s*$',
        "SUMMARY": r'(?i)^\s*(?:summary|profil|profile|objectif|intro)\s*$'
    }
    
    lines = text.split('\n')
    current_section = "HEADER"
    current_lines = []
    section_start_idx = 0
    current_idx = 0
    
    section_map = [] # List of (type, start_idx, end_idx, text)
    
    for i, line in enumerate(lines):
        line_clean = line.strip()
        line_len = len(line) + 1 # +1 for newline
        
        # Check for Header
        is_header = False
        for section_type, pattern in headers.items():
            if re.match(pattern, line_clean):
                # Found a new section
                # Save previous section
                if current_lines:
                    section_text = "\n".join(current_lines)
                    section_map.append({
                        "type": current_section,
                        "start": section_start_idx,
                        "end": current_idx,
                        "text": section_text
                    })
                
                # Start new section
                current_section = section_type
                current_lines = [] # Don't include header in text? Or maybe yes for context. Let's exclude header line from content for cleaner text.
                section_start_idx = current_idx + line_len
                is_header = True
                break
        
        if not is_header:
            current_lines.append(line)
            
        current_idx += line_len

    # Save last section
    if current_lines:
        section_text = "\n".join(current_lines)
        section_map.append({
            "type": current_section,
            "start": section_start_idx,
            "end": current_idx,
            "text": section_text
        })
        
    # 2. Create Blocks and Sub-segment Experience
    block_count = 0
    
    for sec in section_map:
        block_count += 1
        main_block = Block(
            id=f"b{block_count}",
            type=sec["type"],
            text=sec["text"],
            start_idx=sec["start"],
            end_idx=sec["end"]
        )
        
        # Assign Anchors to Main Block
        # Check which anchors fall within [start, end]
        block_anchors = []
        for da in date_anchors:
            if sec["start"] <= da.start_idx < sec["end"]:
                block_anchors.append(da.id)
        for ea in entity_anchors:
            if sec["start"] <= ea.start_idx < sec["end"]:
                block_anchors.append(ea.id)
        main_block.anchors = block_anchors
        
        # Sub-segmentation for EXPERIENCE
        if sec["type"] == "EXPERIENCE":
            sub_blocks = sub_segment_experience(sec["text"], sec["start"], date_anchors, entity_anchors)
            main_block.sub_blocks = sub_blocks
            
        blocks.append(main_block)
        
    return blocks

def sub_segment_experience(text: str, offset: int, date_anchors: List[DateAnchor], entity_anchors: List[EntityAnchor]) -> List[Block]:
    """
    Divides an Experience section into individual job blocks based on anchors.
    """
    sub_blocks = []
    lines = text.split('\n')
    current_sub_lines = []
    current_sub_start = offset
    current_idx = offset
    sub_count = 0
    
    # We need to map anchors to local text indices
    # Filter anchors relevant to this section
    section_date_anchors = [a for a in date_anchors if offset <= a.start_idx < offset + len(text)]
    section_entity_anchors = [a for a in entity_anchors if offset <= a.start_idx < offset + len(text)]
    
    # Sort all relevant anchors by position
    all_anchors = sorted(section_date_anchors + section_entity_anchors, key=lambda x: x.start_idx)
    
    # Identify "Split Points"
    # A split point is a line that starts with a Strong Anchor (Date Range or High Confidence Role/Company)
    # AND is significantly far from the previous split point (to avoid splitting "Role\nCompany\nDate" into 3 blocks)
    
    split_indices = []
    
    for anchor in all_anchors:
        # Check if this anchor is a "Strong Starter"
        is_strong = False
        if isinstance(anchor, DateAnchor):
            if anchor.type in ["range", "range_present", "since"]:
                is_strong = True
        elif isinstance(anchor, EntityAnchor):
            if anchor.confidence == "high":
                is_strong = True
                
        if is_strong:
            # Find the line start for this anchor
            # We approximate by checking which line contains the anchor start_idx
            # But since we iterate lines, maybe better to just mark lines as "starts"
            pass

    # Simpler approach: Iterate lines, check if line *starts* with or *contains* a strong anchor
    # If yes, and we have accumulated content, split.
    
    for line in lines:
        line_len = len(line) + 1
        line_start_abs = current_idx
        line_end_abs = current_idx + len(line)
        
        # Check if this line triggers a split
        is_split_line = False
        
        # Check intersection with Strong Anchors
        for anchor in all_anchors:
            # If anchor starts in this line
            if line_start_abs <= anchor.start_idx < line_end_abs:
                # Is it strong?
                if isinstance(anchor, DateAnchor) and anchor.type in ["range", "range_present", "since"]:
                    is_split_line = True
                    break
                # For entities, we are more careful. Only if it's a Role or Company AND looks like a header (short line)
                if isinstance(anchor, EntityAnchor) and anchor.confidence == "high" and len(line.strip()) < 80:
                    is_split_line = True
                    break
        
        # Heuristic: Don't split if we just started (e.g. Role line followed by Date line)
        # We want to group "Role + Company + Date" into one block header.
        # So we only split if we have "enough" content in the previous block OR if the previous block looks "complete".
        # Actually, simpler: Split whenever we hit a Date Range. That's the strongest signal.
        # Entities are weaker splitters.
        
        if is_split_line:
            # If we have content, save it
            if current_sub_lines:
                # But wait! Is this line part of the *previous* block or the *new* block?
                # Usually a Date Range starts a new block.
                
                # Check if the *previous* lines were just a header (e.g. Company name).
                # If current_sub_lines is very short (< 3 lines) and contains entities, maybe we are still in the header of the *same* job?
                # No, usually "Date" is the best separator.
                
                # Let's save previous block
                sub_count += 1
                sub_text = "\n".join(current_sub_lines)
                
                # Assign anchors
                sub_block_anchors = []
                sb_start = current_sub_start
                sb_end = current_idx
                for a in all_anchors:
                    if sb_start <= a.start_idx < sb_end:
                        sub_block_anchors.append(a.id)

                sub_blocks.append(Block(
                    id=f"sb{sub_count}",
                    type="JOB_ENTRY",
                    text=sub_text,
                    start_idx=sb_start,
                    end_idx=sb_end,
                    anchors=sub_block_anchors
                ))
                
                current_sub_lines = []
                current_sub_start = current_idx
                
        current_sub_lines.append(line)
        current_idx += line_len
        
    # Save last sub-block
    if current_sub_lines:
        sub_count += 1
        sub_text = "\n".join(current_sub_lines)
        sb_start = current_sub_start
        sb_end = current_idx
        
        sub_block_anchors = []
        for a in all_anchors:
            if sb_start <= a.start_idx < sb_end:
                sub_block_anchors.append(a.id)
                
        sub_blocks.append(Block(
            id=f"sb{sub_count}",
            type="JOB_ENTRY",
            text=sub_text,
            start_idx=sb_start,
            end_idx=sb_end,
            anchors=sub_block_anchors
        ))
        
    return sub_blocks
