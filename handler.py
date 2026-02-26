"""
RunPod Serverless Handler
Pipeline: Z-Image generation (×N images) → WAN 2.2 I2V LightX2V (×N videos)

Model stack:
  Image gen : diffusion_models/z_image/ARAZmixZIT019_bf16.safetensors
              clip/qwen_3_4b_fp8_mixed.safetensors
              vae/ae.safetensors
              loras/skin_texture_zit.safetensors

  I2V       : diffusion_models/wan22_i2vHighV21.safetensors  (High noise)
              diffusion_models/wan22_i2vLowV21.safetensors   (Low noise)
              loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank256_bf16.safetensors  (High, str=1.5)
              loras/wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors (Low, str=1.0)
              text_encoders/umt5_xxl_fp16.safetensors
              clip_vision/clip_vision_h.safetensors
              vae/Wan2.1_VAE.pth

Input:
  { "input": { "workflow": { ...ComfyUI API-format JSON... } } }

Output:
  {
    "images":      [{"filename": str, "type": "base64", "data": str}],  // optional
    "videos":      [{"filename": str, "type": "base64", "data": str}],  // optional
    "image_count": int,
    "video_count": int
  }
"""

import base64
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import uuid

import runpod

# ── Config ────────────────────────────────────────────────────
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"
COMFY_DIR  = os.environ.get("COMFY_DIR", "/comfyui")

# A100 80GB: image gen은 빠르고, WAN fp16 14B × 2단계 × 3세그먼트가 병목
STARTUP_TIMEOUT   = int(os.environ.get("STARTUP_TIMEOUT",   300))   # 5min
EXECUTION_TIMEOUT = int(os.environ.get("EXECUTION_TIMEOUT", 2400))  # 40min

# Instagram 4:5 portrait
INSTAGRAM_W = 1080
INSTAGRAM_H = 1350

comfy_process = None


def log(msg: str):
    print(f"[handler] {msg}", flush=True)


# ── ComfyUI lifecycle ─────────────────────────────────────────

def start_comfyui():
    global comfy_process
    log(f"Starting ComfyUI from {COMFY_DIR} ...")

    cmd = [
        sys.executable, "main.py",
        "--listen", "0.0.0.0",
        "--port", str(COMFY_PORT),
        "--disable-auto-launch",
        "--extra-model-paths-config", f"{COMFY_DIR}/extra_model_paths.yaml",
        "--bf16-unet",
    ]
    log(f"CMD: {' '.join(cmd)}")
    comfy_process = subprocess.Popen(cmd, cwd=COMFY_DIR)
    log(f"PID: {comfy_process.pid}")


def wait_for_comfyui() -> bool:
    start, interval, last = time.time(), 3, ""
    while time.time() - start < STARTUP_TIMEOUT:
        if comfy_process and comfy_process.poll() is not None:
            log(f"FATAL: ComfyUI exited with code {comfy_process.returncode}")
            return False
        try:
            urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=5)
            log(f"ComfyUI ready in {int(time.time()-start)}s")
            return True
        except Exception as e:
            status = type(e).__name__
            if status != last:
                log(f"Waiting... ({int(time.time()-start)}s, {status})")
                last = status
            time.sleep(interval)
    log(f"ERROR: ComfyUI not reachable after {STARTUP_TIMEOUT}s")
    return False


# ── ComfyUI API ───────────────────────────────────────────────

def queue_prompt(workflow: dict, client_id: str) -> dict:
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req = urllib.request.Request(
        f"{COMFY_URL}/prompt", data=payload,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def wait_for_execution(prompt_id: str, client_id: str):
    import websocket
    ws = websocket.create_connection(
        f"ws://{COMFY_HOST}:{COMFY_PORT}/ws?clientId={client_id}",
        timeout=EXECUTION_TIMEOUT,
    )
    try:
        deadline = time.time() + EXECUTION_TIMEOUT
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str):
                continue
            msg = json.loads(raw)
            mtype = msg.get("type", "")

            if mtype == "executing":
                d = msg["data"]
                if d.get("node") is None and d.get("prompt_id") == prompt_id:
                    log("Execution complete")
                    return

            elif mtype == "execution_error":
                d = msg.get("data", {})
                raise RuntimeError(
                    f"Node {d.get('node_id','?')} error: {d.get('exception_message','unknown')}"
                )

            elif mtype == "progress":
                d = msg["data"]
                log(f"  step {d.get('value',0)}/{d.get('max',0)}")

        raise TimeoutError(f"Timed out after {EXECUTION_TIMEOUT}s")
    finally:
        ws.close()


