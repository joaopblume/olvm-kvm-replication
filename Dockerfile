FROM apache/airflow:3.0.2

USER root

# Instalar dependências de sistema necessárias para oVirt SDK e qemu-img
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libxml2-dev \
    libxslt-dev \
    libcurl4-openssl-dev \
    qemu-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# Copiar e instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Verificar instalação das dependências críticas
RUN python -c "import ovirtsdk4; print('✓ oVirt SDK instalado!')"
RUN python -c "from ovirt_imageio import client; print('✓ oVirt Imageio instalado!')"