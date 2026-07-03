FROM python:3.11-alpine

WORKDIR /app

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Flask application script
COPY app.py .

# Expose port 13714
EXPOSE 13714

# Run with Gunicorn on port 13714
CMD ["gunicorn", "--bind", "0.0.0.0:13714", "--workers", "2", "--threads", "4", "app:app"]
