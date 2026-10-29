# Generic runtime for Kaisen previews: downloads a project bundle at boot, installs its deps and runs it.
FROM node:22-bookworm-slim
ENV HOME=/home/app PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    npm_config_cache=/home/app/.npm npm_config_update_notifier=false PORT=8080
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
RUN mkdir -p /home/app /opt/kaisen && chown 10001:10001 /home/app
COPY boot.py /opt/kaisen/boot.py
WORKDIR /home/app
USER 10001
EXPOSE 8080
CMD ["python3", "-u", "/opt/kaisen/boot.py"]
