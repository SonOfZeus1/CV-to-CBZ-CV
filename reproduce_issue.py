import os
import logging
import json
from unittest.mock import patch
from parsers import parse_cv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The raw text provided by the user
RAW_TEXT = """Page 1 of 3 
Adel Anani 
E-mail / LinkedIn
Ingénieur Logiciel 
LANGUES: Français, Anglais
COMPÉTENCES TECHNIQUES 
•
Framework et langage de programmation:
•
Java, Spring Boot
•
Python, Django, Flask
•
C++
•
C#
•
Développement Web:
•
HTML5, CSS3,
•
JavaScript, Angular, NodeJS, ReactJS, VueJS
•
Ruby, RubyOnRails
•
Ingénierie logiciel : Maîtrise des concepts UI, UX, Front-end, Back-end, Programmation Orientée Objet,
Clean Code, Principes SOLID
•
Base de données: Oracle, MySQL, MongoDB, Firebase
•
Système d’opération: Windows, MacOS
EXPÉRIENCE 
Développeur Java
SAP, Montréal, CANADA 
      Septembre 2021-Aujourd’hui 
•
Conception, développement et implémentation de microservices (OMS workflow, OSTA Business Config,
OSTA Automation) Java Spring Boot
•
Développement et implémentation de multiples applications (OMS workflow, OSTA Business Config, OSTA
Automation) FrontEnd en Angular
•
Développement et implémentation d'un microservice (OMS workflow) utilisant Temporal et Kafka
•
Développement et implémentation de suites de tests d'intégration et de composants avec Cypress
•
Conception et documentation des plans de tests
•
Processus CI/CD
Environnement Technologique: GitHub, Jira, Confluence, Xray, Angular, Java, Postman, Cucumber, Cypress, 
Jenkins  
Page 2 of 3 
Développeur
Hilo Énergie, Montréal, CANADA 
        Juin 2020- Septembre 2021 
•
Conception, développement et implémentation de microservices C# .Net
•
Développement, implémentation et maintenance des pipelines CI/CD
•
Développement et implémentation de la suite de tests Cypress pour l'application mobile Hilo
•
Conception, développement et implémentation de collections de tests automatisés dans Postman
•
Conception et documentation des plans de tests
•
Processus CI/CD
Environnement Technologique: Azure, Jira, Confluence, Xray, Cypress, Python, C#, Postman, Jenkins 
Développeur Java
Kimoby, Québec, CANADA 
           Décembre 2019-Avril 2020 
•
Développement et implémentation de composants FrontEnd en Angular dans l'application mobile Kimoby,
l'application web Kimoby et le portail Kimoby Partner.
•
Développement et implémentation de fonctionnalités en Java dans l’application Kimoby.
Environnement Technologique: GitHub, Jira, Confluence, Xray, Angular, Java, Postman, Cypress, Jenkins 
Développeur Java
Revenu Quebec, Québec, CANADA 
 Décembre 2017-Novembre 2019 
•
Développement, implémentation et maintenance des pipelines CI/CD pour l'application de normalisation des
logiciels.
•
Développement et implémentation de composants FrontEnd en Angular dans l'application de normalisation
des logiciels.
•
Développement et implémentation de fonctionnalités en Java dans le système principal de l'application de
normalisation des logiciels.
Environnement Technologique: Team Foundation, Angular, Java, Postman 
Page 3 of 3 
Développeur Java 
Desjardins, Lévis, CANADA 
 Mai 2017-Novembre 2017 
•
Développement et implémentation de composants FrontEnd en Angular.
•
Développement et implémentation de fonctionnalités en Java Spring Boot dans le système principal du
système de devis d'assurance Desjardins.
•
Implémentation de suites de tests automatisés EndToEnd UI/UX en Selenium.
Environnement Technologique: Azure DevOps, Jira, Xray, Selenium, Angular, Java 
Analyste Programmeur Java
Revenu Quebec, Québec, CANADA 
 Avril 2014-Avril 2017 
•
Collecte et analyse des besoins fonctionnels
•
Traduction des besoins clients en exigences fonctionnelles
•
Conception, documentation et maintenance des spécifications fonctionnelles
•
Conception et documentation des plans de tests
•
Conception, développement et mise en œuvre de collections de tests automatisés.
•
Développement, implémentation et maintenance des pipelines CI/CD pour l'application de normalisation des
logiciels.
•
Développement et implémentation de composants FrontEnd en Angular dans l'application de normalisation
des logiciels.
•
Développement et implémentation de fonctionnalités en Java dans le système principal de l'application de
normalisation des logiciels.
Environnement Technologique: Team Foundation, Angular, Java, Postman 
ÉDUCATION 
Baccalauréat en Ingénierie Logiciel 
2019 
Université Laval, Québec, CANADA 
"""

def mock_extract_pdf(path):
    return RAW_TEXT, False

@patch('parsers.extract_text_from_pdf', side_effect=mock_extract_pdf)
def run_test(mock_extract):
    print("Running reproduction test...")
    
    # We use a dummy path ending in .pdf to trigger the PDF logic
    result = parse_cv("dummy_cv.pdf")
    
    print("\n--- Result JSON ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Check specific failure conditions
    if not result.get("experience") and result.get("extra_info"):
        print("\n[FAILURE] Experience is empty and extra_info is populated!")
        print(f"Extra info length: {len(result['extra_info'])}")
        if len(result['extra_info']) > 0:
             print(f"Extra info start: {result['extra_info'][0][:100]}...")
    else:
        print("\n[SUCCESS] Experience found or extra_info empty.")

if __name__ == "__main__":
    # Ensure API key is present (it should be in the env)
    if not os.getenv("GROQ_API_KEY"):
        print("WARNING: GROQ_API_KEY not set!")
    
    run_test()
