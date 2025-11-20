import re
import os
import io
import fitz  # PyMuPDF
import docx
import pytesseract
from PIL import Image
import spacy
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime

# --- SCHÉMA DE DONNÉES (V6 - Clean & Intelligent) ---

@dataclass
class ExperienceEntry:
    title: str = ""
    company: str = ""
    role: str = ""
    location: str = ""
    date_start: str = ""
    date_end: str = ""
    duration: str = ""
    
    # Segmentation fine
    context: str = ""
    responsibilities: List[str] = field(default_factory=list)
    achievements: List[str] = field(default_factory=list)
    
    # Filet de sécurité local
    full_text: str = "" 

@dataclass
class EducationEntry:
    degree: str = ""
    institution: str = ""
    date_start: str = ""
    date_end: str = ""
    full_text: str = ""

@dataclass
class CVData:
    meta: Dict[str, str] = field(default_factory=dict)
    basics: Dict[str, str] = field(default_factory=lambda: {
        "name": "", "email": "", "phone": "", "location": ""
    })
    summary: str = "" # Paragraphe propre
    skills_tech: List[str] = field(default_factory=list)
    skills_soft: List[str] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    education: List[EducationEntry] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    achievements_global: List[str] = field(default_factory=list) 
    extra_info: List[str] = field(default_factory=list)
    unmapped: List[str] = field(default_factory=list) 
    raw_text: str = ""

    def to_dict(self):
        return asdict(self)

# --- OUTILS ---

NLP = None
def load_spacy_model():
    global NLP
    if NLP is None:
        try:
            NLP = spacy.load("en_core_web_sm")
        except OSError:
            from spacy.cli import download
            download("en_core_web_sm")
            NLP = spacy.load("en_core_web_sm")
    return NLP

def extract_text_from_docx(file_path: str) -> str:
    try:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception:
        return ""

def extract_text_from_pdf(file_path: str) -> tuple[str, bool]:
    text = ""
    ocr_applied = False
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
            
            # Check OCR
            avg_chars_per_page = len(text.strip()) / len(doc) if len(doc) > 0 else 0
            if avg_chars_per_page < 50:
                text = "" 
                ocr_applied = True
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    text += pytesseract.image_to_string(image) + "\n"
                    del pix; del image
    except Exception:
        pass
    return text, ocr_applied

# --- PARSER INTELLIGENT ---

