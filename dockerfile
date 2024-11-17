# Use an official Python runtime as a parent image
FROM python:3.12-slim-bookworm

# Set the working directory in the container to /app
WORKDIR /app

# Add metadata to the image
LABEL maintainer='Mike Ryan'
LABEL github_repo='https://github.com/devsetgo/mrie'


# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --upgrade pip setuptools
RUN pip install --no-cache-dir -r requirements/prd.txt

# Create a user and set file permissions
RUN useradd -m -r mrieUser && chown -R mrieUser /app

# Switch to the new user
USER mrieUser

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Run the command to start ASGI server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "4"]
# CMD ["granian", "--interface", "asgi", "src.main:app", "--host","0.0.0.0", "--port", "5000", "--workers", "4", "--threads", "2", "--http", "1", "--log-level", "debug"]