# Use an official Python runtime
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy files
COPY . /app

# Install dependencies
RUN pip install --upgrade pip && \
    pip install --default-timeout=100 --no-cache-dir -r requirements.txt
    
# Expose the internal port (Container listens on 8000)
EXPOSE 8000

# Run the server
CMD ["python", "server.py"]