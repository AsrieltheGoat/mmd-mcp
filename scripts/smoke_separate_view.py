"""Opt-in regression check using a new, empty MMD instance only.

python scripts/smoke_separate_view.py --apply --mmd-exe ABS_EXE [--english] [--model ABS_MIKU]
"""

import argparse
import asyncio
import base64
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mmd_mcp.native_dialogs import menu_command
from mmd_mcp.windows import capture_window, list_windows, select_window


async def check(executable, english=False, model=None):
    output = Path(__file__).resolve().parents[1] / "local" / f"separate-view-{datetime.now():%Y%m%d-%H%M%S-%f}"
    output.mkdir(parents=True)
    process = subprocess.Popen([str(executable)], cwd=executable.parent)
    report = {"pid": process.pid, "language": "en" if english else "ja", "steps": []}
    try:
        target = None
        for _ in range(100):
            target = next((w for w in list_windows() if w.pid == process.pid), None)
            if target:
                break
            await asyncio.sleep(.1)
        assert target, "New MMD did not become ready"
        if english:
            menu_command(target.hwnd, 260)
            await asyncio.sleep(.2)
        # Exercise implicit selection when this is the only running instance.
        arguments = {} if len(list_windows()) == 1 else {"hwnd": target.hwnd}
        params = StdioServerParameters(command=sys.executable, args=["-m", "mmd_mcp.server"])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()

                async def call(tool_name, **kwargs):
                    result = await session.call_tool(tool_name, {**arguments, **kwargs})
                    assert not result.isError, (tool_name, result)
                    if result.structuredContent and "status" in result.structuredContent:
                        assert result.structuredContent["status"] == "completed", (tool_name, result)
                    report["steps"].append({"tool": tool_name, "arguments": kwargs})
                    print(f"OK {tool_name} {kwargs}", flush=True)
                    return result

                async def capture(label, view="auto"):
                    result = await call("mmd_capture_window", view=view)
                    images = [item for item in result.content if item.type == "image"]
                    assert len(images) == 1 and images[0].mimeType == "image/png"
                    data = base64.b64decode(images[0].data, validate=True)
                    (output / f"{label}.png").write_bytes(data)
                    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    assert image is not None and image.size
                    report[label] = {"height": image.shape[0], "width": image.shape[1]}
                    return image.shape

                normal_shape = await capture("normal")
                missing = await session.call_tool("mmd_capture_window", {**arguments, "view": "render"})
                assert missing.isError, "render must require separate view"
                menu_command(target.hwnd, 280)
                await asyncio.sleep(.3)
                instances = [w for w in list_windows() if w.pid == process.pid]
                assert len(instances) == 1 and instances[0].hwnd == target.hwnd
                assert capture_window(select_window(target.hwnd)).hwnd != target.hwnd
                listed = await session.call_tool("mmd_list_windows", {})
                assert not listed.isError
                await call("mmd_set_frame", frame=12)
                state = await call("mmd_get_ui_state")
                assert state.structuredContent["displayed_frame"]["value"] == 12
                await call("mmd_batch_camera_keys", items=[{"frame": 12, "camera": {
                    "position": {"x": 3, "y": 11}, "distance": 38}}])
                camera = await call("mmd_get_camera")
                assert camera.structuredContent["position"]["x"] == 3
                render_shape = await capture("separate-auto")
                assert await capture("separate-render", "render") == render_shape
                assert await capture("separate-main", "main") == normal_shape
                assert render_shape != normal_shape, "Detached viewport should differ from editing UI"
                preview = await call("mmd_preview_motion", hwnd=target.hwnd,
                                     output_directory=str(output / "preview"),
                                     start_frame=0, end_frame=2, step=1, allow_frame_evaluation=True)
                assert preview.structuredContent["restored_frame"] == 12
                sample = cv2.imread(str(output / "preview" / "frame-000000.png"))
                assert sample is not None and sample.shape == render_shape
                if model:
                    await call("mmd_load_file", kind="model", path=str(model))
                    await call("mmd_batch_bone_keys", items=[{"frame": 12,
                        "bones": [{"name": "左腕", "rotation_degrees": {"z": -20}}],
                        "morphs": [{"category": "eyes", "name": "blink" if english else "まばたき",
                                    "weight": .6}]}])
                    await call("mmd_set_frame", frame=13)
                    await call("mmd_set_frame", frame=12)
                    await call("mmd_select_bone", name="左腕")
                    bone = await call("mmd_get_selected_bone_transform")
                    assert bone.structuredContent["rotation_degrees"]["z"] == -20
                    state = await call("mmd_get_ui_state")
                    assert state.structuredContent["morph_panels"]["eyes"]["displayed_weight"] == .6
                    await capture("separate-model")
                menu_command(target.hwnd, 280)
                await asyncio.sleep(.3)
                assert capture_window(select_window(target.hwnd)).hwnd == target.hwnd
                # MMD may restore a different main-window size when leaving
                # separate view (notably if it started maximized).
                await capture("normal-restored")
                state = await call("mmd_get_ui_state")
                assert state.structuredContent["displayed_frame"]["value"] == 12
        report["status"] = "passed"
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"PASS {output}", flush=True)
    finally:
        # This script owns only the newly launched process.
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--mmd-exe", type=Path, required=True)
    parser.add_argument("--english", action="store_true")
    parser.add_argument("--model", type=Path, help="Optional model with left-arm and blink controls")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required to launch and edit an isolated test instance")
    if not args.mmd_exe.is_absolute() or not args.mmd_exe.is_file():
        parser.error("--mmd-exe must name an existing absolute executable path")
    if args.model and (not args.model.is_absolute() or not args.model.is_file()):
        parser.error("--model must name an existing absolute model path")
    asyncio.run(check(args.mmd_exe, args.english, args.model))
