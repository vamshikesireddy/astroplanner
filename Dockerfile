FROM python:3.9-slim

# 1. Install system dependencies
# We need chromium and chromium-driver for Selenium, and build-essential for some python packages
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 2. Create a non-root user and switch to it
# This prevents the container from running as root (Security Best Practice)
RUN useradd -m -u 1000 appuser
USER appuser
ENV PATH="/home/appuser/.local/bin:$PATH"

# 3. Set up working directory
WORKDIR /app

# 4. Copy and install Python requirements
# --chown ensures the non-root user owns the files
COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the application code
COPY --chown=appuser:appuser . .

# 6. Expose the port Streamlit runs on
EXPOSE 8501

# 7. Command to run the app
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]