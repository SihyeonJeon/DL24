#!/bin/bash
set -e

echo "=============================================="
echo " AI Model Reels — Unified Worker (Z-Image + LTX-2)"
echo "=============================================="

echo "[startup] Python: $(python3 --version 2>&1)"
echo "[startup] PyTorch: $(python3 -c 'import torch; print(torch.__version__)' 2>&1)"
echo "[startup] CUDA: $(python3 -c 'import torch; print(torch.version.cuda)' 2>&1)"
echo "[startup] GPU: $(python3 -c 'import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "None")' 2>&1)"
echo "[startup] VRAM: $(python3 -c 'import torch; print(f"{torch.cuda.get_device_properties(0).total_memory/1024**3:.1f}GB") if torch.cuda.is_available() else print("N/A")' 2>&1)"

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


# start.sh에 추가 (ComfyUI 실행 전)
echo "[startup] Checking LTXV nodes..."
python3 -c "
import sys
sys.path.insert(0, '/comfyui')
try:
    from comfy_extras import nodes_ltxv
    print('[startup] nodes_ltxv loaded OK')
    print([x for x in dir(nodes_ltxv) if 'Upscale' in x])
except Exception as e:
    print(f'[startup] nodes_ltxv FAILED: {e}')
"

echo "=============================================="
echo " Starting handler..."
echo "=============================================="

exec python3 -u /handler.py
