import logging
import json
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

try:
    from parsers import parse_cv_from_text
    from reproduce_issue import RAW_TEXT
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

load_dotenv()
logging.basicConfig(level=logging.INFO)

def run_test():
    print("Running reproduction test...")
    # Mock metadata
    metadata = {"ocr_applied": False}
    
    try:
        result = parse_cv_from_text(RAW_TEXT, "dummy_cv.md", metadata=metadata)
        print("\n--- Result JSON ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Execution Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
