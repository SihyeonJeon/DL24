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

# ── vfi_utils.py URL 패치 ─────────────────────────────────────
RUN sed -i \
    's|BASE_MODEL_DOWNLOAD_URLS = \[.*\]|BASE_MODEL_DOWNLOAD_URLS = ["https://huggingface.co/Isi99999/Frame_Interpolation_Models/resolve/main/"]|' \
    ${COMFY_DIR}/custom_nodes/ComfyUI-Frame-Interpolation/vfi_utils.py && \
    grep "BASE_MODEL_DOWNLOAD_URLS" ${COMFY_DIR}/custom_nodes/ComfyUI-Frame-Interpolation/vfi_utils.py

# ── Config files ──────────────────────────────────────────────
COPY extra_model_paths.yaml ${COMFY_DIR}/extra_model_paths.yaml
COPY handler.py /handler.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

# ── Bake Models into Docker Image ─────────────────────────────
# 1. 모델 디렉토리 생성
RUN mkdir -p ${COMFY_DIR}/models/diffusion_models \
             ${COMFY_DIR}/models/vae \
             ${COMFY_DIR}/models/clip_vision \
             ${COMFY_DIR}/models/text_encoders

# 2. WAN 2.2 모델 다운로드 (Civitai API Key 포함)
RUN wget -q -O ${COMFY_DIR}/models/diffusion_models/wan22_i2vHighV21.safetensors "https://civitai.com/api/download/models/2567410?type=Model&format=SafeTensor&size=pruned&fp=fp8&token=e5e3f0cd37b9bd27cdac2a5bd76d9c1c" && \
    wget -q -O ${COMFY_DIR}/models/diffusion_models/wan22_i2vLowV21.safetensors "https://civitai.com/api/download/models/2567309?type=Model&format=SafeTensor&size=pruned&fp=fp8&token=e5e3f0cd37b9bd27cdac2a5bd76d9c1c"

# 3. VAE 다운로드
RUN wget -q -O ${COMFY_DIR}/models/vae/Wan2.1_VAE.pth "https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B/resolve/main/Wan2.1_VAE.pth"

# 4. CLIP Vision 다운로드
RUN wget -q -O ${COMFY_DIR}/models/clip_vision/clip_vision_h.safetensors "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors"

# 5. Text Encoder 다운로드
RUN wget -q -O ${COMFY_DIR}/models/text_encoders/umt5_xxl_fp16.safetensors "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp16.safetensors"

# ── Verify ────────────────────────────────────────────────────
RUN python3 -c "import torch; print(f'PyTorch {torch.__version__} | CUDA {torch.version.cuda}')"
RUN python3 -c "import runpod; print(f'runpod {runpod.__version__}')"
RUN python3 -c "import imageio; print(f'imageio {imageio.__version__}')"

WORKDIR /
CMD ["/start.sh"]
