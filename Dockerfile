# =============================================================
# RunPod Serverless Worker
# Pipeline: Z-Image generation → WAN 2.2 I2V (LightX2V 4-step)
# GPU Target: A100 80GB
# Base: runpod/pytorch 2.4.0 + Python 3.11 + CUDA 12.4.1
# =============================================================

FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV COMFY_DIR=/comfyui

# ── System dependencies ───────────────────────────────────────
# (python3/pip already provided by base image — no reinstall needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl ffmpeg \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Clone latest ComfyUI ──────────────────────────────────────
RUN git clone https://github.com/Comfy-Org/ComfyUI.git ${COMFY_DIR}
WORKDIR ${COMFY_DIR}

# ── ComfyUI core dependencies ─────────────────────────────────
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# ── RunPod + handler dependencies ─────────────────────────────
RUN python3 -m pip install --no-cache-dir \
    runpod \
    websocket-client \
    requests \
    Pillow \
    "imageio[ffmpeg]" \
    av \
    typing_extensions

# ─────────────────────────────────────────────────────────────
# Custom Nodes
# ─────────────────────────────────────────────────────────────

# [1] rgthree — SetNode/GetNode, Fast Groups Bypasser
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/rgthree/rgthree-comfy.git && \
    (cd rgthree-comfy && python3 -m pip install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [2] ComfyUI-Custom-Scripts (pysssss)
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git

# [3] ComfyUI-Easy-Use
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/yolain/ComfyUI-Easy-Use.git && \
    (cd ComfyUI-Easy-Use && python3 -m pip install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [4] ComfyUI_Comfyroll_CustomNodes
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git

# [5] ComfyUI-KJNodes
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/kijai/ComfyUI-KJNodes.git && \
    (cd ComfyUI-KJNodes && python3 -m pip install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [6] ComfyUI-VideoHelperSuite (VHS)
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    (cd ComfyUI-VideoHelperSuite && python3 -m pip install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [7] ComfyUI-Wan22FMLF — WanAdvancedI2V
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/wallen0322/ComfyUI-Wan22FMLF.git && \
    (cd ComfyUI-Wan22FMLF && python3 -m pip install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [8] ComfyUI-Frame-Interpolation
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git && \
    (cd ComfyUI-Frame-Interpolation && python3 -m pip install --no-cache-dir -r requirements.txt 2>/dev/null || true) && \
    (cd ComfyUI-Frame-Interpolation && python3 install.py || true)

# ── vfi_utils.py 패치: get_ckpt_container_path가 네트워크 볼륨을 먼저 탐색 ──
RUN python3 - << 'EOF'
import re, os

filepath = '/comfyui/custom_nodes/ComfyUI-Frame-Interpolation/vfi_utils.py'
with open(filepath) as f:
    src = f.read()

# 기존 함수를 찾아서 네트워크 볼륨 우선 탐색 로직으로 교체
old_pattern = r'def get_ckpt_container_path\(model_type\):[^\n]*\n\s+return[^\n]+'
new_func = '''def get_ckpt_container_path(model_type):
    import os as _os
    # RunPod 네트워크 볼륨 우선 탐색 (대소문자 모두 시도)
    nv_base = "/runpod-volume/models/vfi_models"
    for mt in [model_type, model_type.lower(), model_type.upper()]:
        candidate = _os.path.join(nv_base, mt)
        if _os.path.isdir(candidate):
            return candidate
    # flat 디렉토리에 파일이 바로 있는 경우
    if _os.path.isdir(nv_base):
        return nv_base
    # 원래 동작: 플러그인 내부 경로
    return _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "vfi_models", model_type)'''

patched = re.sub(old_pattern, new_func, src)
if patched == src:
    print("WARNING: pattern not found, appending override at top")
    # fallback: 함수 정의 전체를 파일 앞에 삽입
    src = src.replace(
        'def get_ckpt_container_path(model_type):',
        '# PATCHED\n' + new_func + '\nif False:\n    def get_ckpt_container_path(model_type):'
    )
    with open(filepath, 'w') as f:
        f.write(src)
else:
    with open(filepath, 'w') as f:
        f.write(patched)
    print("vfi_utils.py patched OK")
EOF

# ── Config files ──────────────────────────────────────────────
COPY extra_model_paths.yaml ${COMFY_DIR}/extra_model_paths.yaml
COPY handler.py /handler.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

# ── Verify ────────────────────────────────────────────────────
RUN python3 -c "import torch; print(f'PyTorch {torch.__version__} | CUDA {torch.version.cuda}')"
RUN python3 -c "import runpod; print(f'runpod {runpod.__version__}')"
RUN python3 -c "import imageio; print(f'imageio {imageio.__version__}')"

WORKDIR /
CMD ["/start.sh"]
