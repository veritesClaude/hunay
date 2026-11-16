FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py admin.py ./
ENV DB_PATH=/data/mailbot.db
VOLUME /data
CMD ["python", "bot.py"]
