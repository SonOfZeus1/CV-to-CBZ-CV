# ANALYSE TECHNIQUE (Architecture V4 - AI Stable Experiences)

Ce document décrit la nouvelle architecture orientée "AI-first mais robuste" du pipeline de traitement de CV.

## 1. Architecture Globale

| Étape | Description |
|-------|-------------|
| **Extract** | Téléchargement des CVs depuis Google Drive (API v3 + WIF) avec prise en charge des Shared Drives. |
| **Transform** | Parsing hybride : OCR conditionnel, segmentation regex, appels Groq pour structurer chaque expérience, validation + fallback. |
| **Load** | Génération JSON + PDF, puis upload dans le même dossier source. |

L'exécution se fait sur GitHub Actions (Ubuntu, Python 3.11, Tesseract installé à la volée). Les secrets Drive et Groq sont injectés au runtime via OIDC + GitHub Secrets.

## 2. Chaîne de Parsing

### 2.1 Extraction de texte
- `PyMuPDF` lit le texte natif.
- Si la densité < 50 caractères/page, activation OCR (`pytesseract`) page par page.
- Les documents `.docx` sont lus via `python-docx`.

### 2.2 Segmentation
- `UniversalParser` nettoie le texte (suppression des entêtes/pieds de page).
- Les sections (`Expérience`, `Formation`, etc.) sont détectées par mots-clés.
- À l'intérieur du bloc expérience, une regex `DATE_RANGE_REGEX` identifie les séparateurs (dates sur la ligne). Chaque bloc contient :
  - texte brut,
  - extrait de dates exact,
  - indice de localisation (extraction heuristique),
  - lignes d'origine.

### 2.3 Structuration IA + Fallback
1. **IA (Groq)**
   - `ai_client.py` : wrapper Groq (modèle `llama-3.1-70b-versatile` par défaut), retries exponentiels, sortie forcée JSON.
   - `ai_parsers.py` : prompt strict (pas d'invention, format JSON imposé).
   - `ai_parse_experience_block` renvoie `titre_poste`, `entreprise`, `localisation`, `dates`, `duree`, `resume`, `taches`, `competences`.

2. **Validation**
   - Champs obligatoires : titre, entreprise, dates, au moins 1 tâche + 1 compétence tech.
   - Si un champ est manquant → résultat ignoré.

3. **Fallback Rule-based**
   - `_rule_based_entry` exploite les lignes d'origine : découpe du header (`Titre – Entreprise`), extraction location, bullets `-/*/•`, dictionnaire de technos.
   - Durée calculée par `compute_duration_label` (via `dateparser`).

4. **Sortie unifiée**
   - `ExperienceEntry` dataclass: `job_title`, `company`, `location`, `dates`, `duration`, `summary`, `tasks`, `skills`, `full_text`.
   - Toujours au moins un objet par bloc d'expérience.

### 2.4 Normalisation
- `formatters.py` génère exactement le rendu demandé :

```
[Titre] – [Entreprise], [Localisation]
[Dates] ([Durée])

Résumé
...

Tâches principales
- ...

Compétences liées à cette expérience
Java, Spring Boot, ...
```

Le résumé et les sections sont masqués si vides, garantissant la structure visuelle.

## 3. Gestion des Durées
- `compute_duration_label` découpe la chaîne de dates et utilise `dateparser` pour interpréter des formats FR/EN (mois abrégés, "Présent", etc.).
- Gestion des cas ouverts (Présent → date du jour).
- Résultat normalisé : `"{X} ans {Y} mois"` ou `"{X} mois"`.

## 4. Robustesse & Observabilité
- **AI Client** : `GROQ_API_KEY` obligatoire, `GROQ_MODEL` optionnel. Les erreurs sont journalisées (`logger.warning`), puis fallback automatique.
- **Logging** : tous les modules (`google_drive`, `parsers`, `ai_client`) partagent le logger standard.
- **Filet de sécurité** : `full_text` stocke le bloc original, et la section `Annexe` du PDF conserve les lignes non classifiées.

## 5. JSON Final

```json
{
  "meta": { "filename": "...", "ocr_applied": "False" },
  "basics": { "name": "...", "email": "...", "phone": "..." },
  "experience": [
    {
      "job_title": "...",
      "company": "...",
      "location": "...",
      "dates": "Janv. 2021 - Présent",
      "duration": "3 ans 2 mois",
      "summary": "",
      "tasks": ["..."],
      "skills": ["Java", "Spring Boot"],
      "full_text": "Bloc brut..."
    }
  ],
  "education": [{"degree": "...", "full_text": "..."}],
  "skills_tech": [...],
  "skills_soft": [...],
  "raw_text": "..."
}
```

## 6. Points d'Extension
- Support multi-langues pour les prompts Groq (FR/EN déjà gérés implicitement).
- Ajout de tests unitaires sur `compute_duration_label` + `segment_experience_blocks`.
- Ajout d'une file `ai_cache.json` si l'on souhaite mémoïser les réponses Groq sur un repo public (non requis aujourd'hui).

## 7. Feature Flag IA
- `USE_AI_EXPERIENCE=false` (par défaut) : seul le parser rule-based est utilisé, aucune dépendance à Groq pendant l’exécution GitHub Action.
- `USE_AI_EXPERIENCE=true` : `ai_parsers.py` est chargé, chaque bloc d’expérience passe par Groq puis validation. Tout échec retombe automatiquement sur `_rule_based_entry`.