class UniversalParser:
    def __init__(self, text: str):
        self.raw_text = text
        self.nlp = load_spacy_model()
        self.doc = self.nlp(text[:100000])
        self.lines = self.pre_process_text(text) # Nettoyage initial

    def pre_process_text(self, text: str) -> List[str]:
        """Nettoie le texte des artefacts répétitifs (headers/footers)."""
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        cleaned_lines = []
        
        # Artefacts à supprimer
        patterns_to_remove = [
            r'^page\s*\d+\s*(/|sur|of)\s*\d+$', # Page X/Y
            r'^curriculum\s*vitae$', # CV répété
            r'^cv$',
            # Email répété en footer ? Difficile sans contexte page
        ]
        
        for line in lines:
            keep = True
            for pat in patterns_to_remove:
                if re.match(pat, line, re.IGNORECASE):
                    keep = False
                    break
            if keep:
                cleaned_lines.append(line)
        return cleaned_lines

    def normalize_paragraph(self, text_lines: List[str]) -> str:
        """Fusionne une liste de lignes en un paragraphe propre."""
        full_text = " ".join(text_lines)
        # Nettoyage des espaces multiples
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        return full_text

    def extract_basics(self) -> Dict[str, Any]:
        full_text = "\n".join(self.lines)
        
        # Regex améliorées
        email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_regex = r'(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?(\d{2,4}[-.\s]?){2,4}'
        link_regex = r'(https?://\S+|www\.\S+|linkedin\.com/in/\S+|github\.com/\S+)'
        
        emails = re.findall(email_regex, full_text)
        phones = [m.group(0).strip() for m in re.finditer(phone_regex, full_text) if len(re.sub(r'\D', '', m.group(0))) >= 9]
        links = list(set(re.findall(link_regex, full_text, re.IGNORECASE)))

        # Nom: priorité majuscules en haut
        name = "Inconnu"
        blacklist = {"curriculum", "vitae", "resume", "cv", "email", "phone", "page", "profil", "summary"}
        for line in self.lines[:40]:
            words = line.split()
            if 2 <= len(words) <= 4:
                if any(w.lower() in blacklist for w in words): continue
                if any(c.isdigit() or c in "@+/" for c in line): continue
                
                if line.isupper(): 
                    name = line.title()
                    break
                if line.istitle() and name == "Inconnu": 
                    name = line
        
        return {
            "name": name, 
            "email": emails[0] if emails else "", 
            "phone": phones[0] if phones else "", 
            "links": links
        }

    def extract_skills(self) -> Dict[str, List[str]]:
        tech_keywords = {"python", "java", "c++", "sql", "javascript", "react", "docker", "aws", "linux", "git", "html", "css", "kubernetes", "azure", "vba", "oracle", "visio", "jira", "confluence", "power bi", "tableau", "sap", ".net", "c#", "spring", "angular"}
        soft_keywords = {"management", "communication", "leadership", "agile", "scrum", "anglais", "français", "espagnol", "analyste", "stratégique", "coordination", "gestion de projet", "autonomie"}
        
        tech, soft = set(), set()
        for token in [t.text.lower() for t in self.doc if not t.is_stop]:
            if token in tech_keywords: tech.add(token.capitalize())
            if token in soft_keywords: soft.add(token.capitalize())
        return {"tech": list(tech), "soft": list(soft)}

    def segment_experiences(self, raw_lines: List[str]) -> List[ExperienceEntry]:
        """Découpe intelligente et complète des mandats."""
        entries = []
        if not raw_lines: return entries

        # 1. IDENTIFICATION DES BORNES DE DÉBUT
        # Une borne est soit un "MANDAT X", soit une ligne de Date claire
        # Regex date : "Avril 2020 - Présent" ou "2020-2021"
        date_pattern = r'([A-Za-zûé]+\s\d{4}|\d{2}/\d{4}|\d{4})\s*[à-]\s*([A-Za-zûé]+\s\d{4}|aujourd’hui|présent|maintenant)'
        mandat_pattern = r'(?i)^\s*(MANDAT\s*\d+|EXPÉRIENCE\s*\d+|POSTE\s*\d+)'
        
        bounds = [] # Indices de début de bloc
        for i, line in enumerate(raw_lines):
            # Priorité 1: Mandat explicite
            if re.match(mandat_pattern, line):
                bounds.append(i)
                continue
            
            # Priorité 2: Ligne de date EN DÉBUT de bloc probable (pas au milieu d'un texte)
            # On regarde si c'est une ligne courte (< 60 chars) qui ressemble à un header
            if len(line) < 80 and re.search(date_pattern, line, re.IGNORECASE):
                # On vérifie que ce n'est pas déjà couvert par un mandat juste avant
                if not bounds or (i - bounds[-1] > 2): 
                     bounds.append(i)

        # Si aucune borne trouvée, tout est un seul bloc
        if not bounds:
            bounds = [0]
            
        # 2. DÉCOUPAGE ET PARSING
        for idx, start_index in enumerate(bounds):
            end_index = bounds[idx+1] if idx+1 < len(bounds) else len(raw_lines)
            block_lines = raw_lines[start_index:end_index]
            
            entry = ExperienceEntry()
            entry.full_text = "\n".join(block_lines) # Sauvegarde 100% du texte
            
            # HEADER ANALYSIS (5 premières lignes)
            header_pool = block_lines[:6]
            
            # Extraction Dates (prioritaire)
            for line in header_pool:
                m = re.search(r'([A-Za-zûé]+\s\d{4}|\d{2}/\d{4})\s*[à-]\s*([A-Za-zûé]+\s\d{4}|aujourd’hui|présent|maintenant)(?:\s*\(([^)]+)\))?', line, re.IGNORECASE)
                if m:
                    entry.date_start = m.group(1)
                    entry.date_end = m.group(2)
                    entry.duration = m.group(3) if m.group(3) else ""
                    break
            
            # Extraction Titre / Company
            # Si "MANDAT", le titre est explicite
            if re.match(mandat_pattern, header_pool[0]):
                entry.title = header_pool[0]
                # Recherche Company après
                for line in header_pool[1:]:
                    if len(line) < 50 and not re.search(date_pattern, line):
                        entry.company = line; break
            else:
                # Sinon, ligne 1 = Titre ou Company ?
                # Heuristique simple : Titre souvent en premier
                entry.title = header_pool[0]
                if len(header_pool) > 1:
                    # Si ligne 2 n'est pas la date, c'est peut-être la boite
                    if not re.search(date_pattern, header_pool[1]):
                        entry.company = header_pool[1]

            # CONTENT ANALYSIS (tout le reste)
            context_acc = []
            current_mode = "context" # context, responsibilities, achievements
            
            for line in block_lines:
                # Skip lines used in header fields strictly if they are identical
                if line == entry.title or line == entry.company or (entry.date_start and entry.date_start in line):
                    continue
                
                # Detection listes
                if re.match(r'^[-•o*]\s', line):
                    clean = re.sub(r'^[-•o*]\s?', '', line).strip()
                    if current_mode == "achievements":
                        entry.achievements.append(clean)
                    else:
                        entry.responsibilities.append(clean)
                        current_mode = "responsibilities"
                # Detection sous-titres
                elif any(k in line.lower() for k in ["réalisations", "résultats", "bénéfices", "livrables", "valeur ajoutée"]):
                    current_mode = "achievements"
                else:
                    # Texte libre -> contexte ou suite de phrase
                    if current_mode == "context":
                        context_acc.append(line)
                    # Si on est en mode liste et qu'on tombe sur du texte normal, c'est soit un wrap, soit un retour au contexte
                    # Ici on simplifie : on considère que ça continue la section courante ou context si vide
            
            entry.context = self.normalize_paragraph(context_acc)
            entries.append(entry)
            
        return entries

    def classify_and_parse(self) -> Dict[str, Any]:
        sections = {
            "experience": [], "education": [], "summary": [], 
            "skills": [], "languages": [], "achievements": [], "extra": [], "unmapped": []
        }
        
        # Mots-clés de section
        map_keys = {
            "experience": ["expérience", "experience", "mandats", "parcours"],
            "education": ["formation", "education", "diplômes"],
            "skills": ["compétences", "skills", "expertises"],
            "summary": ["résumé", "summary", "profil", "objectif"],
            "languages": ["langues"],
            "achievements": ["réalisations", "projets"],
            "extra": ["intérêts", "hobbies", "certifications"]
        }
        
        current_section = "unmapped"
        
        for line in self.lines:
            line_lower = line.lower()
            # Détection Header
            if len(line) < 60:
                for key, val in map_keys.items():
                    if any(v in line_lower for v in val) and (line.isupper() or len(line.split()) < 5):
                        current_section = key
                        break
            
            sections[current_section].append(line)

        # Parsing final
        parsed_exp = self.segment_experiences(sections["experience"])
        # Tri chronologique inversé (Heuristique simple sur l'année si présente dans date_start)
        # TODO: Parsing date réel pour tri parfait. Ici on fait confiance à l'ordre du CV (souvent antichronologique)
        
        # Clean Summary
        clean_summary = self.normalize_paragraph(sections["summary"])
        
        return {
            "experience": parsed_exp,
            "summary": clean_summary,
            "raw_sections": sections
        }

