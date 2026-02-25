import json
import os
import subprocess
import sys
import time
import uuid
import base64
import glob
import urllib.request
import urllib.parse
import traceback

import runpod

# ── Configuration ────────────────────────────────────────────
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"
COMFY_DIR = os.environ.get("COMFY_DIR", "/comfyui")
STARTUP_TIMEOUT = int(os.environ.get("STARTUP_TIMEOUT", 300))
EXECUTION_TIMEOUT = int(os.environ.get("EXECUTION_TIMEOUT", 1800))  # 30 min max

# ── Hardcoded Credentials ────────────────────────────────────
# Cloudinary (Unsigned upload using preset)
CLOUDINARY_UPLOAD_URL = "https://api.cloudinary.com/v1_1/dp2azbanc/upload"
CLOUDINARY_UPLOAD_PRESET = "n8n insta"

# Instagram Graph API
IG_USER_ID = "17841477290360293"
IG_ACCESS_TOKEN = "EAAWj6Tz1HQoBQ4ZAyFNTFpE2RKSlSZB1ZCZBgBvzeXqMpk98MwXc2MegnmIYZCze8lLtaLJweEayuu7RTycZA5GhUaP1iqKypvUAk7H2fOWExY0vJEJqg4r1jpneutFn4JmTeDt5bvThvlRUuCrWrF1rKAFzZCQvQGunhYPE9HrsCSzAr6ysoQZC6ZBXb3SoHlbPJNQZDZD"

comfy_process = None

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
    log(f"Connecting to WebSocket: {ws_url}")
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
                    error_data = data.get("data", {})
                    raise RuntimeError(f"ComfyUI execution error: {json.dumps(error_data)}")
                elif msg_type == "progress":
                    progress = data["data"]
                    log(f"Progress: {progress.get('value', 0)}/{progress.get('max', 0)}")
    finally:
        ws.close()

# ── Upload Functions ─────────────────────────────────────────
def upload_to_cloudinary(file_path, resource_type="auto"):
    """Valid resource_type: 'image', 'video' or 'auto'"""
    import urllib.parse
    log(f"Uploading {file_path} to Cloudinary...")
    
    url = f"https://api.cloudinary.com/v1_1/dp2azbanc/{resource_type}/upload"
    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-F", f"upload_preset={CLOUDINARY_UPLOAD_PRESET}",
        "-F", f"file=@{file_path}"
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Cloudinary upload failed: {result.stderr}")
    
    resp_json = json.loads(result.stdout)
    secure_url = resp_json.get("secure_url")
    if not secure_url:
        raise RuntimeError(f"Cloudinary returned no secure_url: {result.stdout}")
    
    log(f"Cloudinary URL: {secure_url}")
    return secure_url

