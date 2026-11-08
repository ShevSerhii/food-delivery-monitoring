FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY auth/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY auth/ .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
