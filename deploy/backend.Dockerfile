FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 使用国内镜像源降低服务器构建时的网络波动。
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g; s/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gcc \
        g++ \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml /app/backend/pyproject.toml

RUN pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && python -c "import tomllib; from pathlib import Path; project = tomllib.loads(Path('/app/backend/pyproject.toml').read_text()); print('\n'.join(project['project']['dependencies']))" > /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY backend/src /app/backend/src

RUN pip install --no-deps /app/backend -i https://pypi.tuna.tsinghua.edu.cn/simple

EXPOSE 8000

CMD ["uvicorn", "a_share_radar.main:app", "--app-dir", "/app/backend/src", "--host", "0.0.0.0", "--port", "8000"]
