"""Smoke-test the Streamable HTTP transport without requiring a running MMD instance."""

import asyncio
import socket
import subprocess
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_listener(process: subprocess.Popen, port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MCP HTTP server exited early with code {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    raise TimeoutError(f"MCP HTTP server did not listen on port {port}: {last_error}")


async def check() -> None:
    port = unused_local_port()
    command = [
        sys.executable, "-m", "mmd_mcp.server",
        "--transport", "streamable-http",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--path", "/mcp",
        "--allowed-host", "bridge.example.test",
        "--allowed-origin", "https://bridge.example.test",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        await asyncio.to_thread(wait_for_listener, process, port)
        endpoint = f"http://127.0.0.1:{port}/mcp"
        # Simulate a tunnel forwarding its public Host and browser Origin headers.
        async with httpx.AsyncClient(
            headers={
                "host": "bridge.example.test",
                "origin": "https://bridge.example.test",
            }
        ) as http_client:
            async with streamable_http_client(endpoint, http_client=http_client) as (
                read_stream, write_stream, _
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    names = {tool.name for tool in response.tools}
                    required = {
                        "mmd_list_windows",
                        "mmd_capture_window",
                        "mmd_list_models",
                        "mmd_get_ui_state",
                        "mmd_batch_bone_keys",
                        "mmd_batch_camera_keys",
                        "mmd_read_motion_document",
                        "mmd_write_motion_document",
                        "mmd_export_video",
                    }
                    missing = sorted(required - names)
                    assert not missing, f"HTTP server is missing expected tools: {missing}"
                    assert len(names) >= 50, f"Expected at least 50 tools, found {len(names)}"
                    print(
                        "Streamable HTTP initialize, tunnel Host/Origin allowlist and "
                        f"tool discovery: OK ({len(names)} tools)"
                    )
    finally:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 10)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, 5)
        if process.returncode not in (0, -15, 1):
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"MCP HTTP server exited with {process.returncode}: {stderr}")


if __name__ == "__main__":
    asyncio.run(check())
