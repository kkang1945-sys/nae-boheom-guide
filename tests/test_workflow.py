from fastapi.testclient import TestClient

from insurance_app.main import app


def test_pc_workflow_pages_are_connected() -> None:
    with TestClient(app) as client:
        for path in ("/", "/documents/combined", "/privacy", "/financial-info", "/results", "/evidence"):
            response = client.get(path)
            assert response.status_code == 200

        home = client.get("/").text
        assert "내보험 사용설명서" in home
        assert "보험증권 추가" in home
        assert "기존 점검내역 불러오기" in home
        assert "보험증권 읽어보기" in home
        assert "먼저 보험증권 파일을 선택해 주세요" in home
        assert "보험증권을 읽는 중입니다" in home
        assert "개인정보와 원본 보호 안내" not in home
        assert "경제정보 입력" in client.get("/financial-info").text
        assert "빈칸은 0원이 아니라" in client.get("/financial-info").text
        results = client.get("/results").text
        assert "보험 보장 점검 결과" in results
        assert "질병별 생활자금 공백" not in results
        assert "정부·공공기관 자료" in client.get("/evidence").text
