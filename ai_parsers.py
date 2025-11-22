import logging
from typing import Any, Dict, List

from ai_client import AIClientUnavailable, get_ai_client

logger = logging.getLogger(__name__)

EXPERIENCE_SYSTEM_PROMPT = (
    "Tu es un expert en analyse de CV chargé de structurer des expériences "
    "professionnelles sans jamais inventer d'information et en respectant "
    "strictement les données fournies."
)

EXPERIENCE_USER_PROMPT = """
Tu reçois un bloc brut issu d'un CV. Tu dois en extraire UNIQUEMENT les informations présentes.

Contraintes absolues :
1. Ne reformate pas les dates : reprends exactement le texte des dates (ex: "Septembre 2021-Aujourd’hui").
2. Ne crée jamais d'expérience ni d'information qui n'existe pas.
3. Si un champ manque dans le texte, laisse-le vide (""), sauf les listes qui doivent être vides ([]).
4. Les "taches" DOIVENT être extraites. Cherche toutes les phrases d'action (bullet points ou phrases) décrivant le travail.
   - Ne résume pas, extrait les points clés.
   - Si aucune tâche n'est explicite, cherche des phrases décrivant ce que la personne a fait.
5. Les "competences" doivent uniquement contenir des technologies/outils/méthodes mentionnés EXPLICITEMENT dans le bloc (ex: Java, AWS, Git).
   - Ne devine pas les compétences.

Retourne un JSON strict avec les clés suivantes :
{
  "titre_poste": "Le titre exact du poste",
  "entreprise": "Le nom de l'entreprise",
  "localisation": "Ville, PAYS (ex: Montréal, CANADA)",
  "dates": "Les dates exactes telles qu'écrites",
  "duree": "Calcul approximatif si possible (ex: 4 ans 2 mois), sinon vide",
  "resume": "Court résumé si présent, sinon vide",
  "taches": ["Tâche 1", "Tâche 2", ...],
  "competences": ["Java", "Python", ...]
}

Bloc à analyser :
\"\"\"{experience_block}\"\"\"
"""


REQUIRED_STRING_FIELDS = ("titre_poste", "entreprise", "dates")
REQUIRED_LIST_FIELDS = ("taches", "competences")


def _sanitize_list(values: Any, max_items: int = 8) -> List[str]:
    if isinstance(values, list):
        cleaned = [str(item).strip() for item in values if str(item).strip()]
    elif isinstance(values, str):
        cleaned = [values.strip()] if values.strip() else []
    else:
        cleaned = []
    deduped = []
    for item in cleaned:
        if item not in deduped:
            deduped.append(item)
        if len(deduped) >= max_items:
            break
    return deduped


def _validate_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    sanitized: Dict[str, Any] = {}
    for field in REQUIRED_STRING_FIELDS:
        value = str(payload.get(field, "")).strip()
        sanitized[field] = value
    for field in REQUIRED_LIST_FIELDS:
        sanitized[field] = _sanitize_list(payload.get(field, []))
    sanitized["localisation"] = str(payload.get("localisation", "")).strip()
    sanitized["duree"] = str(payload.get("duree", "")).strip()
    sanitized["resume"] = str(payload.get("resume", "")).strip()
    return sanitized


def ai_parse_experience_block(block_text: str) -> Dict[str, Any]:
    """
    Calls Groq to structure a single experience block. Returns an empty dict if
    the AI client is unavailable or the response is invalid.
    """
    if not block_text.strip():
        return {}
    try:
        client = get_ai_client()
    except AIClientUnavailable as exc:
        logger.info("AI client unavailable, fallback to rule-based parsing: %s", exc)
        return {}

    messages = [
        {"role": "system", "content": EXPERIENCE_SYSTEM_PROMPT},
        {"role": "user", "content": EXPERIENCE_USER_PROMPT.format(experience_block=block_text.strip())},
    ]

    try:
        raw_payload = client.structured_completion(messages)
    except Exception as exc:
        logger.warning("AI parsing failed, will fallback to rule-based logic: %s", exc)
        return {}

    processed = _validate_payload(raw_payload)

    # Validation : ensure mandatory fields exist
    if any(not processed.get(field) for field in REQUIRED_STRING_FIELDS):
        logger.warning("AI payload missing mandatory fields, ignoring result: %s", processed)
        return {}
    if any(not processed.get(field) for field in REQUIRED_LIST_FIELDS):
        logger.warning("AI payload missing tasks/skills, ignoring result.")
        return {}

    return processed


CONTACT_SYSTEM_PROMPT = (
    "Tu es un expert en extraction de données de CV. Ta mission est d'identifier "
    "le candidat et ses coordonnées avec une précision absolue."
)

CONTACT_USER_PROMPT = """
Voici le début d'un CV (texte brut). Extrait uniquement les informations de contact et d'entête candidat.

Contraintes CRITIQUES :
1. Le "name" est le NOM PROPRE du candidat (ex: 'Jean Dupont').
   - Ce n'est JAMAIS un titre de section comme "COMPÉTENCES TECHNIQUES", "EXPERIENCE", "CURRICULUM VITAE".
   - Si tu trouves "COMPÉTENCES TECHNIQUES" en haut, CE N'EST PAS LE NOM. Cherche ailleurs.
2. Le "title" est le rôle professionnel (ex: 'Ingénieur Logiciel', 'Développeur Fullstack').
3. Ne rien inventer. Si une info est absente, laisse vide "".

Retourne un JSON strict :
{
  "name": "Nom Prénom",
  "title": "Titre du poste",
  "email": "email@example.com",
  "phone": "+1 234...",
  "linkedin": "url ou handle",
  "location": "Ville, Pays",
  "languages": ["Français", "Anglais"]
}

Texte à analyser :
\"\"\"{text_head}\"\"\"
"""

def ai_parse_contact(text_head: str) -> Dict[str, Any]:
    """
    Uses AI to extract contact info from the first ~2000 chars of the CV.
    """
    if not text_head.strip():
        return {}
    
    try:
        client = get_ai_client()
    except AIClientUnavailable:
        return {}

    messages = [
        {"role": "system", "content": CONTACT_SYSTEM_PROMPT},
        {"role": "user", "content": CONTACT_USER_PROMPT.format(text_head=text_head[:2500])},
    ]

    try:
        return client.structured_completion(messages)
    except Exception as exc:
        logger.warning("AI contact parsing failed: %s", exc)
        return {}


