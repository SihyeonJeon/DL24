import json
import os
import subprocess
import time
import uuid
import base64
import urllib.request
import urllib.parse
import traceback
from pathlib import Path

import runpod

# ── Configuration ────────────────────────────────────────────
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"
COMFY_DIR = os.environ.get("COMFY_DIR", "/comfyui")
EXECUTION_TIMEOUT = int(os.environ.get("EXECUTION_TIMEOUT", 1800))  # 30 mins

def log(msg):
    print(f"[handler] {msg}", flush=True)

# ── ComfyUI API Interaction ──────────────────────────────────
def queue_prompt(workflow, client_id):
    payload = json.dumps({
        "prompt": workflow,
        "client_id": client_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{COMFY_URL}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    response = urllib.request.urlopen(req, timeout=30)
    return json.loads(response.read())

def wait_for_execution(prompt_id, client_id):
    import websocket
    ws_url = f"ws://{COMFY_HOST}:{COMFY_PORT}/ws?clientId={client_id}"
    log(f"Connecting to WS: {ws_url}")
    ws = websocket.create_connection(ws_url, timeout=EXECUTION_TIMEOUT)

    try:
        start = time.time()
        while time.time() - start < EXECUTION_TIMEOUT:
            try:
                msg = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue

            if isinstance(msg, str):
                data = json.loads(msg)
                if data["type"] == "executing":
                    node = data["data"].get("node")
                    if node is None and data["data"].get("prompt_id") == prompt_id:
                        log("Execution completed!")
                        return True
                elif data["type"] == "execution_error":
                    log(f"ComfyUI ERROR: {data['data']}")
                    raise RuntimeError(f"ComfyUI execution error: {data['data']}")
                elif data["type"] == "progress":
                    p = data["data"]
                    log(f"Progress: {p.get('value')}/{p.get('max')}")
    except Exception as e:
        log(f"WS Error: {e}")
        raise
    finally:
        ws.close()

# ── RunPod Handler ───────────────────────────────────────────
def handler(event):
    try:
        job_input = event.get("input", {})
        workflow = job_input.get("workflow")
        image_url = job_input.get("image_url")
        
        if not workflow:
            return {"error": "Missing 'workflow' in input"}

        # 1. Handle __FROM_URL__ inputs (Download given image)
        if image_url:
            log(f"Downloading image_url: {image_url}")
            fname = f"dl_{uuid.uuid4().hex[:8]}.png"
            in_path = os.path.join(COMFY_DIR, "input", fname)
            os.makedirs(os.path.dirname(in_path), exist_ok=True)
            
            req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(in_path, 'wb') as out_file:
                out_file.write(response.read())
            
            # Replace __FROM_URL__ in workflow with actual filename
            wdump = json.dumps(workflow)
            wdump = wdump.replace("__FROM_URL__", fname)
            workflow = json.loads(wdump)
            log(f"Replaced __FROM_URL__ with {fname}")

        # 2. Track existing files to find newly generated ones
        out_dir = Path(COMFY_DIR) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        before_files = set(out_dir.glob("**/*"))

        # 3. Queue and Wait
        client_id = str(uuid.uuid4())
        res = queue_prompt(workflow, client_id)
        if "prompt_id" not in res:
            return {"error": f"Failed to queue prompt: {res}"}
            
        wait_for_execution(res["prompt_id"], client_id)

        # 4. Find new files
        after_files = set(out_dir.glob("**/*"))
        new_files = [f for f in after_files - before_files if f.is_file()]
        
        if not new_files:
            return {"error": "Execution completed but no new output files were found"}

        images = []
        videos = []
        
        for fpath in new_files:
            ext = fpath.suffix.lower()
            log(f"Reading generated file: {fpath.name}")
            with open(fpath, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
                
            if ext in [".png", ".jpg", ".jpeg", ".webp"]:
                images.append({"data": b64, "name": fpath.name})
            elif ext in [".mp4", ".webm", ".gif"]:
                videos.append({"data": b64, "name": fpath.name})

        return {
            "success": True,
            "images": images,
            "videos": videos
        }

    except Exception as e:
        err = traceback.format_exc()
        log(f"Handler error: {err}")
        return {"error": str(e), "trace": err}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
