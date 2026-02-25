#!/bin/bash
set -e

echo "=============================================="
echo " AI Model Reels — Unified Worker (Z-Image + LTX-2)"
echo "=============================================="

echo "[startup] Python: $(python3 --version 2>&1)"
echo "[startup] PyTorch: $(python3 -c 'import torch; print(torch.__version__)' 2>&1)"
echo "[startup] CUDA: $(python3 -c 'import torch; print(torch.version.cuda)' 2>&1)"
echo "[startup] GPU: $(python3 -c 'import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "None")' 2>&1)"
echo "[startup] VRAM: $(python3 -c 'import torch; print(f"{torch.cuda.get_device_properties(0).total_mem/1024**3:.1f}GB") if torch.cuda.is_available() else print("N/A")' 2>&1)"

# Check network volume
if [ -d "/runpod-volume/models" ]; then
    echo "[startup] Network volume: MOUNTED"
    for dir in diffusion_models clip vae checkpoints loras latent_upscale_models; do
        if [ -d "/runpod-volume/models/$dir" ]; then
            count=$(find "/runpod-volume/models/$dir" -name "*.safetensors" 2>/dev/null | wc -l)
            echo "  $dir/: ${count} safetensors"
        fi
    done
else
    echo "[startup] WARNING: No network volume at /runpod-volume"
fi

echo "=============================================="
echo " Starting handler..."
echo "=============================================="

exec python3 -u /handler.py
