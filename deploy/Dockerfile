FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite data volume
VOLUME ["/app/data"]
ENV DATABASE_PATH=/app/data/grimhaven.db

# Default: Telegram bot in long-polling mode (works behind NAT, no public URL needed).
# For the web demo console instead: docker run … python run_demo.py
CMD ["python", "run_bot.py"]
