# Use an official lightweight Python image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install 'procps' which contains the 'ps' command needed for health checks.
RUN apt-get update && apt-get install -y procps git --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your source code into the container
COPY . .

# Download model files for all plugins (including Silero VAD) during build time
# This uses the official LiveKit CLI command: python main.py download-files
# The model is bundled in the package, but this ensures it's ready to use
RUN python main.py download-files

# Command to run your application when the container starts
CMD ["python", "main.py", "start"]


# # Use an official lightweight Python image
# FROM python:3.11-slim

# # Set the working directory inside the container
# WORKDIR /app

# # Install 'procps' which contains the 'ps' command needed for health checks.
# RUN apt-get update && apt-get install -y procps git --no-install-recommends && rm -rf /var/lib/apt/lists/*

# # Copy and install dependencies first to leverage Docker layer caching
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# # Download Silero model weights once at build time
# # RUN python main.py download-files

# # Copy all your source code into the container
# COPY . .

# # Command to run your application when the container starts
# CMD ["python", "keda_bridge.py"]