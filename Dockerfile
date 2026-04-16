FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HCM_DB_PATH=kb/hcm_kb.sqlite

CMD ["python", "tools/hcm_slackbot.py"]
