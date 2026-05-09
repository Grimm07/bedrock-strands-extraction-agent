FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

# uv is not in the upstream image; install once at build-time so subsequent
# `make install` calls are fast.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && chmod 755 /usr/local/bin/uv

USER vscode