def create_ig_reels_container(video_url, caption):
    url = f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media"
    payload = json.dumps({
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": True,
        "access_token": IG_ACCESS_TOKEN
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req)
        data = json.loads(response.read())
        log(f"IG Container created: {data}")
        return data["id"]
    except urllib.error.HTTPError as e:
        log(f"IG Create API Error: {e.read().decode('utf-8')}")
        raise

def wait_for_ig_container(creation_id):
    url = f"https://graph.facebook.com/v21.0/{creation_id}?fields=status_code&access_token={IG_ACCESS_TOKEN}"
    for i in range(15):  # Wait up to ~75 seconds
        time.sleep(5)
        req = urllib.request.Request(url)
        try:
            response = urllib.request.urlopen(req)
            data = json.loads(response.read())
            status = data.get("status_code")
            log(f"IG Container Status: {status}")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                raise RuntimeError("IG container processing failed.")
        except Exception as e:
            log(f"Poll error: {e}")
    raise TimeoutError("IG Container took too long to finish.")

def publish_ig_reels(creation_id):
    url = f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media_publish"
    payload = json.dumps({
        "creation_id": creation_id,
        "access_token": IG_ACCESS_TOKEN
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req)
        data = json.loads(response.read())
        log(f"IG Reel successfully published! ID: {data}")
        return data
    except urllib.error.HTTPError as e:
        log(f"IG Publish API Error: {e.read().decode('utf-8')}")
        raise

# ── RunPod Handler ───────────────────────────────────────────
def handler(event):
    try:
        job_input = event.get("input", {})
        prompt1 = job_input.get("prompt1")
        video_prompt = job_input.get("videoPrompt")
        seed = job_input.get("seed", 12345)
        caption = job_input.get("caption", "Auto-generated reel ✨ #fyp #Mood")

        if not prompt1 or not video_prompt:
            return {"error": "Missing prompt1 or videoPrompt"}

        # 1. Image Generation (Z-Image SDXL pipeline)
        log("--- Stage 1: Generating Image ---")
        client_id_img = str(uuid.uuid4())
        
        # ── STAGE 1: IMAGE WORKFLOW (SDXL / Z-IMAGE) ──
        # IMPORTANT: Replace `img_workflow` with your exported ComfyUI API JSON!
        # Instructions:
        # 1. In ComfyUI Web UI, open Settings -> Enable "Enable Dev mode Options"
        # 2. Click "Save (API Format)"
        # 3. Paste the JSON here.
        # Ensure the prompt uses `prompt1` and seed uses `seed`.
        
        img_workflow = {
            "3": { "inputs": { "seed": seed, "steps": 25, "cfg": 3.5, "sampler_name": "euler", "scheduler": "simple", "denoise": 1, "model": ["31", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["13", 0] }, "class_type": "KSampler" },
            "6": { "inputs": { "text": prompt1, "clip": ["31", 1] }, "class_type": "CLIPTextEncode" },
            "7": { "inputs": { "text": job_input.get("neg", "blurry, low quality"), "clip": ["31", 1] }, "class_type": "CLIPTextEncode" },
            "8": { "inputs": { "samples": ["3", 0], "vae": ["17", 0] }, "class_type": "VAEDecode" },
            "9": { "inputs": { "filename_prefix": "ReelsImg_", "images": ["8", 0] }, "class_type": "SaveImage" },
            "13": { "inputs": { "width": 768, "height": 1344, "batch_size": 1 }, "class_type": "EmptyLatentImage" },
            "17": { "inputs": { "vae_name": "ae.safetensors" }, "class_type": "VAELoader" },
            "18": { "inputs": { "clip_name": "qwen_3_4b_fp8_mixed.safetensors", "type": "lumina2" }, "class_type": "CLIPLoader" },
            "31": { "inputs": { "value": {}, "model": ["43", 0], "clip": ["18", 0] }, "class_type": "Power Lora Loader (rgthree)" },
            "43": { "inputs": { "unet_name": "z_image/ARAZmixZIT019_bf16.safetensors", "weight_dtype": "default" }, "class_type": "UNETLoader" }
        }

        result_img = queue_prompt(img_workflow, client_id_img)
        wait_for_execution(result_img["prompt_id"], client_id_img)

        # Locate generated image
        output_dir = os.path.join(COMFY_DIR, "output")
        img_files = sorted(glob.glob(os.path.join(output_dir, "ReelsImg_*.png")), key=os.path.getmtime)
        if not img_files:
            raise RuntimeError("Image generation failed (no file found)")
        img_path = img_files[-1]
        log(f"Image generated: {img_path}")
        
        # Rename image to a fixed name in input for LTX-2
        img_input_name = "reels_base.png"
        img_input_path = os.path.join(COMFY_DIR, "input", img_input_name)
        os.makedirs(os.path.dirname(img_input_path), exist_ok=True)
        os.rename(img_path, img_input_path)

        # 2. Video Generation (LTX-2 pipeline)
        log("--- Stage 2: Generating Video ---")
        client_id_vid = str(uuid.uuid4())
        
        # ── STAGE 2: VIDEO WORKFLOW (LTX-2 IMAGE-TO-VIDEO) ──
        # IMPORTANT: Replace `vid_workflow` with your exported ComfyUI API JSON!
        # When creating the workflow in ComfyUI:
        # - Use a `LoadImage` node and point it to any dummy image file named exactly `reels_base.png`.
        # - Export the API JSON.
        # - Replace the dict below.
        
        vid_workflow = {
            "100": { "inputs": { "ckpt_name": "ltx-2-19b-distilled-fp8.safetensors" }, "class_type": "LTXVLoader" },
            "101": { "inputs": { "text": video_prompt, "clip": ["100", 1] }, "class_type": "CLIPTextEncode" },
            "102": { "inputs": { "text": "", "clip": ["100", 1] }, "class_type": "CLIPTextEncode" },
            "103": { "inputs": { "width": 576, "height": 1024, "length": 121, "batch_size": 1 }, "class_type": "LTXVModelConfigurator" },
            "104": { "inputs": { "image": img_input_name, "upload": "image" }, "class_type": "LoadImage" },
            "105": { "inputs": { "images": ["104", 0], "model_config": ["103", 0] }, "class_type": "LTXVImgToVideo" },
            "106": { "inputs": { "positive": ["101", 0], "negative": ["102", 0], "frame_rate": 24 }, "class_type": "LTXVConditioning" },
            "107": { "inputs": { "steps": 8, "max_shift": 0.95, "base_shift": 0.85, "stretch": True, "model": ["100", 0] }, "class_type": "LTXVScheduler" },
            "108": { "inputs": { "noise_seed": seed }, "class_type": "RandomNoise" },
            "109": { "inputs": { "sampler_name": "euler" }, "class_type": "KSamplerSelect" },
            "110": { "inputs": { "model": ["100", 0], "conditioning": ["106", 0] }, "class_type": "BasicGuider" },
            "111": { "inputs": { "noise": ["108", 0], "guider": ["110", 0], "sampler": ["109", 0], "sigmas": ["107", 0], "latent_image": ["105", 0] }, "class_type": "SamplerCustomAdvanced" },
            "112": { "inputs": { "samples": ["111", 0], "vae": ["100", 2] }, "class_type": "VAEDecode" },
            "113": { "inputs": { "frame_rate": 24, "loop_count": 0, "filename_prefix": "ReelsVid", "format": "video/h264-mp4", "save_output": True, "images": ["112", 0] }, "class_type": "VHS_VideoCombine" }
        }
        
        result_vid = queue_prompt(vid_workflow, client_id_vid)
        wait_for_execution(result_vid["prompt_id"], client_id_vid)

        vid_files = sorted(glob.glob(os.path.join(output_dir, "ReelsVid*.mp4")), key=os.path.getmtime)
        if not vid_files:
            raise RuntimeError("Video generation failed (no mp4 found)")
        vid_path = vid_files[-1]
        log(f"Video generated: {vid_path}")

        # 3. Cloudinary Upload
        log("--- Stage 3: Cloudinary Upload ---")
        vid_url = upload_to_cloudinary(vid_path, resource_type="video")

        # 4. Instagram Publish
        log("--- Stage 4: Instagram Publish ---")
        creation_id = create_ig_reels_container(vid_url, caption)
        wait_for_ig_container(creation_id)
        ig_resp = publish_ig_reels(creation_id)

        # Optional read data to return
        with open(vid_path, "rb") as f:
            b64_vid = base64.b64encode(f.read()).decode("utf-8")

        return {
            "success": True,
            "video_url": vid_url,
            "instagram_id": ig_resp.get("id"),
            #"video_base64": b64_vid  # Omit to save payload size if not needed
        }

    except Exception as e:
        log(f"Handler error: {traceback.format_exc()}")
        return {"error": str(e)}

# ── Main ─────────────────────────────────────────────────────
if __name__ == "__main__":
    if not hasattr(runpod, "serverless"):
        log("Running locally for debug...")
        # Local test logic...
    else:
        runpod.serverless.start({"handler": handler})
