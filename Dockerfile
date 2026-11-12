FROM node:20-bookworm-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000 DATA_DIR=/tmp/nuel-ai-data WHATSAPP_AUTH_DIR=/tmp/nuel-ai-auth
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY package.json ./
RUN npm install --omit=dev
COPY app.py ./
COPY bridge ./bridge
EXPOSE 10000
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","10000"]
