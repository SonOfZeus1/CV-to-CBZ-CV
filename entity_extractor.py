import re
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class EntityAnchor:
    id: str
    raw: str
    type: str # "role", "company"
    confidence: str # "high", "medium", "low"
    start_idx: int
    end_idx: int

def extract_entity_anchors(text: str) -> List[EntityAnchor]:
    """
    Extracts potential Role and Company anchors based on heuristics.
    """
    anchors = []
    anchor_count = 0
    
    # Keywords
    # Keywords (Roots for broad matching)
    # We use \b to ensure it starts the word (e.g. "Admin" matches "Administrator")
    role_keywords = [
        # Tech / Engineering
        r'\bDev', r'\bDév', r'\bProg', r'\bSoft', r'\bEngin', r'\bIng', r'\bArchi', r'\bTech', 
        r'\bData', r'\bSys', r'\bNet', r'\bWeb', r'\bFull', r'\bFront', r'\bBack',
        r'\bSecur', r'\bS[ée]cur', r'\bCyber', r'\bCloud', r'\bOps', r'\bQA', r'\bTest', r'\bScrum',
        r'\bAgile', r'\bProduct', r'\bProject', r'\bProjet', r'\bLead',
        
        # Management / Leadership
        r'\bManag', r'\bDirect', r'\bChief', r'\bChef', r'\bHead', r'\bLead', 
        r'\bSuperv', r'\bCoord', r'\bAdmin', r'\bExec', r'\bEx[ée]c', r'\bPres', r'\bPr[ée]s', r'\bVP', 
        r'\bFound', r'\bOwn', r'\bGér', r'\bResp', r'\bDir',
        
        # Business / Finance / Ops
        r'\bAnaly', r'\bConsult', r'\bStrat', r'\bBusin', r'\bAffair', 
        r'\bOper', r'\bOpér', r'\bFinan', r'\bCompt', r'\bAccount', 
        r'\bMarket', r'\bSale', r'\bVend', r'\bComm', r'\bRelat',
        
        # HR / Legal / Support
        r'\bRH', r'\bHR', r'\bRecrut', r'\bRecruit', r'\bTalent', 
        r'\bTrain', r'\bForm', r'\bLegal', r'\bJurid', r'\bAvocat',
        r'\bAssist', r'\bSupport', r'\bHelp', r'\bService', r'\bClient',
        
        # Levels / Status
        r'\bSenior', r'\bS[ée]nior', r'\bJunior', r'\bPrinc', r'\bStaff', r'\bIntern', r'\bStag',
        r'\bApprent', r'\bFreelance', r'\bIndep', r'\bIndép', r'\bContract',
        
        # Other Common Roles
        r'\bAgent', r'\bOffic', r'\bClerk', r'\bCommis', r'\bSpec', r'\bSp[ée]c', r'\bExpert',
        r'\bTeach', r'\bEnseign', r'\bFormateur', r'\bCoach', r'\bWriter', r'\bRédac'
    ]
    
    # Compile regex
    role_pattern = re.compile('|'.join(role_keywords), re.IGNORECASE)
    
    lines = text.split('\n')
    current_idx = 0
    
    for line in lines:
        line_len = len(line)
        line_clean = line.strip()
        
        if not line_clean:
            current_idx += line_len + 1 # +1 for newline
            continue
            
        # --- STRUCTURAL HEURISTICS (The Filter) ---
        
        # 1. Exclusion Rules (Negative Filters)
        # - Starts with bullet
        if re.match(r'^[\u2022\-\*\+]', line_clean):
            current_idx += line_len + 1
            continue
            
        # - Ends with period (likely a sentence)
        if line_clean.endswith('.'):
             # Exception: "Inc." or "Ltd." but we removed company logic, so mostly valid rule.
             # But some roles might end with dot? Rare. Let's be safe.
             if not line_clean.lower().endswith('inc.'):
                 current_idx += line_len + 1
                 continue
                 
        # - Contains digits (KPIs, Dates mixed in line)
        # Allow a single digit (e.g. "Level 2 Support"), but reject if many
        digit_count = sum(c.isdigit() for c in line_clean)
        if digit_count > 2: # More than 2 digits -> likely date or KPI
             current_idx += line_len + 1
             continue
             
        # 2. Positive Structural Rules
        words = line_clean.split()
        word_count = len(words)
        
        # - Length Constraint (2 to 8 words usually)
        if word_count < 1 or word_count > 10: # Relaxed slightly to 10
            current_idx += line_len + 1
            continue
            
        # - Capitalization Ratio
        # Count words starting with Uppercase
        cap_count = sum(1 for w in words if w[0].isupper())
        cap_ratio = cap_count / word_count if word_count > 0 else 0
        
        # Heuristic: Roles are usually Title Cased
        # We require > 40% capitalization (allows for some lowercase prepositions like "of", "de", "and")
        if cap_ratio < 0.4:
             current_idx += line_len + 1
             continue

        # --- KEYWORD MATCHING (The Confirmation) ---
        role_match = role_pattern.search(line_clean)
        
        if role_match:
            anchor_count += 1
            # Confidence based on structure
            # High if short and highly capitalized
            confidence = "high"
            if word_count > 6 or cap_ratio < 0.6:
                confidence = "medium"
                
            anchors.append(EntityAnchor(
                id=f"e{anchor_count}",
                raw=line_clean,
                type="role",
                confidence=confidence,
                start_idx=current_idx + line.find(line_clean),
                end_idx=current_idx + line.find(line_clean) + len(line_clean)
            ))

        current_idx += line_len + 1
        
    return anchors
