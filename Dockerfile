# Dockerfile
# Base image: Official RunPod ComfyUI worker (Pre-configured with ComfyUI + Torch)
FROM runpod/worker-comfyui:5.7.1-base

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# ── Install System Dependencies ────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# ── Install Custom Nodes ───────────────────────────────────────
WORKDIR /comfyui/custom_nodes

# 1. ComfyUI-LTXVideo (Official LTX-2 support)
RUN git clone https://github.com/Lightricks/ComfyUI-LTXVideo.git && \
    cd ComfyUI-LTXVideo && \
    pip install -r requirements.txt

# 2. ComfyUI-VideoHelperSuite (For VideoCombine / LoadVideo)
RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    cd ComfyUI-VideoHelperSuite && \
    pip install -r requirements.txt

# 3. ComfyUI_Comfyroll_CustomNodes (General utility)
RUN git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git

# 4. rgthree-comfy (Optimization/Muting)
RUN git clone https://github.com/rgthree/rgthree-comfy.git && \
    cd rgthree-comfy && \
    pip install -r requirements.txt

# ── Install Python Dependencies for Handler ────────────────────
RUN pip install --no-cache-dir requests websocket-client Pillow aiohttp runpod

# ── Copy Application Files ─────────────────────────────────────
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY handler.py /handler.py
COPY start.sh /start.sh

RUN chmod +x /start.sh

# ── Start ──────────────────────────────────────────────────────
# RunPod worker-comfyui base image usually uses a specific CMD.
# We override it to use our start script which launches ComfyUI + Handler.
CMD ["/start.sh"]