def parse_cv(file_path: str) -> Optional[dict]:
    filename = os.path.basename(file_path)
    _, extension = os.path.splitext(filename)
    
    text, ocr_applied = "", False
    if extension.lower() == ".pdf":
        text, ocr_applied = extract_text_from_pdf(file_path)
    elif extension.lower() == ".docx":
        text = extract_text_from_docx(file_path)
    
    if not text.strip(): return None

    try:
        parser = UniversalParser(text)
        basics = parser.extract_basics()
        skills = parser.extract_skills()
        struct_data = parser.classify_and_parse()
        raw = struct_data["raw_sections"]
        
        cv_data = CVData(
            meta={"filename": filename, "ocr_applied": str(ocr_applied)},
            basics=basics,
            links=basics["links"],
            summary=struct_data["summary"],
            skills_tech=skills["tech"],
            skills_soft=skills["soft"],
            experience=struct_data["experience"],
            education=[EducationEntry(degree=l, full_text=l) for l in raw["education"] if len(l) > 3],
            languages=[l for l in raw["languages"] if len(l) > 3],
            achievements_global=raw["achievements"],
            extra_info=raw["extra"],
            unmapped=raw["unmapped"],
            raw_text=text
        )
        return cv_data.to_dict()

    except Exception as e:
        print(f"Erreur: {e}")
        return CVData(raw_text=text, meta={"error": str(e)}).to_dict()
