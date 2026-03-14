FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    wget \
    curl \
    autoconf \
    automake \
    libtool \
    pkg-config \
    bison \
    flex \
    swig \
    python3-dev \
    libxml2-dev \
    libpcre2-dev \
    ca-certificates \
    patch \
 && rm -rf /var/lib/apt/lists/*

# Build and install LibreDWG (provides dwgread / dwg2dxf)
RUN git clone --depth=1 https://github.com/LibreDWG/libredwg.git /tmp/libredwg && \
    cd /tmp/libredwg && \
    ./autogen.sh && \
    ./configure --prefix=/usr/local --disable-docs --disable-bindings && \
    make -j"$(nproc)" && \
    make install && \
    ldconfig && \
    dwgread --version && \
    rm -rf /tmp/libredwg

ENV PATH="/usr/local/bin:${PATH}"

COPY . /app

RUN pip install --upgrade pip && \
    pip install -r requirements.txt

EXPOSE 8501

CMD ["streamlit", "run", "Builtly_AI.py", "--server.port=8501", "--server.address=0.0.0.0"]
