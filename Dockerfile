FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

# System packages needed for Streamlit + building LibreDWG
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    wget \
    git \
    build-essential \
    pkg-config \
    autoconf \
    automake \
    libtool \
    bison \
    flex \
    m4 \
    swig \
    python3-dev \
    libxml2-dev \
    libpcre2-dev \
    zlib1g-dev \
    xz-utils \
 && rm -rf /var/lib/apt/lists/*

# Build and install LibreDWG (provides dwgread)
RUN git clone --depth=1 https://github.com/LibreDWG/libredwg.git /tmp/libredwg && \
    cd /tmp/libredwg && \
    ./autogen.sh && \
    ./configure --prefix=/usr/local && \
    make -j"$(nproc)" && \
    make install && \
    ldconfig && \
    dwgread --version && \
    rm -rf /tmp/libredwg

# Copy requirements first for better Docker layer caching
COPY requirements.txt /app/requirements.txt

# Install Python deps
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /app/requirements.txt

# Copy the rest of the app
COPY . /app

EXPOSE 8501

# Starts the main Streamlit app
CMD ["streamlit", "run", "Builtly_AI.py", "--server.port=8501", "--server.address=0.0.0.0"]
