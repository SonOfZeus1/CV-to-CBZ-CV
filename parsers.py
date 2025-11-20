import re
import os
import io
import fitz  # PyMuPDF
import docx
import pytesseract
from PIL import Image
import spacy
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

# --- SCHÉMA DE DONNÉES ---

@dataclass
class CVData:
    """Structure standardisée pour les données extraites d'un CV."""
    meta: Dict[str, str] = field(default_factory=dict)
    basics: Dict[str, str] = field(default_factory=lambda: {
        "name": "", "email": "", "phone": "", "location": ""
    })
    skills: List[str] = field(default_factory=list)
    experience: List[str] = field(default_factory=list)
    education: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self):
        return asdict(self)

# --- CHARGEMENT MODÈLE NLP ---

NLP = None

def load_spacy_model():
    global NLP
    if NLP is None:
        try:
            # Essai de chargement du modèle (doit être installé via requirements ou workflow)
            NLP = spacy.load("en_core_web_sm")
        except OSError:
            print("Modèle Spacy non trouvé. Téléchargement en cours...")
            from spacy.cli import download
            download("en_core_web_sm")
            NLP = spacy.load("en_core_web_sm")
    return NLP

# --- OUTILS D'EXTRACTION BAS NIVEAU ---

def extract_text_from_docx(file_path: str) -> str:
    try:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        print(f"Erreur lecture DOCX {file_path}: {e}")
        return ""

def extract_text_from_pdf(file_path: str) -> tuple[str, bool]:
    """
    Extrait le texte d'un PDF. 
    Retourne (texte, ocr_applied).
    Applique l'OCR si la densité de texte est trop faible.
    """
    text = ""
    ocr_applied = False
    
    try:
        with fitz.open(file_path) as doc:
            # 1. Extraction texte native
            for page in doc:
                text += page.get_text()
            
            # 2. Vérification de la densité (OCR nécessaire ?)
            # Heuristique : Si < 50 caractères par page en moyenne, c'est probablement un scan.
            avg_chars_per_page = len(text.strip()) / len(doc) if len(doc) > 0 else 0
            
            if avg_chars_per_page < 50:
                print(f"PDF Image détecté ({avg_chars_per_page:.1f} chars/page). Démarrage OCR pour {os.path.basename(file_path)}...")
                text = "" # Reset pour remplacer par l'OCR
                ocr_applied = True
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=150) # 150 DPI suffit souvent et va plus vite
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    # OCR via Tesseract
                    text += pytesseract.image_to_string(image) + "\n"
                    
    except Exception as e:
        print(f"Erreur extraction PDF {file_path}: {e}")
    
    return text, ocr_applied

# --- LOGIQUE D'ANALYSE (REMPLACEMENT DE PYRESPARSER) ---

