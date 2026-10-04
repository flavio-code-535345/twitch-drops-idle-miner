"""Fork: a dashboard tab leaving the sign-in viewer must not log an ASGI crash."""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocket

from src.web.auth import WebAuth
from src.web.session_api import SessionAPI


@pytest.mark.asyncio
async def test_closing_after_the_dashboard_tab_left_does_not_raise(tmp_path):
    async def vnc(reader, writer):
        writer.write(b"RFB 003.008\n")
        await writer.drain()
        await reader.read()
        writer.close()

    server = await asyncio.start_server(vnc, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    browser = SimpleNamespace(state="sign_in", attempt=1, desktop=SimpleNamespace(port=port))
    auth = WebAuth(tmp_path / "auth.json")
    api = SessionAPI(auth, lambda: SimpleNamespace(login_browser=browser))
    incoming, output = asyncio.Queue(), asyncio.Queue()

    async def send(message):
        if message["type"] == "websocket.close":
            # uvicorn's ClientDisconnected is an OSError; Starlette re-raises it as
            # WebSocketDisconnect(1006), which escaped the viewer's cleanup before.
            raise OSError("client disconnected")
        await output.put(message)

    scope = {"type": "websocket", "path": "/api/session/vnc", "scheme": "ws", "query_string": b"",
             "server": ("testserver", 80), "client": ("local", 1),
             "headers": [(b"host", b"testserver"), (b"origin", b"http://testserver")]}
    websocket = WebSocket(scope, incoming.get, send)
    await incoming.put({"type": "websocket.connect"})
    task = asyncio.create_task(api.viewer(websocket))
    try:
        assert (await asyncio.wait_for(output.get(), 1))["type"] == "websocket.accept"
        assert (await asyncio.wait_for(output.get(), 1))["bytes"] == b"RFB 003.008\n"
        await incoming.put({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(task, 1)  # Raises if WebSocketDisconnect escapes.
        assert api._viewers == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        server.close()
        await server.wait_closed()
