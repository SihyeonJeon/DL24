"""
RunPod Serverless Handler — Z-Image + LTX-2 Unified
Supports single job and batch mode on a single A100 endpoint.

API:
  Single:  {"workflow": {...}}
  Single:  {"workflow": {...}, "image_url": "https://..."}
  Batch:   {"batch": [{"workflow": {...}}, {"workflow": {...}, "image_url": "..."}, ...]}
"""

import json
import os
import subprocess
import sys
import time
import uuid
import base64
import urllib.request
import urllib.parse
import traceback
import io
from pathlib import Path

import runpod

# ── Configuration ────────────────────────────────────────────
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"
COMFY_DIR = os.environ.get("COMFY_DIR", "/comfyui")
STARTUP_TIMEOUT = int(os.environ.get("STARTUP_TIMEOUT", 300))
EXECUTION_TIMEOUT = int(os.environ.get("EXECUTION_TIMEOUT", 1800))

comfy_process = None


def log(msg):
    print(f"[handler] {msg}", flush=True)


# ── ComfyUI Server Management ───────────────────────────────

def start_comfyui():
    global comfy_process
    log(f"Starting ComfyUI from {COMFY_DIR}...")

    cmd = [
        sys.executable, "main.py",
        "--listen", "0.0.0.0",
        "--port", str(COMFY_PORT),
        "--disable-auto-launch",
        "--extra-model-paths-config", f"{COMFY_DIR}/extra_model_paths.yaml",
        "--bf16-unet",
    ]
    log(f"Command: {' '.join(cmd)}")

    comfy_process = subprocess.Popen(cmd, cwd=COMFY_DIR)
    log(f"ComfyUI started (PID: {comfy_process.pid})")


def wait_for_comfyui():
    start = time.time()
    last_status = ""

    while time.time() - start < STARTUP_TIMEOUT:
        if comfy_process and comfy_process.poll() is not None:
            log(f"FATAL: ComfyUI exited with code {comfy_process.returncode}")
            return False
        try:
            req = urllib.request.Request(f"{COMFY_URL}/system_stats")
            urllib.request.urlopen(req, timeout=5)
            log(f"ComfyUI ready! ({int(time.time() - start)}s)")
            return True
        except Exception as e:
            status = type(e).__name__
            if status != last_status:
                log(f"Waiting for ComfyUI... ({int(time.time() - start)}s, {status})")
                last_status = status
            time.sleep(3)

    log(f"ERROR: ComfyUI not reachable after {STARTUP_TIMEOUT}s")
    return False


# ── ComfyUI API ──────────────────────────────────────────────

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
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())


def wait_for_execution(prompt_id, client_id):
    import websocket

    ws_url = f"ws://{COMFY_HOST}:{COMFY_PORT}/ws?clientId={client_id}"
    log(f"WS connect: {ws_url}")
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
                msg_type = data.get("type", "")

                if msg_type == "executing":
                    node = data["data"].get("node")
                    if node is None and data["data"].get("prompt_id") == prompt_id:
                        log("Execution completed!")
                        return True

                elif msg_type == "execution_error":
                    err = data.get("data", {})
                    raise RuntimeError(
                        f"ComfyUI error node {err.get('node_id', '?')}: "
                        f"{err.get('exception_message', 'Unknown')}"
                    )

                elif msg_type == "progress":
                    p = data["data"]
                    log(f"Progress: {p.get('value', 0)}/{p.get('max', 0)}")

        raise TimeoutError(f"Execution timed out ({EXECUTION_TIMEOUT}s)")
    finally:
        ws.close()


# ── Image Processing ─────────────────────────────────────────

def optimize_for_instagram(img_data):
    """Crop 1920→1350 height (4:5) + PNG→JPEG q95 for Instagram."""
    from PIL import Image

    img = Image.open(io.BytesIO(img_data))
    w, h = img.size

    target_h = 1350
    if h > target_h:
        top = (h - target_h) // 2
        img = img.crop((0, top, w, top + target_h))
        log(f"Cropped: {w}x{h} → {w}x{target_h}")

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, optimize=True, subsampling=0)
    jpeg_bytes = buf.getvalue()
    log(f"Optimized: {len(img_data)//1024}KB → {len(jpeg_bytes)//1024}KB JPEG")
    return jpeg_bytes


