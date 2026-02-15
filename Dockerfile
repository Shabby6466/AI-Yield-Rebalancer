
# AI Yield Rebalancer - Unified Dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    build-essential \
    libpq-dev \
    postgresql-client \
    lsof \
    && rm -rf /var/lib/apt/lists/*

# Install Foundry (Anvil/Forge) for the Simulation Lab
# We add a retry loop because foundryup can be flaky on server networks
RUN curl -L https://foundry.paradigm.xyz | bash && \
    export PATH="/root/.foundry/bin:${PATH}" && \
    (foundryup || foundryup || foundryup)
ENV PATH="/root/.foundry/bin:${PATH}"

# Set working directory
WORKDIR /app

# Copy requirement files and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Set PYTHONPATH so 'src' can be imported from /app
ENV PYTHONPATH=/app

# Copy source code
COPY . .

# Ensure data directory exists and has correct permissions
RUN mkdir -p data/db data/logs contracts
RUN chmod -R 777 data contracts

# Expose ports
EXPOSE 8501 8000 8545 5432

# Default command
CMD ["streamlit", "run", "dashboard/app.py"]
