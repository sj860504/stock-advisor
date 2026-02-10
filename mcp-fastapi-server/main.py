from fastapi import FastAPI, Request
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
import uvicorn
import asyncio

# 1. FastAPI 앱 및 MCP 서버 초기화
app = FastAPI(title="My MCP Server")
mcp = Server("fastapi-mcp-demo")

# 2. 툴(Tool) 정의
# 간단한 덧셈 툴을 예제로 등록합니다.
@mcp.tool()
async def add(a: int, b: int) -> int:
    """두 숫자를 더합니다."""
    return a + b

@mcp.tool()
async def get_weather(city: str) -> str:
    """도시의 날씨를 조회합니다 (예제)."""
    return f"{city}의 날씨는 맑음입니다. (Demo)"

@mcp.tool()
async def ask_amiya(message: str) -> str:
    """
    AI 어시스턴트 Amiya에게 메시지를 보냅니다.
    이 툴은 Amiya가 있는 Slack 채널로 메시지를 전송하여 Amiya를 호출합니다.
    """
    # TODO: 실제 슬랙 Webhook 또는 API 호출 로직 구현 필요
    # 현재는 로그만 출력하고 성공 메시지 반환
    print(f"[To Amiya] {message}")
    
    # 예시: requests.post(SLACK_WEBHOOK_URL, json={"text": f"<@AMIYA_ID> {message}"})
    
    return f"Amiya에게 다음 메시지를 전달했습니다: '{message}' (현재는 시뮬레이션 모드)"

# 3. SSE Transport 설정 (FastAPI 연동 핵심)
# SSE 연결을 관리하기 위한 전역 transport 저장소 (단일 연결 데모용)
# 실제 프로덕션에서는 세션 관리가 필요할 수 있습니다.
sse_transport = None

@app.get("/sse")
async def handle_sse(request: Request):
    """MCP 클라이언트와 SSE 연결을 수립하는 엔드포인트"""
    global sse_transport
    
    # SSE Transport 생성 및 연결 시작
    sse_transport = SseServerTransport("/messages")
    
    async def stream():
        async for message in sse_transport.connect(request):
            yield message
            
    # mcp 서버와 transport 연결
    await mcp.connect(sse_transport)
    
    # SSE 스트림 반환 (FastAPI의 EventSourceResponse 등 활용 가능하나, 여기선 기본 방식)
    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(stream())

@app.post("/messages")
async def handle_messages(request: Request):
    """클라이언트로부터 메시지를 받아 MCP 서버로 전달"""
    if sse_transport:
        await sse_transport.handle_post_message(request)
    return {"status": "ok"}

if __name__ == "__main__":
    # sse-starlette 필요 (SSE 응답용)
    print("Starting MCP Server on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
