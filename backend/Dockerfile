FROM python
ENV PYTHONUNBUFFERED True
ENV PORT 8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py .
CMD exec uvicorn api:app --host 0.0.0.0 --port ${PORT}