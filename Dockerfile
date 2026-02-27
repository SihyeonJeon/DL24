# =============================================================
# RunPod Serverless Worker
# Pipeline: Z-Image generation → WAN 2.2 I2V (LightX2V 4-step)
# GPU Target: A100 80GB
# =============================================================

FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV COMFY_DIR=/comfyui

# ── System dependencies ───────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl ffmpeg \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*


# ── Python 3.12 as default ────────────────────────────────────
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12

# ── PyTorch with CUDA 12.4 ──────────────────────────────────
RUN pip3 install --no-cache-dir \
    torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu124

# ── Clone latest ComfyUI ──────────────────────────────────────
RUN git clone https://github.com/Comfy-Org/ComfyUI.git ${COMFY_DIR}
WORKDIR ${COMFY_DIR}

# ── ComfyUI core dependencies ─────────────────────────────────
RUN pip3 install --no-cache-dir -r requirements.txt

# ── RunPod + handler dependencies ─────────────────────────────
RUN pip3 install --no-cache-dir \
    runpod \
    websocket-client \
    requests \
    Pillow \
    imageio[ffmpeg] \
    av \
    typing_extensions

# ─────────────────────────────────────────────────────────────
# Custom Nodes
# ─────────────────────────────────────────────────────────────

# [1] rgthree — SetNode/GetNode, Fast Groups Bypasser
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/rgthree/rgthree-comfy.git && \
    (cd rgthree-comfy && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [2] ComfyUI-Custom-Scripts (pysssss) — ShowText, Set/Get wire nodes
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git

# [3] ComfyUI-Easy-Use — easy int / easy utility nodes
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/yolain/ComfyUI-Easy-Use.git && \
    (cd ComfyUI-Easy-Use && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [4] ComfyUI_Comfyroll_CustomNodes — CR Text Concatenate / CR Prompt Text
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git

# [5] ComfyUI-KJNodes — ImageBatchExtendWithOverlap, SomethingToString
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/kijai/ComfyUI-KJNodes.git && \
    (cd ComfyUI-KJNodes && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [6] ComfyUI-VideoHelperSuite (VHS) — VHS_VideoCombine, video encode/save
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    (cd ComfyUI-VideoHelperSuite && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true)

# [7] ComfyUI-Wan22FMLF — WanAdvancedI2V (SVI mode 포함, I2V 핵심 노드)
#     Uses comfy_api.latest — requires ComfyUI >= 0.3.x (latest clone OK)
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/wallen0322/ComfyUI-Wan22FMLF.git && \
    (cd ComfyUI-Wan22FMLF && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true)
    
RUN cd ${COMFY_DIR}/custom_nodes && \
    git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git && \
    (cd ComfyUI-Frame-Interpolation && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true) && \
    (cd ComfyUI-Frame-Interpolation && python3 install.py || true)
    
# ── Config files ──────────────────────────────────────────────
COPY extra_model_paths.yaml ${COMFY_DIR}/extra_model_paths.yaml
COPY handler.py /handler.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

# ── Verify ────────────────────────────────────────────────────
RUN python3 -c "import torch; print(f'PyTorch {torch.__version__} | CUDA {torch.version.cuda}')"
RUN python3 -c "import imageio; print(f'imageio {imageio.__version__}')"

WORKDIR /
CMD ["/start.sh"]
