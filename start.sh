#!/bin/bash
set -e

echo "=============================================="
echo " Starting AI Model Reels v3 Worker"
echo "=============================================="

# 1. Start ComfyUI in background
echo "[start.sh] Launching ComfyUI..."
# The base image installs comfyui in /comfyui
cd /comfyui
python3 main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch --extra-model-paths-config /comfyui/extra_model_paths.yaml &
COMFY_PID=$!

# 2. Wait for ComfyUI to be ready
echo "[start.sh] Waiting for ComfyUI to allow connections..."
timeout=300
while ! curl -s http://127.0.0.1:8188/ > /dev/null; do
    timeout=$((timeout - 1))
    if [ $timeout -le 0 ]; then
        echo "[start.sh] ERROR: ComfyUI failed to start within 300 seconds."
        exit 1
    fi
    sleep 1
done
echo "[start.sh] ComfyUI is ready."

# 3. Start the RunPod Handler
echo "[start.sh] Starting RunPod Handler..."
python3 -u /handler.py

# If handler exits, kill ComfyUI
kill $COMFY_PID
