# Use the official Anaconda image as the base image
FROM continuumio/anaconda3

# Set the working directory
WORKDIR /app

# Copy your Python script and other necessary files into the container
COPY *.py /app
COPY entrypoint.sh /app
COPY rfp_scraper_config.json /app
COPY environment.yml /app

# Update packages and install any additional dependencies
RUN apt-get update && \
    apt-get install -y unzip wget libmagic1 libmagic-dev gnupg xvfb && \
    conda env create -f environment.yml && \
    echo "conda activate bids" >> ~/.bashrc && \
    . ~/.bashrc

# Install Google Chrome and ChromeDriver for Selenium
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo 'deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google.list && \
    apt update && \
    apt install -y google-chrome-stable

# Set the entrypoint to run your script
ENTRYPOINT ["bash", "entrypoint.sh"]

