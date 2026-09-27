# Base image with Python 3.11 slim
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies required for psycopg and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY requirements/ /app/requirements/

# Install python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements/development.txt

# Copy project files
COPY . /app/

# Expose Django development port
EXPOSE 8000

# Default command for web service
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