def get_images_from_history(prompt_id):
    """Retrieve generated images via ComfyUI history API + Instagram optimize."""
    req = urllib.request.Request(f"{COMFY_URL}/history/{prompt_id}")
    resp = urllib.request.urlopen(req, timeout=30)
    history = json.loads(resp.read())

    if prompt_id not in history:
        raise RuntimeError(f"Prompt {prompt_id} not in history")

    outputs = history[prompt_id].get("outputs", {})
    images = []

    for node_id, node_output in outputs.items():
        if "images" not in node_output:
            continue
        for img_info in node_output["images"]:
            if img_info.get("type") == "temp":
                continue

            params = urllib.parse.urlencode({
                "filename": img_info["filename"],
                "subfolder": img_info.get("subfolder", ""),
                "type": img_info.get("type", "output"),
            })
            img_req = urllib.request.Request(f"{COMFY_URL}/view?{params}")
            img_data = urllib.request.urlopen(img_req, timeout=60).read()

            img_data = optimize_for_instagram(img_data)
            images.append({
                "filename": img_info["filename"].rsplit(".", 1)[0] + ".jpg",
                "data": base64.b64encode(img_data).decode("utf-8"),
            })

    return images


# ── Video Processing ─────────────────────────────────────────

def download_image_for_comfy(image_url):
    """Download image_url into ComfyUI input dir, return local filename."""
    fname = f"dl_{uuid.uuid4().hex[:8]}.png"
    in_path = os.path.join(COMFY_DIR, "input", fname)
    os.makedirs(os.path.dirname(in_path), exist_ok=True)

    req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(in_path, "wb") as f:
        f.write(resp.read())

    log(f"Downloaded image → {fname}")
    return fname


def get_videos_from_output(before_files):
    """Find newly generated video files by diffing output directory."""
    out_dir = Path(COMFY_DIR) / "output"
    after_files = set(out_dir.glob("**/*"))
    new_files = [f for f in after_files - before_files if f.is_file()]

    videos = []
    for fpath in new_files:
        ext = fpath.suffix.lower()
        if ext in (".mp4", ".webm", ".gif"):
            log(f"Found video: {fpath.name}")
            with open(fpath, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            videos.append({"name": fpath.name, "data": b64})

    return videos


# ── Job Execution ────────────────────────────────────────────

def execute_single_job(job):
    """
    Execute one ComfyUI workflow job.
    - If image_url present → video job (LTX-2)
    - Otherwise → image job (Z-Image)
    """
    workflow = job.get("workflow")
    image_url = job.get("image_url")

    if not workflow:
        return {"error": "Missing 'workflow'"}

    is_video = bool(image_url)
    job_type = "video" if is_video else "image"
    log(f"=== Executing {job_type} job ===")

    # ── Video: download source image & replace placeholder ────
    if is_video:
        fname = download_image_for_comfy(image_url)
        wdump = json.dumps(workflow)
        wdump = wdump.replace("__FROM_URL__", fname)
        workflow = json.loads(wdump)

    # ── Snapshot output dir (for video file detection) ────────
    out_dir = Path(COMFY_DIR) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    before_files = set(out_dir.glob("**/*"))

    # ── Queue & execute ──────────────────────────────────────
    client_id = str(uuid.uuid4())
    result = queue_prompt(workflow, client_id)
    prompt_id = result.get("prompt_id")

    if not prompt_id:
        errors = result.get("error", result.get("node_errors", "Unknown"))
        return {"error": f"Queue failed: {errors}"}

    log(f"Queued: {prompt_id}")
    wait_for_execution(prompt_id, client_id)

    # ── Collect results ──────────────────────────────────────
    if is_video:
        videos = get_videos_from_output(before_files)
        if not videos:
            return {"error": "No video files generated"}
        return {"videos": videos}
    else:
        images = get_images_from_history(prompt_id)
        if not images:
            return {"error": "No images generated"}
        return {"images": images}


# ── RunPod Handler ───────────────────────────────────────────

def handler(event):
    try:
        job_input = event.get("input", {})

        # ── Batch mode ───────────────────────────────────────
        batch = job_input.get("batch")
        if batch and isinstance(batch, list):
            log(f"=== BATCH MODE: {len(batch)} jobs ===")
            results = []
            for i, job in enumerate(batch):
                log(f"--- Batch job {i+1}/{len(batch)} ---")
                try:
                    r = execute_single_job(job)
                    results.append(r)
                except Exception as e:
                    log(f"Batch job {i+1} failed: {e}")
                    results.append({"error": str(e)})

            return {"batch_results": results}

        # ── Single mode ──────────────────────────────────────
        return execute_single_job(job_input)

    except Exception as e:
        err = traceback.format_exc()
        log(f"Handler error: {err}")
        return {"error": str(e), "trace": err}


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    log("=" * 60)
    log("  AI Model Reels — Unified Worker (Z-Image + LTX-2)")
    log("=" * 60)

    start_comfyui()

    if not wait_for_comfyui():
        log("FATAL: ComfyUI failed to start. Exiting.")
        sys.exit(1)

    log("Starting RunPod serverless handler...")
    runpod.serverless.start({"handler": handler})
