FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app && useradd --system --gid app --create-home app
RUN pip install --no-cache-dir uv==0.12.23

WORKDIR /app
COPY pyproject.toml uv.lock README.md mcp-config.local.json ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

USER app
EXPOSE 8000
ENTRYPOINT []
CMD ["mineral-daily-agent", "--prompt", "给我生成一份关于 Pilbara 锂矿的今日简报"]
