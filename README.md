# Projet d'Automatisation de Traitement de CV

Ce projet automatise le traitement de CVs (.pdf, .docx) stockés dans un dossier Google Drive en utilisant une GitHub Action et une authentification sécurisée sans clé via Workload Identity Federation.

## Fonctionnalités (Mise à jour IA Stable)

- **Téléchargement depuis Google Drive** : Récupère les fichiers .pdf et .docx d'un dossier spécifié (supporte les Shared Drives).
- **Extraction Hybride Robuste** : 
    - OCR intelligent (via Tesseract) qui ne s'active que si la densité de texte est insuffisante.
    - Extraction de texte via Regex (Emails, Téléphones, Liens) et NLP (Spacy) pour les noms et compétences.
    - Segmentation automatique des sections (Expérience, Formation).
- **Structuration IA Groq** :
    - Chaque bloc d'expérience est restructuré par un modèle Groq (LLM) avec validation stricte.
    - Format imposé : `Titre – Entreprise, Localisation` + `Dates (Durée)` + Résumé + Tâches + Compétences.
    - Fallback déterministe regex si l'IA échoue ou n'est pas disponible.
- **Performance** : Traitement séquentiel (1 worker) pour la stabilité, prêt pour le multi-threading si nécessaire.
- **Génération de Fichiers** : Crée un fichier `.json` structuré et un `.pdf` formaté.
- **Upload vers Google Drive** : Renvoie les résultats directement dans le dossier source.

## Structure du Projet

```
.
├── .github/workflows/
│   └── process-cvs.yml   # Workflow GitHub Action
├── templates/
│   └── template.html     # Template Jinja2 pour le PDF final
├── formatters.py         # Génération du PDF (mapping données -> template)
├── google_drive.py       # Wrapper API Google Drive (Shared Drives support)
├── main.py               # Orchestrateur parallélisé
├── parsers.py            # Logique d'extraction (Regex + Spacy + OCR)
└── requirements.txt      # Dépendances Python
```

## Configuration Requise

Pour que ce projet fonctionne, vous devez configurer **Workload Identity Federation (WIF)** entre votre projet Google Cloud et votre dépôt GitHub. Cette méthode est plus sécurisée car elle n'utilise pas de clés JSON à longue durée de vie.

Ensuite, vous devez configurer les secrets suivants dans votre dépôt GitHub (`Settings > Secrets and variables > Actions > New repository secret`).

1.  **`GCP_WORKLOAD_IDENTITY_PROVIDER`** :
    - C'est le nom de ressource complet de votre fournisseur d'identité de charge de travail.
    - Il ressemble à : `projects/1234567890/locations/global/workloadIdentityPools/YOUR_POOL_NAME/providers/YOUR_PROVIDER_NAME`.

2.  **`GCP_SERVICE_ACCOUNT`** :
    - C'est l'adresse e-mail complète de votre compte de service Google Cloud.
    - Il ressemble à : `files-to-json@filestojson.iam.gserviceaccount.com`.
    - **Important** : Assurez-vous que ce compte de service a les permissions nécessaires sur les dossiers Google Drive.

3.  **`GCP_AUDIENCE`** :
    - C'est la valeur exacte utilisée pour le champ "Audience" dans la configuration de votre fournisseur d'identité sur Google Cloud.
    - Elle est composée du préfixe `//iam.googleapis.com/` suivi du nom de ressource complet de votre fournisseur d'identité (la même valeur que `GCP_WORKLOAD_IDENTITY_PROVIDER`).
    - La valeur complète doit ressembler à : `//iam.googleapis.com/projects/1234567890/locations/global/workloadIdentityPools/YOUR_POOL_NAME/providers/YOUR_PROVIDER_NAME`.

4.  **`SOURCE_FOLDER_ID`** :
    - Naviguez vers votre dossier Google Drive contenant les CVs à traiter.
    - L'ID du dossier se trouve dans l'URL (`https://drive.google.com/drive/folders/THIS_IS_THE_ID`).
    - Créez un secret avec cet ID.

5. **`GROQ_API_KEY`** :
    - Clé API Groq utilisée pour l'appel LLM.
    - Le workflow l'injecte dans `main.py` pour activer le parsing IA. Sans cette clé, le fallback regex sera utilisé.

6. *(Optionnel)* **`GROQ_MODEL`** (Variables → Repository variables) :
    - Permet de surcharger le modèle Groq (ex: `llama-3.1-70b-versatile`). Par défaut, ce modèle est utilisé.

## Lancement

Le workflow est configuré pour s'exécuter de deux manières :

1.  **Manuellement** : Allez dans l'onglet "Actions" de votre dépôt GitHub, sélectionnez "Process CVs from Google Drive" et cliquez sur "Run workflow".
2.  **Planifié** : Le workflow s'exécute automatiquement tous les jours à 2h UTC (configurable dans `process-cvs.yml`).

Les fichiers traités (`.json` et `.pdf`) seront automatiquement uploadés dans le même dossier que les fichiers sources sur Google Drive.

## Format JSON des expériences

Chaque expérience est garantie avec le schéma suivant :

```json
{
  "job_title": "Développeur Fullstack",
  "company": "TechCorp",
  "location": "Montréal, QC",
  "dates": "Janvier 2021 - Présent",
  "duration": "3 ans 2 mois",
  "summary": "Synthèse optionnelle si présente dans le CV original.",
  "tasks": [
    "Construire ...",
    "Maintenir ..."
  ],
  "skills": ["Java", "Spring Boot", "React"],
  "full_text": "Bloc brut conservé en filet de sécurité."
}
```

Ce format est ensuite injecté tel quel dans le template HTML (Résumé → Tâches → Compétences), garantissant une restitution stable entre le JSON et le PDF.
