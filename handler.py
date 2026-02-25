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
EXECUTION_TIMEOUT = int(os.environ.get("EXECUTION_TIMEOUT", 1800))

# ── Cloudinary Config (Hardcoded) ────────────────────────────
CLOUDINARY_UPLOAD_PRESET = "n8n insta"
# NOTE: We use the unsigned upload URL for dp2azbanc
CLOUDINARY_CLOUD_NAME = "dp2azbanc"

def log(msg):
    print(f"[handler] {msg}", flush=True)

# ── ComfyUI Wrappers ─────────────────────────────────────────
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
                    if data["data"]["node"] is None and data["data"]["prompt_id"] == prompt_id:
                        log("Execution completed!")
                        return True
                elif data["type"] == "execution_error":
                    raise RuntimeError(f"ComfyUI Error: {data['data']}")
    finally:
        ws.close()

# ── Helpers ──────────────────────────────────────────────────
def upload_to_cloudinary_curl(file_path, resource_type="video"):
    """
    Uploads via curl to minimize dependencies. 
    Returns the 'secure_url'.
    """
    log(f"Uploading to Cloudinary: {file_path}")
    url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/{resource_type}/upload"
    
    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-F", f"upload_preset={CLOUDINARY_UPLOAD_PRESET}",
        "-F", f"file=@{file_path}"
    ]
    
    # Run curl
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Cloudinary upload failed: {res.stderr}")
    
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Invalid Cloudinary response: {res.stdout}")
        
    if "secure_url" not in data:
        raise RuntimeError(f"Cloudinary error: {data}")
        
    return data["secure_url"]

# ── Handler ──────────────────────────────────────────────────
def handler(event):
    try:
        job = event.get("input", {})
        prompt1 = job.get("prompt1")
        video_prompt = job.get("videoPrompt")
        seed = job.get("seed", 12345)
        
        if not prompt1 or not video_prompt:
            return {"error": "Missing 'prompt1' or 'videoPrompt'"}

        # 1. Image Generation
        log("--- Stage 1: Image Gen ---")
        client_id = str(uuid.uuid4())
        
        # INSERT EXPORTED IMAGE WORKFLOW HERE
        img_workflow = {
            "3": { "inputs": { "seed": seed, "steps": 25, "cfg": 3.5, "sampler_name": "euler", "scheduler": "simple", "denoise": 1, "model": ["31", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["13", 0] }, "class_type": "KSampler" },
            "6": { "inputs": { "text": prompt1, "clip": ["31", 1] }, "class_type": "CLIPTextEncode" },
            "7": { "inputs": { "text": job.get("neg", "(embedding:easynegative)"), "clip": ["31", 1] }, "class_type": "CLIPTextEncode" },
            "8": { "inputs": { "samples": ["3", 0], "vae": ["17", 0] }, "class_type": "VAEDecode" },
            "9": { "inputs": { "filename_prefix": "ReelsImg_", "images": ["8", 0] }, "class_type": "SaveImage" },
            "13": { "inputs": { "width": 768, "height": 1344, "batch_size": 1 }, "class_type": "EmptyLatentImage" },
            "17": { "inputs": { "vae_name": "ae.safetensors" }, "class_type": "VAELoader" },
            "18": { "inputs": { "clip_name": "qwen_3_4b_fp8_mixed.safetensors", "type": "lumina2" }, "class_type": "CLIPLoader" },
            "31": { "inputs": { "value": {}, "model": ["43", 0], "clip": ["18", 0] }, "class_type": "Power Lora Loader (rgthree)" },
            "43": { "inputs": { "unet_name": "z_image/ARAZmixZIT019_bf16.safetensors", "weight_dtype": "default" }, "class_type": "UNETLoader" }
        }
        
        res = queue_prompt(img_workflow, client_id)
        wait_for_execution(res["prompt_id"], client_id)
        
        # Move output to input
        out_dir = os.path.join(COMFY_DIR, "output")
        # Find latest png
        imgs = sorted(glob.glob(os.path.join(out_dir, "ReelsImg_*.png")), key=os.path.getmtime)
        if not imgs: raise RuntimeError("No image generated")
        
        last_img = imgs[-1]
        input_path = os.path.join(COMFY_DIR, "input", "reels_base.png")
        os.rename(last_img, input_path)
        log(f"Stage 1 Complete. Image moved to {input_path}")

        # 2. Video Generation
        log("--- Stage 2: Video Gen ---")
        
        # INSERT EXPORTED VIDEO WORKFLOW HERE
        vid_workflow = {
            "100": { "inputs": { "ckpt_name": "ltx-2-19b-distilled-fp8.safetensors" }, "class_type": "LTXVLoader" },
            "101": { "inputs": { "text": video_prompt, "clip": ["100", 1] }, "class_type": "CLIPTextEncode" },
            "102": { "inputs": { "text": "", "clip": ["100", 1] }, "class_type": "CLIPTextEncode" },
            "103": { "inputs": { "width": 576, "height": 1024, "length": 121, "batch_size": 1 }, "class_type": "LTXVModelConfigurator" },
            "104": { "inputs": { "image": "reels_base.png", "upload": "image" }, "class_type": "LoadImage" },
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

        res = queue_prompt(vid_workflow, client_id)
        wait_for_execution(res["prompt_id"], client_id)

        vids = sorted(glob.glob(os.path.join(out_dir, "ReelsVid*.mp4")), key=os.path.getmtime)
        if not vids: raise RuntimeError("No video generated")
        final_video = vids[-1]
        log(f"Stage 2 Complete. Video: {final_video}")

        # 3. Cloudinary Upload
        log("--- Stage 3: Cloudinary Upload ---")
        vid_url = upload_to_cloudinary_curl(final_video, "video")
        
        log(f"Job Complete. URL: {vid_url}")
        
        # Return URL to n8n so n8n can handle the slow Instagram posting
        return {
            "status": "success",
            "video_url": vid_url
        }

    except Exception as e:
        log(f"Error: {traceback.format_exc()}")
        return {"error": str(e)}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
