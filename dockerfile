# Use an official Python runtime as a parent image
FROM python:3.12-slim-bookworm

# Set the working directory in the container to /app
WORKDIR /app

# Add metadata to the image
LABEL maintainer='Mike Ryan'
LABEL github_repo='https://github.com/devsetgo/dsg'

# Install gcc, curl, and other dependencies
RUN apt-get update && apt-get -y install gcc wget gnupg unzip curl

# Install Google Chrome
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable

# Install ChromeDriver
RUN CHROME_DRIVER_VERSION=$(curl -sS chromedriver.storage.googleapis.com/LATEST_RELEASE) \
    && wget -O /tmp/chromedriver.zip https://chromedriver.storage.googleapis.com/$CHROME_DRIVER_VERSION/chromedriver_linux64.zip \
    && unzip /tmp/chromedriver.zip chromedriver -d /usr/local/bin/ \
    && rm /tmp/chromedriver.zip

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