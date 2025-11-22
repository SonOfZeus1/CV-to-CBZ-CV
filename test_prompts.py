from ai_parsers import CONTACT_USER_PROMPT, SEGMENTATION_USER_PROMPT, EXPERIENCE_USER_PROMPT, EDUCATION_USER_PROMPT

def test_formatting():
    print("Testing CONTACT_USER_PROMPT formatting...")
    try:
        formatted = CONTACT_USER_PROMPT.format(text="test content")
        print("✅ CONTACT_USER_PROMPT OK")
    except KeyError as e:
        print(f"❌ CONTACT_USER_PROMPT FAILED: {e}")

    print("Testing SEGMENTATION_USER_PROMPT formatting...")
    try:
        formatted = SEGMENTATION_USER_PROMPT.format(text="test content")
        print("✅ SEGMENTATION_USER_PROMPT OK")
    except KeyError as e:
        print(f"❌ SEGMENTATION_USER_PROMPT FAILED: {e}")

    print("Testing EXPERIENCE_USER_PROMPT formatting...")
    try:
        formatted = EXPERIENCE_USER_PROMPT.format(text="test content")
        print("✅ EXPERIENCE_USER_PROMPT OK")
    except KeyError as e:
        print(f"❌ EXPERIENCE_USER_PROMPT FAILED: {e}")

    print("Testing EDUCATION_USER_PROMPT formatting...")
    try:
        formatted = EDUCATION_USER_PROMPT.format(text="test content")
        print("✅ EDUCATION_USER_PROMPT OK")
    except KeyError as e:
        print(f"❌ EDUCATION_USER_PROMPT FAILED: {e}")

if __name__ == "__main__":
    test_formatting()
