import os
import sys

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from services.kis.kis_service import KisService

def issue_and_print_token():
    print("🔑 Issuing KIS access token...")
    try:
        token = KisService.get_access_token()
        print(f"\nIssued token:\n{token}\n")
        print("💡 You can paste this token into the HARDCODED_TOKEN variable in scripts/verify_kis_services.py.")
    except Exception as e:
        print(f"❌ Token issuance failed: {e}")

if __name__ == "__main__":
    issue_and_print_token()
