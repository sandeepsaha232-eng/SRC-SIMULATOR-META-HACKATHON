FROM python:3.11-slim

# Set up a new user named "user" with user ID 1000 for HF Spaces
RUN useradd -m -u 1000 user

# Switch to the "user" user
USER user

# Set home to the user's home directory
ENV HOME=/home/user \
	PATH=/home/user/.local/bin:$PATH

# Set the working directory to the user's home directory
WORKDIR $HOME/app

# Copy requirements and install
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=user . .

# Expose port for HF Spaces
EXPOSE 7860

# Health check (matches OpenEnv spec)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')" || exit 1

# Run the server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
