import logging
from typing import Any, Dict, List
from ai_client import call_ai

logger = logging.getLogger(__name__)

# --- PROMPTS ---

CONTACT_SYSTEM_PROMPT = """
Tu es un expert en extraction de données de CV.
Ta mission est d'identifier le candidat et ses coordonnées avec une précision absolue, pour des CV de tous formats (français, anglais, multi-pages, colonnes, PDF scannés ou non).

Règles générales :
1. Tu ne dois JAMAIS inventer d'information. Si une donnée est absente ou illisible, tu renvoies une chaîne vide "".
2. Tu dois être robuste aux variations de mise en page, majuscules, accents, et petites fautes.
3. Tu dois ignorer les titres de sections (COMPÉTENCES, EXPERIENCE, SKILLS, EDUCATION, etc.) comme potentiels noms.
"""

CONTACT_USER_PROMPT = """
Voici le début d'un CV (texte brut). Extrait uniquement les informations de contact et d'entête candidat.

Contraintes CRITIQUES :
1. Le "name" est le NOM PROPRE du candidat (ex: "Jean Dupont").
   - Ce n'est JAMAIS un titre de section comme "COMPÉTENCES TECHNIQUES", "EXPERIENCE", "CURRICULUM VITAE".
   - Si tu trouves "COMPÉTENCES TECHNIQUES" ou "EXPERIENCE" en haut, CE N'EST PAS LE NOM. Cherche ailleurs.
2. Le "title" est le rôle professionnel (ex: "Ingénieur Logiciel", "Développeur Fullstack").
3. L'"email" peut être présent sous forme explicite (ex: "nom@domaine.com") ou implicite (dans un lien mailto).
4. Le "linkedin" peut être une URL complète (https://linkedin.com/in/...) ou un handle.
5. "location" est typiquement de la forme "Ville, Pays" ou "Ville, Province, Pays".
6. "languages" doit contenir les langues que le candidat affirme parler (Français, Anglais, etc.).
7. Ne rien inventer. Si une info est absente, laisse vide "" ou [] selon le type attendu.

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
Tu es un expert en structure de documents et en parsing de CV.
Ton rôle est de découper un CV en sections logiques avec une précision maximale, pour des CV de toutes origines (FR, EN, autres), de tous formats (une ou plusieurs pages, colonnes, PDF générés ou OCR, etc.).

COMPORTEMENT GÉNÉRAL (OBLIGATOIRE) :
1. Tu NE MODIFIES JAMAIS le texte d'origine : tu ne reformules pas, tu ne traduis pas, tu ne résumes pas.
2. Tu NE DÉPLACES PAS du texte d'une section à une autre : chaque caractère reste dans l'ordre d'origine.
3. Tu NE CRÉES PAS de texte nouveau : tu te contentes de copier des sous-parties du texte fourni.
4. Tu peux supprimer les lignes complètement vides ou réduire plusieurs lignes vides consécutives à une seule, mais jamais retirer des lignes contenant du texte.

OBJECTIF :
Découper le texte en blocs logiques pour faciliter un parsing ultérieur :
- un bloc entête / contact,
- un bloc compétences,
- une liste de blocs d'expérience (une expérience par bloc),
- un bloc éducation / formation,
- un bloc "autre".

Tu dois être ROBUSTE à :
- différents noms de sections (français / anglais) : "EXPÉRIENCE", "EXPERIENCE", "WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE", "COMPÉTENCES", "SKILLS", "ÉDUCATION", "EDUCATION", "FORMATION", etc.
- différents formats de dates :
  * "Mai 2017 - Novembre 2017"
  * "Décembre 2019-Avril 2020"
  * "2017 - 2020"
  * "2019"
  * "Septembre 2021-Aujourd'hui"
- CV où les sections ne sont pas dans l'ordre classique.

RÈGLES POUR CHAQUE SECTION :

1) contact_block
- Contient le nom du candidat, le titre professionnel, les coordonnées (email, téléphone, LinkedIn, adresse).
- Contient toutes les lignes d'en-tête AVANT la première grande section claire ("COMPÉTENCES", "SKILLS", "EXPERIENCE", "FORMATION", etc.).
- Ne doit PAS inclure la section "COMPÉTENCES", "SKILLS", "EXPÉRIENCE", "WORK EXPERIENCE", "EDUCATION", etc.

2) skills_block
- Contient les listes de compétences techniques, outils, langages de programmation, logiciels, etc.
- Typiquement sous des titres comme : "COMPÉTENCES", "COMPÉTENCES TECHNIQUES", "SKILLS", "TECHNICAL SKILLS", "TECHNOLOGIES".
- Ne doit pas contenir de descriptions d'expériences (dates, entreprises, responsabilités).

3) experience_blocks
- C'est une LISTE de chaînes. Chaque élément de la liste doit être une expérience professionnelle complète.
- Une "expérience professionnelle" inclut :
  * le titre du poste,
  * le nom de l'entreprise,
  * la localisation éventuelle,
  * les dates (période),
  * toutes les lignes de description associées (tâches, réalisations, environnement technologique, etc.).
- Une expérience commence généralement par une combinaison de :
  * titre de poste (ex: "Développeur Java", "Software Engineer", "Analyste Programmeur"),
  * et/ou nom d'entreprise,
  * suivie de dates.
- Une expérience se termine juste avant la prochaine entête de poste/date ou la fin de la section expérience.
- NE JAMAIS fusionner deux expériences dans le même bloc : mieux vaut couper trop tard que trop tôt.
- Inclure les bullet points ("•", "-", etc.) à l'intérieur du bloc d'expérience correspondant.

4) education_block
- Contient la partie "ÉDUCATION", "FORMATION", "EDUCATION", "ACADEMIC BACKGROUND", etc.
- Inclut les diplômes, écoles/universités, années, lieux, éventuellement une courte description.
- Copie exacte du texte de cette section.

5) other_block
- Contient tout ce qui ne va pas clairement dans les autres sections : certifications, intérêts, hobbies, langues si elles ne sont pas ailleurs, références, etc.
- Il peut être vide si le CV ne contient rien de plus.

Si une section est absente du CV, renvoie une chaîne vide pour cette section (ou une liste vide pour "experience_blocks").
"""

