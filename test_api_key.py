import os
import sys
from groq import Groq
from dotenv import load_dotenv

def test_key():
    # Load env vars
    load_dotenv()
    
    # 1. Get the key
    api_key = os.getenv("GROQ_API_KEY")
    
    print("--- TEST CLÉ API GROQ ---")
    
    if not api_key:
        print("❌ ERREUR: La variable d'environnement GROQ_API_KEY n'est pas définie.")
        print("Pour la définir temporairement, lancez :")
        print('export GROQ_API_KEY="gsk_..."')
        print("Puis relancez ce script.")
        
        # Option to paste it directly for testing
        response = input("\nVoulez-vous coller votre clé ici pour tester maintenant ? (o/n): ")
        if response.lower().startswith('o'):
            api_key = input("Collez votre clé (gsk_...): ").strip()
        else:
            return

    try:
        print(f"\nTentative de connexion avec la clé : {api_key[:10]}...")
        client = Groq(api_key=api_key)
        
        # 2. Make a simple call
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": "Réponds juste par le mot 'SUCCÈS'.",
                }
            ],
            model="llama-3.1-8b-instant",
        )
        
        # 3. Check result
        result = chat_completion.choices[0].message.content
        print(f"\n✅ RÉPONSE DE L'API : {result}")
        print("✅ VOTRE CLÉ FONCTIONNE PARFAITEMENT.")
        
    except Exception as e:
        print(f"\n❌ ÉCHEC DE L'APPEL API : {e}")
        print("\nVérifiez que :")
        print("1. La clé est exacte (pas d'espace en trop).")
        print("2. Vous avez bien accès au modèle 'llama-3.1-8b-instant'.")

if __name__ == "__main__":
    test_key()
