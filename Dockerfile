FROM python:3.11-slim

WORKDIR /app

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8

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
    locales \
    tesseract-ocr \
    tesseract-ocr-nor \
    tesseract-ocr-eng \
    poppler-utils \
 && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
 && locale-gen \
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

# Streamlit config — upload size, headless, CORS
RUN mkdir -p /root/.streamlit && \
    printf '[server]\nmaxUploadSize = 200\nmaxMessageSize = 200\nenableCORS = false\nenableXsrfProtection = false\nheadless = true\n\n[browser]\ngatherUsageStats = false\n' > /root/.streamlit/config.toml

RUN pip install --upgrade pip && \
    pip install -r requirements.txt

EXPOSE 8501

CMD ["streamlit", "run", "Builtly_AI_frontpage_access_gate_expanded.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.maxUploadSize=200", "--server.maxMessageSize=200", "--server.enableXsrfProtection=false"]