SEGMENTATION_USER_PROMPT = """
Voici le texte complet d'un CV. Tu dois le découper en sections distinctes, en respectant strictement les règles suivantes :

RAPPEL DES SECTIONS ATTENDUES :
- "contact_block": tout ce qui concerne l'entête, le nom, les coordonnées (haut de CV).
- "skills_block": la section des compétences techniques, langages, outils, stacks, logiciels.
- "experience_blocks": une liste de chaînes, où CHAQUE chaîne est UNE expérience professionnelle complète (titre + entreprise + éventuellement localisation + dates + description).
- "education_block": la section formation / diplômes / éducation.
- "other_block": tout le reste (certifications, centres d'intérêt, langues si ailleurs, références, etc.).

CONTRAINTES :
1. Tu NE RÉÉCRIS PAS le texte, tu ne le reformules pas.
2. Tu COPIES-COLLES les blocs tels quels, en conservant l'ordre original.
3. Tu ne dois JAMAIS inventer de texte.
4. "experience_blocks" doit contenir une chaîne distincte par expérience, pas toute la section expérience dans une seule chaîne.
5. Si tu es incertain, préfère inclure un peu plus de texte dans un bloc expérience plutôt que d'en fusionner deux.

FORMAT DE SORTIE :
Retourne un JSON strict :
{{
  "contact_block": "Texte brut de l'entête...",
  "skills_block": "Texte brut des compétences...",
  "experience_blocks": [
    "Texte complet de l'expérience 1...",
    "Texte complet de l'expérience 2..."
  ],
  "education_block": "Texte brut de la formation...",
  "other_block": "Texte brut des autres informations..."
}}

Texte du CV :
\"\"\"{text}\"\"\"
"""

EXPERIENCE_SLOT_FILLING_SYSTEM_PROMPT = """
Tu es un assistant spécialisé dans l'extraction de contenu de CV.
On te fournit un bloc de texte qui correspond à UNE expérience professionnelle, ainsi que les dates DÉJÀ VALIDÉES par le système.

TA MISSION :
Remplir les champs sémantiques (titre, entreprise, lieu, tâches, compétences) à partir du texte.

RÈGLES CRITIQUES :
1. NE TOUCHE PAS AUX DATES. Elles te sont fournies en entrée, tu dois les ignorer ou les remettre telles quelles si demandé, mais NE JAMAIS les inventer ou les modifier.
2. Si le titre ou l'entreprise ne sont pas clairs, cherche les indices visuels (MAJUSCULES, gras, début de ligne).
3. "tasks" doit être une liste de chaînes (bullet points).
4. "skills" doit contenir les technologies/outils mentionnés DANS CE BLOC UNIQUEMENT.
5. Ne rien inventer. Si absent -> "".

FORMAT DE SORTIE (JSON) :
{
  "job_title": "...",
  "company": "...",
  "location": "...",
  "summary": "...",
  "tasks": ["...", "..."],
  "skills": ["...", "..."]
}
"""

