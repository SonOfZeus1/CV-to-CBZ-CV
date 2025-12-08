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

EXPERIENCE_SYSTEM_PROMPT = """
Tu es un assistant spécialisé dans l’extraction structurée de CV vers du JSON propre, cohérent et sans doublons.
On te fournit le texte brut complet d’un CV. Tu dois en extraire en priorité la section expériences professionnelles dans un tableau experience[] avec le schéma suivant :

{
  "job_title": "",
  "company": "",
  "location": "",
  "dates": "",        // texte lisible, exactement comme dans le CV (ex: "Octobre 2018 — Août 2020")
  "date_start": "",   // optionnel, ISO "YYYY-MM" si déductible
  "date_end": "",     // optionnel, ISO "YYYY-MM" ou "" / null si "Aujourd'hui" / "Maintenant"
  "is_current": false,
  "duration": "",     // laisse vide si tu n'es pas CERTAIN. La durée sera recalculée par le code.
  "summary": "",
  "tasks": [],
  "skills": [],
  "full_text": ""
}

1. Règles générales

Respecte strictement les informations du CV.

Si une information n’est pas clairement présente, ne l’invente pas. Laisse le champ vide ou omets-le.

Reconstitue chaque expérience à partir d’un bloc cohérent : titre + entreprise + lieu + dates + description/bullets.

2. Gestion des dates (cause d’erreur majeure à éviter)

Ne remplace JAMAIS une date de fin explicite par “Aujourd’hui” ou “Maintenant”.

Si le CV indique Octobre 2018 — Août 2020, tu dois garder exactement cette plage dans dates.

date_end doit être dérivée de la vraie date de fin (2020-08), pas de la date actuelle.

“Aujourd’hui”, “Maintenant”, “Présent”, “Present” ne s’appliquent que si le CV l’écrit explicitement pour ce poste.

Ne propage pas ce statut aux autres expériences plus anciennes.

Ne réutilise pas la date d’un poste précédent sur un autre poste.

Chaque bloc de dates appartient au poste auquel il est visuellement associé dans le CV.

date_start / date_end

Si le mois et l’année sont présents, convertis en YYYY-MM (ex: Octobre 2018 → "2018-10").

Si seule l’année est donnée, tu peux mettre YYYY-01 ou laisser vide si tu n’es pas sûr.

3. Durée des expériences (nouvelle stratégie)

Ne fais PLUS de “best guess” flou.

Si tu peux calculer la durée de façon arithmétique et cohérente à partir de date_start / date_end, tu peux renseigner duration (ex: "3 ans 2 mois").

Si tu n’es pas sûr à 100 % de la durée, laisse duration vide ("").

Il vaut mieux une durée absente qu’une durée fausse (le code en aval recalculera la durée précisément à partir de date_start / date_end et de la date actuelle fournie).

4. Éviter les doublons (autre cause d’erreur majeure)

Ne duplique pas la même expérience plusieurs fois.

Deux expériences sont considérées identiques si tous les éléments suivants correspondent fortement :

job_title

company

location

dates

le texte principal dans full_text

Si tu retrouves la même expérience dans plusieurs parties du texte (par exemple à cause de répétitions ou de chunks), ne l’ajoute qu’une seule fois dans le tableau experience.

5. Résumé / profil

Si tu génères un résumé global (summary), ne réécris pas l’historique de manière trompeuse.

Ne transforme pas des années d’expérience générales en “années d’Angular” ou d’une techno spécifique, sauf si le CV le dit explicitement.

Exemple d’erreur à éviter (tiré d’un CV réel) :

CV : “Expérience de 10 ans comme analyste développeur, près de 2 ans comme architecte.”

Mauvais résumé généré : “plus de 14 années d’expérience en développement Angular” (faux).

6. Exemple d’erreurs à NE PAS reproduire (cas réel "Marc Plante")

Ne pas transformer :

Août 2020 – Octobre 2023 en Août 2020 – Aujourd’hui avec une durée de 5 ans 3 mois.

Octobre 2018 – Août 2020 en Octobre 2018 – Aujourd’hui avec 7 ans 1 mois.

Avril 2016 – Septembre 2017 en Avril 2016 – Aujourd’hui avec 9 ans 7 mois.

Janvier 2013 – Avril 2016 en Janvier 2013 – Aujourd’hui avec 12 ans 10 mois.

Octobre 2009 – Janvier 2013 en Octobre 2009 – Aujourd’hui avec 16 ans 1 mois.

Ne pas répéter 3 fois la même séquence d’expériences (Angular / Architecte APEX / Analyste APEX / SharePoint / Scrum Master / Analyste télécom / Ingénieur logiciel/analyste).

7. Sortie attendue

Retourne un objet JSON valide avec au minimum :

experience: tableau d’expériences propres, dédupliquées et alignées sur le texte.

Tu peux ajouter d’autres sections (éducation, langues, etc.) si elles sont simples à extraire, mais la priorité est la qualité de experience[].
"""

EXPERIENCE_USER_PROMPT = """
Voici le texte brut d'une expérience professionnelle (ou d'un bloc d'expériences).
Analyse-le et extrait les informations selon les règles strictes définies ci-dessus.

Texte à analyser :
\"\"\"{text}\"\"\"
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
