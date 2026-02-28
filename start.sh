#!/bin/bash
set -e

echo "=============================================="
echo " RunPod Worker — Z-Image + WAN 2.2 I2V"
echo " A100 80GB Build"
echo "=============================================="

# ── Python / GPU 환경 출력 ─────────────────────────────────────
echo "[startup] Python  : $(python3 --version 2>&1)"
echo "[startup] PyTorch : $(python3 -c 'import torch; print(torch.__version__)' 2>&1)"
echo "[startup] CUDA    : $(python3 -c 'import torch; print(torch.version.cuda)' 2>&1)"
echo "[startup] GPU     : $(python3 -c 'import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE")' 2>&1)"
echo "[startup] VRAM    : $(python3 -c '
import torch
if torch.cuda.is_available():
    gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f"{gb:.1f} GB")
else:
    print("N/A")
' 2>&1)"

# ── 네트워크 볼륨 ──────────────────────────────────────────────
echo ""
if [ -d "/runpod-volume/models" ]; then
    echo "[startup] ✓ Network volume mounted: /runpod-volume/models"
else
    echo "[startup] ✗ WARNING: /runpod-volume/models NOT found — all models missing!"
fi

# ── 모델 파일 체크 ─────────────────────────────────────────────
echo ""
echo "[startup] ── Model verification ──────────────────────────"

check_model() {
    local label="$1"
    local path="$2"
    if [ -f "$path" ]; then
        local size
        size=$(du -sh "$path" 2>/dev/null | cut -f1)
        echo "  ✓ [$label] $(basename $path)  ($size)"
    else
        echo "  ✗ [$label] MISSING: $path"
    fi
}

# ... 기존 코드 ...
BASE="/runpod-volume/models"
BAKED_BASE="/comfyui/models" # 구워진 모델들의 기준 경로 추가

echo ""
echo "  [Z-Image — image generation (from Volume)]"
check_model "UNET"    "$BASE/diffusion_models/z_image/ARAZmixZIT019_bf16.safetensors"
check_model "CLIP"    "$BASE/clip/qwen_3_4b_fp8_mixed.safetensors"
check_model "VAE"     "$BASE/vae/ae.safetensors"
check_model "LoRA"    "$BASE/loras/skin_texture_zit.safetensors"

echo ""
echo "  [WAN 2.2 I2V — video generation (from Docker & Volume)]"
# 도커에 구워진 모델들 경로 변경
check_model "High Noise"  "$BAKED_BASE/diffusion_models/wan22_i2vHighV21.safetensors"
check_model "Low Noise"   "$BAKED_BASE/diffusion_models/wan22_i2vLowV21.safetensors"
check_model "T5 encoder"  "$BAKED_BASE/text_encoders/umt5_xxl_fp16.safetensors"
check_model "CLIP Vision" "$BAKED_BASE/clip_vision/clip_vision_h.safetensors"
check_model "VAE"         "$BAKED_BASE/vae/Wan2.1_VAE.pth"

# LoRA 모델들은 여전히 볼륨에 있다고 가정
check_model "LoRA High"   "$BASE/loras/Wan_2_2_I2V_A14B_HIGH_lightx2v_4step_lora_v1030_rank_64_bf16.safetensors"
check_model "LoRA Low"    "$BASE/loras/Wan2.2-Lightning_I2V-A14B-4steps-lora_LOW_fp16.safetensors"


# ── extra_model_paths.yaml 확인 ───────────────────────────────
echo ""
echo "[startup] ── extra_model_paths.yaml ──────────────────────"
if [ -f "/comfyui/extra_model_paths.yaml" ]; then
    cat /comfyui/extra_model_paths.yaml
else
    echo "  ✗ WARNING: not found!"
fi

# ── Custom nodes 확인 ──────────────────────────────────────────
echo ""
echo "[startup] ── Custom nodes ────────────────────────────────"
REQUIRED_NODES=(
    "rgthree-comfy"
    "ComfyUI-Custom-Scripts"
    "ComfyUI-Easy-Use"
    "ComfyUI_Comfyroll_CustomNodes"
    "ComfyUI-KJNodes"
    "ComfyUI-VideoHelperSuite"
    "ComfyUI-Wan22FMLF"
    "ComfyUI-Frame-Interpolation"
)
for node in "${REQUIRED_NODES[@]}"; do
    if [ -d "/comfyui/custom_nodes/$node" ]; then
        echo "  ✓ $node"
    else
        echo "  ✗ MISSING: $node"
    fi
done

# ── ComfyUI import test ───────────────────────────────────────
echo ""
echo "[startup] ── ComfyUI import test ─────────────────────────"
python3 -c "
import sys
sys.path.insert(0, '/comfyui')
try:
    import main
    print('[startup] ComfyUI main.py: OK')
except SystemExit:
    print('[startup] ComfyUI main.py: OK (SystemExit normal at import)')
except Exception as e:
    print(f'[startup] ComfyUI import ERROR: {e}')
" 2>&1

# ── WAN22FMLF 노드 등록 확인 ──────────────────────────────────
echo ""
echo "[startup] ── Wan22FMLF node check ────────────────────────"
python3 -c "
import sys
sys.path.insert(0, '/comfyui')
try:
    sys.path.insert(0, '/comfyui/custom_nodes/ComfyUI-Wan22FMLF')
    from wan_advanced_i2v import WanAdvancedI2V
    print('[startup] WanAdvancedI2V: importable OK')
except Exception as e:
    print(f'[startup] WanAdvancedI2V import ERROR: {e}')
" 2>&1


# ── RIFE 모델 심볼릭 링크 ─────────────────────────────────────
echo "[startup] ── RIFE VFI model symlink ──────────────────────"
RIFE_SRC="/runpod-volume/models/vfi_models/rife49.pth"

# 플러그인이 실제로 탐색하는 경로 (대소문자 모두 커버)
RIFE_DST1="/comfyui/custom_nodes/ComfyUI-Frame-Interpolation/vfi_models/rife/rife49.pth"
RIFE_DST2="/comfyui/custom_nodes/ComfyUI-Frame-Interpolation/vfi_models/RIFE/rife49.pth"

mkdir -p "$(dirname "$RIFE_DST1")"
mkdir -p "$(dirname "$RIFE_DST2")"

if [ -f "$RIFE_SRC" ]; then
    ln -sf "$RIFE_SRC" "$RIFE_DST1"
    ln -sf "$RIFE_SRC" "$RIFE_DST2"
    echo "[startup] ✓ rife49.pth → $RIFE_DST1"
    echo "[startup] ✓ rife49.pth → $RIFE_DST2"
else
    echo "[startup] ✗ WARNING: $RIFE_SRC not found!"
fi

echo ""
echo "=============================================="
echo " Starting handler.py ..."
echo "=============================================="

exec python3 -u /handler.py
