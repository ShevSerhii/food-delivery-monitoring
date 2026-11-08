FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements-common.txt .
RUN pip install --no-cache-dir -r requirements-common.txt

COPY common ./common
COPY generator/main.py .

EXPOSE 8000
CMD ["python", "main.py"]
