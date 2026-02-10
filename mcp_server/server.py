from mcp.server.fastmcp import FastMCP
import uvicorn
import httpx
import os

# Initialize FastMCP server
# This creates a Starlette/FastAPI-compatible application
mcp = FastMCP("Amiya-Connector")

@mcp.tool()
async def talk_to_amiya(message: str) -> str:
    """
    Send a message to Amiya (OpenClaw) and receive a reply.
    
    Args:
        message: The text message to send to Amiya.
    """
    # In a real scenario, you would send this to OpenClaw's webhook or API.
    # For now, we'll log it and return a placeholder since we are running locally
    # without a direct loopback channel configured.
    
    print(f"[{mcp.name}] Sending to Amiya: {message}")
    
    # Placeholder response
    # To make this work for real, configure an Incoming Webhook in OpenClaw
    # and POST to it here.
    return f"Amiya received: '{message}' (Mock response)"

@mcp.tool()
def get_status() -> str:
    """Check the status of the connection to Amiya."""
    return "Connected (Mock)"

if __name__ == "__main__":
    # Get the underlying Starlette/ASGI app
    # This serves the MCP protocol over SSE at /sse and /messages
    app = mcp.sse_app()
    
    print("Starting MCP Server on http://0.0.0.0:8080")
    print("MCP SSE Endpoint: http://0.0.0.0:8080/sse")
    uvicorn.run(app, host="0.0.0.0", port=8080)
