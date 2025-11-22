import os
import logging
import json
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add src to path if needed (assuming we are in root)
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from parsers import parse_cv
except ImportError:
    # Try importing from current directory if src is not used
    from parsers import parse_cv

def test_cv(file_path):
    logger.info(f"Testing CV: {file_path}")
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return

    result = parse_cv(file_path)
    
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        # Check for experience tasks
        experiences = result.get("experience", [])
        for i, exp in enumerate(experiences):
            tasks = exp.get("tasks", [])
            print(f"Experience {i+1}: {len(tasks)} tasks found.")
            if not tasks:
                print(f"WARNING: Experience {i+1} has NO tasks!")
            else:
                print(f"Sample task: {tasks[0]}")
                
        # Check for AI usage indication in logs (by checking if tasks look structured)
    else:
        logger.error("Parsing returned None")

if __name__ == "__main__":
    # Check for API Key
    if os.getenv("GROQ_API_KEY"):
        print("GROQ_API_KEY is set.")
    else:
        print("WARNING: GROQ_API_KEY is NOT set. AI will be disabled.")

    test_cv("data/test_cvs/cv2.pdf")
