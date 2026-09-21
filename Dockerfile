# CP-Agent MCP Server — 容器化部署
# 镜像内：python:3.11-slim + g++（Linux g++ 自带 bits/stdc++.h）+ cp-agent + mcp SDK
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# 编译器与证书（cp-agent 出题需本地编译 C++ 标程/生成器/校验器）
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/cp-agent

# 先把依赖清单相关文件复制进来利用层缓存，再装依赖
COPY pyproject.toml ./
COPY config.yaml.example ./

# 安装到独立 venv，pip 走默认 PyPI（容器环境无本机坏镜像问题）
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -e . "mcp<2"

# 复制完整源码（含 testlib.h / templates / problem_db / mcp_server.py）
COPY . /app/cp-agent

# 从样例生成默认配置（运行时可用 DEEPSEEK_API_KEY 注入 key）
RUN if [ ! -f config.yaml ]; then cp config.yaml.example config.yaml; fi

ENV PATH="/opt/venv/bin:$PATH" \
    CP_AGENT_CONFIG=/app/cp-agent/config.yaml

EXPOSE 8000

# 默认 streamable-http（端点 /mcp，与 WorkBuddy 的 type:http 连接器对齐）
# 如需 SSE（端点 /sse）则改为 "sse"
CMD ["python", "mcp_server.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
