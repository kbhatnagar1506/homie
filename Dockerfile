FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium-headless-shell
COPY . .
ENV PYTHONUNBUFFERED=1 PORT=8080
# Hub (scoreboard, test site, listing pages, screenshots) on $PORT; all agents + Relay bridge alongside it.
CMD ["sh", "-c", "uvicorn hub.server:app --host 0.0.0.0 --port ${PORT} & python -m agents.run"]
