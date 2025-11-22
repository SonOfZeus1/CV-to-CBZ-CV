import logging
from typing import Any, Dict, List
from ai_client import call_ai

logger = logging.getLogger(__name__)

# --- PROMPTS ---

CONTACT_SYSTEM_PROMPT = """
Tu es un expert en extraction de données de CV. 
Ta mission est d'identifier le candidat et ses coordonnées avec une précision absolue.
"""

CONTACT_USER_PROMPT = """
Voici le début d'un CV (texte brut). Extrait uniquement les informations de contact et d'entête candidat.

Contraintes CRITIQUES :
1. Le "name" est le NOM PROPRE du candidat (ex: 'Jean Dupont').
   - Ce n'est JAMAIS un titre de section comme "COMPÉTENCES TECHNIQUES", "EXPERIENCE", "CURRICULUM VITAE".
   - Si tu trouves "COMPÉTENCES TECHNIQUES" en haut, CE N'EST PAS LE NOM. Cherche ailleurs.
2. Le "title" est le rôle professionnel (ex: 'Ingénieur Logiciel', 'Développeur Fullstack').
3. Ne rien inventer. Si une info est absente, laisse vide "".

Retourne un JSON strict :
{{
  "name": "Nom Prénom",
  "title": "Titre du poste",
  "email": "email@example.com",
  "phone": "+1 234...",
  "linkedin": "url ou handle",
  "location": "Ville, Pays",
  "languages": ["Français", "Anglais"]
}}

Texte à analyser :
\"\"\"{text}\"\"\"
"""

SEGMENTATION_SYSTEM_PROMPT = """
Tu es un expert en structure de documents. Tu dois segmenter un CV en blocs logiques.
"""

SEGMENTATION_USER_PROMPT = """
Voici le texte complet d'un CV. Tu dois le découper en sections distinctes.
Ne réécris pas le texte, copie-colle les blocs tels quels.

Sections attendues :
- "contact_block": Tout ce qui concerne l'entête, le nom, les coordonnées.
- "skills_block": La section des compétences techniques, langages, outils.
- "experience_blocks": Une liste de chaînes de caractères, où CHAQUE chaîne est UNE expérience professionnelle complète (Dates + Titre + Entreprise + Description).
- "education_block": La section formation / diplômes.
- "other_block": Tout le reste (certifications, intérêts, etc.).

Retourne un JSON strict :
{{
  "contact_block": "...",
  "skills_block": "...",
  "experience_blocks": ["Expérience 1...", "Expérience 2..."],
  "education_block": "...",
  "other_block": "..."
}}

Texte du CV :
\"\"\"{text}\"\"\"
"""

EXPERIENCE_SYSTEM_PROMPT = """
Tu es un expert en formatage de CV. Tu dois structurer une expérience professionnelle sans rien inventer.
"""

EXPERIENCE_USER_PROMPT = """
Voici le texte brut d'une seule expérience professionnelle. 
Tu dois produire un JSON structuré.

Contraintes :
- Reprendre EXACTEMENT les dates du CV (ne jamais modifier le format).
- Calculer la durée en années+mois si possible (ex: "2 ans 3 mois").
- Les "taches" doivent être extraites du texte original.
  - Reformule légèrement pour que ce soit propre (verbe d'action), mais n'invente RIEN.
  - Ne laisse jamais vide si le texte contient des descriptions.
- Les "competences" doivent venir exclusivement du texte de cette expérience (stack technique citée).

Retourne un JSON strict :
{{
  "titre_poste": "...",
  "entreprise": "...",
  "localisation": "...",
  "dates": "...",
  "duree": "...",
  "resume": "Court résumé si présent",
  "taches": ["Tâche 1", "Tâche 2"],
  "competences": ["Java", "Python"]
}}

Bloc expérience :
\"\"\"{text}\"\"\"
"""

EDUCATION_SYSTEM_PROMPT = """
Tu es un expert en formatage de CV. Tu dois structurer la section formation.
"""

EDUCATION_USER_PROMPT = """
Voici le texte brut de la section formation / éducation.
Tu dois en extraire une liste de diplômes.

Retourne un JSON strict :
{{
  "education": [
    {{
      "diplome": "Titre du diplôme",
      "etablissement": "Nom de l'école/université",
      "annee": "Année ou période",
      "localisation": "Ville, Pays"
    }}
  ]
}}

Section formation :
\"\"\"{text}\"\"\"
"""

# --- FUNCTIONS ---

def ai_parse_contact(text_head: str) -> Dict[str, Any]:
    """Extracts contact info from the first part of the CV."""
    prompt = CONTACT_USER_PROMPT.format(text=text_head[:3000])
    return call_ai(prompt, CONTACT_SYSTEM_PROMPT, expect_json=True)

def ai_parse_segmentation(full_text: str) -> Dict[str, Any]:
    """Segments the full CV text into logical blocks."""
    prompt = SEGMENTATION_USER_PROMPT.format(text=full_text[:15000])
    return call_ai(prompt, SEGMENTATION_SYSTEM_PROMPT, expect_json=True)

def ai_parse_experience_block(block_text: str) -> Dict[str, Any]:
    """Formats a single experience block."""
    prompt = EXPERIENCE_USER_PROMPT.format(text=block_text)
    return call_ai(prompt, EXPERIENCE_SYSTEM_PROMPT, expect_json=True)

def ai_parse_education(block_text: str) -> Dict[str, Any]:
    """Formats the education section."""
    if not block_text.strip():
        return {"education": []}
    prompt = EDUCATION_USER_PROMPT.format(text=block_text)
    return call_ai(prompt, EDUCATION_SYSTEM_PROMPT, expect_json=True)