# ── Output retrieval ──────────────────────────────────────────

def fetch_file(filename: str, subfolder: str, ftype: str) -> bytes:
    params = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": ftype})
    return urllib.request.urlopen(f"{COMFY_URL}/view?{params}", timeout=120).read()


def optimize_for_instagram(raw: bytes) -> bytes:
    """Center-crop to 1080×1350, convert PNG → JPEG q95."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(raw))
    w, h = img.size
    if h > INSTAGRAM_H:
        top = (h - INSTAGRAM_H) // 2
        img = img.crop((0, top, w, top + INSTAGRAM_H))
        log(f"  Cropped {w}×{h} → {w}×{INSTAGRAM_H}")
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, optimize=True, subsampling=0)
    result = buf.getvalue()
    log(f"  PNG({len(raw)//1024}KB) → JPEG({len(result)//1024}KB)")
    return result


def get_outputs(prompt_id: str):
    """
    Returns (images, videos) as lists of base64 dicts.
    Handles both 'images' key and 'videos'/'gifs' keys in node outputs.
    """
    history = json.loads(
        urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}", timeout=30).read()
    )
    if prompt_id not in history:
        raise RuntimeError(f"Prompt {prompt_id} not found in history")

    outputs = history[prompt_id].get("outputs", {})
    images, videos = [], []

    for node_id, node_out in outputs.items():

        # Images
        for img_info in node_out.get("images", []):
            if img_info.get("type") == "temp":
                continue
            fname    = img_info["filename"]
            subfolder = img_info.get("subfolder", "")
            ftype    = img_info.get("type", "output")
            log(f"Fetching image: {fname}")
            raw = fetch_file(fname, subfolder, ftype)
            if fname.lower().endswith(".png"):
                raw   = optimize_for_instagram(raw)
                fname = fname.rsplit(".", 1)[0] + ".jpg"
            images.append({
                "filename": fname,
                "type": "base64",
                "data": base64.b64encode(raw).decode(),
            })

        # Videos — VHS uses "gifs" key; Wan22FMLF may use "videos" key
        for vkey in ("videos", "gifs"):
            for vid_info in node_out.get(vkey, []):
                if vid_info.get("type") == "temp":
                    continue
                fname = vid_info.get("filename", "")
                if not fname:
                    continue
                subfolder = vid_info.get("subfolder", "")
                ftype     = vid_info.get("type", "output")
                log(f"Fetching video: {fname} [{subfolder}]")
                raw = fetch_file(fname, subfolder, ftype)
                log(f"  Video size: {len(raw)//1024} KB")
                videos.append({
                    "filename": fname,
                    "type": "base64",
                    "data": base64.b64encode(raw).decode(),
                })

    return images, videos


# ── RunPod handler ────────────────────────────────────────────

def handler(event: dict) -> dict:
    try:
        job_input = event.get("input", {})
        workflow  = job_input.get("workflow")
        if not workflow:
            return {"error": "No 'workflow' key in input"}

        client_id = str(uuid.uuid4())
        log(f"Queuing prompt (client={client_id})")

        result    = queue_prompt(workflow, client_id)
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            log(f"Queue response: {json.dumps(result, indent=2)}")
            return {"error": f"Failed to queue: {result.get('error', result.get('node_errors', 'unknown'))}"}

        log(f"Prompt ID: {prompt_id}")
        wait_for_execution(prompt_id, client_id)

        images, videos = get_outputs(prompt_id)
        log(f"Done — {len(images)} image(s), {len(videos)} video(s)")

        if not images and not videos:
            return {"error": "No outputs generated"}

        resp = {"image_count": len(images), "video_count": len(videos)}
        if images:
            resp["images"] = images
        if videos:
            resp["videos"] = videos
        return resp

    except Exception:
        log(f"Handler exception:\n{traceback.format_exc()}")
        return {"error": traceback.format_exc()}


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    log("=" * 60)
    log("RunPod Worker — Z-Image + WAN 2.2 I2V LightX2V")
    log("=" * 60)

    start_comfyui()
    if not wait_for_comfyui():
        log("FATAL: ComfyUI failed to start")
        sys.exit(1)

    log("Starting RunPod serverless handler...")
    runpod.serverless.start({"handler": handler})
