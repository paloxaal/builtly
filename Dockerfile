FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    wget \
    libxml2-dev \
    libpcre2-dev \
    swig \
    python3-dev

# Install LibreDWG
RUN git clone https://github.com/LibreDWG/libredwg.git && \
    cd libredwg && \
    ./autogen.sh && \
    ./configure && \
    make && \
    make install

ENV PATH="/usr/local/bin:${PATH}"

COPY . .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

EXPOSE 8501

CMD ["streamlit", "run", "Builtly_AI.py", "--server.port=8501", "--server.address=0.0.0.0"]
