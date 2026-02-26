# =============================================================
# RunPod Serverless Worker — Z-Image + LTX-2 Unified (A100)
# Single endpoint: image generation + video generation + batch
# =============================================================

FROM runpod/worker-comfyui:5.7.1-base

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# ── System Dependencies ──────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsm6 libxext6 curl wget \
    && rm -rf /var/lib/apt/lists/*

# ── Update ComfyUI to Latest ────────────────────────────────
RUN cd /comfyui && \
    git config --global pull.rebase false && \
    git fetch --all && \
    git reset --hard origin/master && \
    pip install --no-cache-dir -r requirements.txt

# ── Custom Nodes ─────────────────────────────────────────────
WORKDIR /comfyui/custom_nodes

# Z-Image needs: rgthree (Power Lora Loader)
RUN git clone https://github.com/rgthree/rgthree-comfy.git && \
    cd rgthree-comfy && \
    pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

# LTX-2 needs: ComfyUI-LTXVideo
RUN cd /comfyui/custom_nodes && \
    rm -rf ComfyUI-LTXVideo && \
    git clone https://github.com/Lightricks/ComfyUI-LTXVideo.git && \
    cd ComfyUI-LTXVideo && \
    pip install -r requirements.txt --break-system-packages 2>/dev/null || true

# LTX-2 needs: VideoHelperSuite (VHS_VideoCombine)
RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    cd ComfyUI-VideoHelperSuite && \
    pip install --no-cache-dir -r requirements.txt

# Shared utility nodes
RUN git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git

# ── Python Dependencies ──────────────────────────────────────
RUN pip install --no-cache-dir requests websocket-client Pillow aiohttp

# ── Copy Application Files ───────────────────────────────────
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY handler.py /handler.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

WORKDIR /
ENTRYPOINT ["/start.sh"]
CMD []