class SimpleResumeParser:
    def __init__(self, text: str):
        self.text = text
        self.nlp = load_spacy_model()
        self.doc = self.nlp(text)
        
    def extract_emails(self) -> List[str]:
        # Regex standard pour email
        regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        return list(set(re.findall(regex, self.text)))

    def extract_phone(self) -> List[str]:
        # Regex permissive pour téléphones (français et internationaux)
        # Ex: 06 12 34 56 78, +33 6..., 06.12...
        regex = r'(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?(\d{2,4}[-.\s]?){2,4}'
        matches = re.finditer(regex, self.text)
        phones = [m.group(0).strip() for m in matches if len(m.group(0).strip()) >= 10]
        return list(set(phones))
        
    def extract_links(self) -> List[str]:
        regex = r'(https?://\S+|www\.\S+|linkedin\.com/in/\S+|github\.com/\S+)'
        return list(set(re.findall(regex, self.text, re.IGNORECASE)))

    def extract_name(self) -> str:
        # Stratégie : Le nom est souvent au début, et détecté comme PERSON par Spacy
        for ent in self.doc.ents[:10]: # On regarde seulement le début du doc
            if ent.label_ == "PERSON" and len(ent.text.split()) >= 2:
                return ent.text.strip()
        return ""

    def extract_skills(self) -> List[str]:
        # Stratégie simple : Extraction des Noun Chunks qui ressemblent à des compétences
        # Idéalement, on croiserait avec une liste de skills, mais sans DB externe, 
        # on extrait les termes techniques (souvent en majuscules ou noms propres dans le contexte IT)
        skills = []
        # Liste de mots clés techniques "hardcodés" pour l'exemple (extensible)
        common_tech_skills = {"python", "java", "c++", "sql", "javascript", "react", "docker", "aws", "linux", "git", "html", "css", "office", "management"}
        
        tokens = [token.text.lower() for token in self.doc if not token.is_stop and not token.is_punct]
        
        # 1. Match exact avec la liste
        for token in tokens:
            if token in common_tech_skills:
                skills.append(token.capitalize())
                
        # 2. ORG entities qui pourraient être des technologies
        for ent in self.doc.ents:
            if ent.label_ == "ORG" or ent.label_ == "PRODUCT":
                skills.append(ent.text)
                
        return list(set(skills))[:15] # Limite à 15 pour ne pas polluer

    def segment_sections(self) -> Dict[str, List[str]]:
        """Découpe le texte en sections basées sur des mots-clés."""
        sections = {"experience": [], "education": []}
        lines = self.text.split('\n')
        
        current_section = None
        buffer = []
        
        # Mots-clés déclencheurs (en anglais et français)
        keywords = {
            "experience": ["experience", "employment", "work history", "expérience", "professionnelle"],
            "education": ["education", "formation", "diplômes", "academic", "scolarité"]
        }
        
        for line in lines:
            line_clean = line.strip().lower()
            
            # Détection de changement de section
            is_header = False
            for key, words in keywords.items():
                if any(w in line_clean for w in words) and len(line_clean) < 50:
                    # C'est probablement un titre
                    current_section = key
                    is_header = True
                    break
            
            if is_header:
                continue
                
            if current_section and line.strip():
                # Nettoyage basique : on garde les lignes qui ressemblent à du contenu
                if len(line.strip()) > 3:
                    sections[current_section].append(line.strip())

        return sections

# --- FONCTION PRINCIPALE ---

def parse_cv(file_path: str) -> Optional[dict]:
    """
    Analyse un fichier de CV et retourne un dictionnaire structuré (CVData).
    """
    filename = os.path.basename(file_path)
    _, extension = os.path.splitext(filename)
    
    text = ""
    ocr_applied = False

    # 1. Extraction du texte
    if extension.lower() == ".pdf":
        text, ocr_applied = extract_text_from_pdf(file_path)
    elif extension.lower() == ".docx":
        text = extract_text_from_docx(file_path)
    else:
        print(f"Format non supporté : {extension}")
        return None

    if not text.strip():
        print(f"Échec extraction texte pour {filename}")
        return None

    # 2. Analyse sémantique
    try:
        parser = SimpleResumeParser(text)
        
        emails = parser.extract_emails()
        phones = parser.extract_phone()
        links = parser.extract_links()
        name = parser.extract_name()
        skills = parser.extract_skills()
        sections = parser.segment_sections()
        
        # 3. Construction de l'objet de données
        cv_data = CVData(
            raw_text=text,
            meta={
                "filename": filename,
                "ocr_applied": str(ocr_applied),
                "parser": "custom_hybrid_v1"
            },
            basics={
                "name": name if name else "Nom non détecté",
                "email": emails[0] if emails else "",
                "phone": phones[0] if phones else "",
                "location": "" 
            },
            links=links,
            skills=skills,
            experience=sections["experience"],
            education=sections["education"]
        )
        
        return cv_data.to_dict()

    except Exception as e:
        print(f"Erreur critique parsing {filename}: {e}")
        # Fallback ultime : on renvoie au moins le texte
        return CVData(raw_text=text, meta={"error": str(e)}).to_dict()
