import os
import sys

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from services.kis_service import KisService

def issue_and_print_token():
    print("🔑 KIS 엑세스 토큰 발급 중...")
    try:
        token = KisService.get_access_token()
        print(f"\n발급된 토큰:\n{token}\n")
        print("💡 이 토큰을 scripts/verify_kis_services.py의 HARDCODED_TOKEN 변수에 붙여넣어 사용할 수 있습니다.")
    except Exception as e:
        print(f"❌ 토큰 발급 실패: {e}")

if __name__ == "__main__":
    issue_and_print_token()
