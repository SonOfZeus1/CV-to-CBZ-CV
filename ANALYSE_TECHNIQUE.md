# ANALYSE TECHNIQUE (Architecture V3 - Robust Hybrid Parsing)

Ce document décrit l'architecture refondue du pipeline de traitement de CV. Cette version (v3) a été conçue pour pallier les manques de fiabilité des librairies obsolètes (`pyresparser`) et pour offrir une structure de données riche et exploitable.

## 1. Architecture Globale

Le pipeline est un système ETL (Extract, Transform, Load) exécuté de manière éphémère sur GitHub Actions.

- **Source :** Google Drive (Supporte les "Shared Drives").
- **Compute :** GitHub Runner (Ubuntu 22.04/24.04, Python 3.11).
- **Destination :** Google Drive (Même dossier que la source).
- **Concurrency :** Traitement parallèle (Multi-threading) pour optimiser les I/O.

## 2. Stratégie de Parsing (Le cœur du système)

L'ancien parser (boîte noire fragile) a été remplacé par une implémentation maison (`parsers.py`) qui combine plusieurs techniques pour garantir un résultat, même sur des documents difficiles.

### A. Extraction de Texte Hybride
Le système décide dynamiquement comment lire le fichier :
1.  **Extraction native (`fitz`) :** Rapide et précise pour les "vrais" PDF.
2.  **Détection de "Garbage" / Scan :** Si la densité de texte est trop faible (< 50 chars/page) ou si le ratio de caractères non-alphanumériques dépasse 40% (signe d'un mauvais encodage de police), le mode OCR s'active.
3.  **OCR (`Tesseract`) :** Conversion des pages en images haute résolution (150 DPI) puis extraction optique.

### B. Analyse Sémantique "Inter-Section"
Au lieu de traiter le texte comme un bloc unique, le parser le segmente :
1.  **Identification des Headers :** Utilisation de mots-clés (`Expérience`, `Formation`, `Langues`) pour découper le texte en blocs logiques.
2.  **Parsing Granulaire (Expérience) :**
    - À l'intérieur du bloc "Expérience", une Regex puissante détecte les lignes contenant des dates (ex: `Jan 2020 - Present`).
    - Ces lignes servent de séparateurs pour créer des objets `ExperienceEntry` structurés.
    - Une analyse NER (Spacy) est tentée sur ces lignes pour extraire le nom de l'entreprise (`ORG`).
3.  **Extraction de Champs Spécifiques :**
    - **Emails/Téléphones/Liens :** Regex strictes.
    - **Compétences :** Recherche par dictionnaire de mots-clés (Tech vs Soft) + NLP.

### C. Modèle de Données (Schema)
Les données ne sont plus des dictionnaires en vrac, mais des `dataclass` Python typées, garantissant une structure JSON constante :

```json
{
  "meta": { "filename": "cv.pdf", "ocr_applied": "True" },
  "basics": { "name": "...", "email": "...", "phone": "..." },
  "skills_tech": ["Python", "Docker"],
  "experience": [
    {
      "title": "Devops Engineer",
      "company": "TechCorp",
      "date_start": "2020",
      "date_end": "Present",
      "description": ["Managed K8s cluster", "CI/CD pipeline"]
    }
  ],
  "raw_text": "..."
}
```

## 3. Performance & Scalabilité

- **Chargement Unique :** Le modèle NLP (Spacy `en_core_web_sm`) est chargé une seule fois en mémoire au démarrage du script, et partagé entre les threads.
- **Parallélisme :** Utilisation de `ThreadPoolExecutor` (4 workers par défaut) pour télécharger, traiter et uploader 4 CVs simultanément. Cela permet de masquer la latence réseau de l'API Google Drive.

## 4. Robustesse & Gestion d'Erreurs

- **Logging :** Utilisation du module `logging` standard.
- **Fallback :** Si le parsing structuré échoue (cas très rare), le script ne plante pas. Il capture l'exception et renvoie un objet `CVData` valide contenant au moins le texte brut (`raw_text`) et l'erreur dans les métadonnées.
- **Nettoyage :** Les ressources graphiques (Pixmaps) générées lors de l'OCR sont explicitement libérées de la mémoire.

## 5. Pistes d'Évolution

Pour aller encore plus loin :
- **LLM (GPT-4o/Gemini) :** Remplacer la logique Regex/Spacy par un appel API à un LLM pour structurer le JSON. C'est plus coûteux mais imbattable sur la qualité d'extraction.
- **Base de données Vectorielle :** Au lieu de générer des JSON, indexer les `raw_text` et les `skills` dans une DB vectorielle (Pinecone, Weaviate) pour permettre la recherche sémantique ("Trouve-moi un candidat qui connaît Python et a fait de la finance").