EXPERIENCE_SLOT_FILLING_USER_PROMPT = """
Voici le texte de l'expérience :
\"\"\"{text}\"\"\"

Dates détectées (Contexte) : {date_context}

Extrait les informations sémantiques.
"""

EDUCATION_SYSTEM_PROMPT = """
Tu es un expert en formatage de CV. Tu dois structurer la section formation / éducation.
Tu dois être robuste aux différentes langues (FR/EN), aux dates variées, et aux mises en forme non standards.
"""

EDUCATION_USER_PROMPT = """
Voici le texte brut de la section formation / éducation.
Tu dois en extraire une liste de diplômes.

Règles :
- Un "diplome" correspond à un titre académique ou professionnel (ex: "Baccalauréat en Ingénierie Logiciel", "Master en Informatique", "Licence en Mathématiques").
- "etablissement" est le nom de l'école ou de l'université.
- "annee" est l'année ou la période telle qu'écrite dans le CV (ne pas modifier le format).
- "localisation" est la ville, province et/ou pays si présents.
- Ne rien inventer : si un champ est absent, renvoie "" pour ce champ.

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

def ai_parse_experience_slot(block_text: str, date_context: str, start_is_year_only: bool = False, end_is_year_only: bool = False) -> Dict[str, Any]:
    """Fills content slots for a pre-segmented experience block."""
    # Append instruction about year-only dates if needed
    extra_instruction = ""
    if start_is_year_only or end_is_year_only:
        extra_instruction = "\nNOTE: Dates provided are Year-Only. Do NOT invent months."
        
    prompt = EXPERIENCE_SLOT_FILLING_USER_PROMPT.format(text=block_text, date_context=date_context + extra_instruction)
    return call_ai(prompt, EXPERIENCE_SLOT_FILLING_SYSTEM_PROMPT, expect_json=True)

def ai_parse_education(block_text: str) -> Dict[str, Any]:
    """Formats the education section."""
    if not block_text.strip():
        return {"education": []}
    prompt = EDUCATION_USER_PROMPT.format(text=block_text)
    return call_ai(prompt, EDUCATION_SYSTEM_PROMPT, expect_json=True)

SUMMARY_SYSTEM_PROMPT = """
You are an expert career consultant. Your goal is to write a single, powerful summary sentence for a CV.
"""

SUMMARY_USER_PROMPT = """
Based on the following experience list, generate a single summary sentence following this EXACT template:

"[Role] [seniority] comptant plus de [X] années d’expérience en [Main Tech/Field], ayant travaillé pour des organisations d’envergure telles que [Company1], [Company2] et [Company3]."

Rules:
1. [Role]: Extract the most common or current role (e.g., Ingénieur logiciel, Développeur Java).
2. [seniority]: Add "senior" if > 5 years, "intermédiaire" if > 2 years, else remove.
3. [X]: Calculate total years of experience from the dates provided.
4. [Main Tech/Field]: The primary technology or field (e.g., développement Java, architecture Cloud).
5. [CompanyList]: List 3-5 most significant/recognizable companies from the list.
6. Output MUST be a JSON object with a single key "generated_summary".

Experiences:
{experiences_text}
"""

def ai_generate_summary(experiences: List[Dict[str, Any]]) -> Dict[str, str]:
    """Generates a dynamic summary based on extracted experiences."""
    if not experiences:
        return {"generated_summary": ""}
        
    # Format experiences for the prompt
    exp_text = ""
    for exp in experiences:
        exp_text += f"- {exp.get('job_title')} at {exp.get('company')} ({exp.get('dates')})\n"
        
    prompt = SUMMARY_USER_PROMPT.format(experiences_text=exp_text)
    return call_ai(prompt, SUMMARY_SYSTEM_PROMPT, expect_json=True)
