# Projet d'Automatisation de Traitement de CV

Ce projet automatise le traitement de CVs (.pdf, .docx) stockés dans un dossier Google Drive en utilisant une GitHub Action.

## Fonctionnalités

- **Téléchargement depuis Google Drive** : Récupère les fichiers .pdf et .docx d'un dossier spécifié.
- **Extraction de Texte** : Extrait le contenu textuel des fichiers. Gère les PDFs basés sur des images grâce à l'OCR (Tesseract).
- **Analyse de CV** : Utilise `pyresparser` pour extraire des informations structurées (nom, email, compétences, etc.).
- **Génération de Fichiers** : Crée un fichier `.json` avec les données brutes et extraites, et un `.pdf` formaté à partir d'un template HTML.
- **Upload vers Google Drive** : Envoie les fichiers `.json` et `.pdf` générés dans un dossier de destination.
- **Automatisation via GitHub Actions** : L'ensemble du processus est exécuté automatiquement via une GitHub Action, sans aucune exécution locale requise.

## Structure du Projet

```
.
├── .github/workflows/
│   └── process-cvs.yml   # Workflow GitHub Action
├── data/test_cvs/
│   ├── cv1.pdf           # Fichier CV de test (placeholder)
│   └── cv2.pdf           # Fichier CV de test (placeholder)
├── templates/
│   └── template.html     # Template Jinja2 pour le PDF final
├── formatters.py         # Génération du PDF à partir du template
├── google_drive.py       # Fonctions pour interagir avec l'API Google Drive
├── main.py               # Script principal orchestrant le pipeline
├── parsers.py            # Fonctions d'extraction et d'analyse de texte
└── requirements.txt      # Dépendances Python
```

## Configuration Requise

Pour que ce projet fonctionne, vous devez configurer les secrets suivants dans votre dépôt GitHub (`Settings > Secrets and variables > Actions > New repository secret`).

1.  **`GOOGLE_SERVICE_ACCOUNT_JSON`** :
    - Créez un projet sur la [Google Cloud Console](https://console.cloud.google.com/).
    - Activez l'API Google Drive.
    - Créez un compte de service (Service Account).
    - Générez une clé JSON pour ce compte de service et téléchargez-la.
    - Partagez vos dossiers Google Drive (source et destination) avec l'adresse email du compte de service.
    - Copiez le contenu complet du fichier JSON et collez-le comme valeur pour ce secret.

2.  **`SOURCE_FOLDER_ID`** :
    - Naviguez vers votre dossier Google Drive contenant les CVs à traiter.
    - L'ID du dossier se trouve dans l'URL (`https://drive.google.com/drive/folders/THIS_IS_THE_ID`).
    - Créez un secret avec cet ID.

## Lancement

Le workflow est configuré pour s'exécuter de deux manières :

1.  **Manuellement** : Allez dans l'onglet "Actions" de votre dépôt GitHub, sélectionnez "Process CVs from Google Drive" et cliquez sur "Run workflow".
2.  **Planifié** : Le workflow s'exécute automatiquement tous les jours à 2h UTC (configurable dans `process-cvs.yml`).

Les fichiers traités (`.json` et `.pdf`) seront automatiquement uploadés dans un dossier nommé `CV-Processed` à la racine de votre Google Drive.
