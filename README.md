# Sistema de Backup e Recuperação de Desastres oVirt/OLVM

Solução automatizada de backup e recuperação de desastres para ambientes de virtualização oVirt/OLVM usando Apache Airflow. Este sistema oferece backup automatizado de VMs, restauração e gerenciamento de certificados com suporte para backups incrementais.

## Pré-requisitos

- **Docker e Docker Compose** (única dependência necessária)
- **VM no Ambiente DR**: Sistema deve rodar dentro de uma VM no cluster DR
- **Disco de Backup Separado**: Disco dedicado para armazenamento de backup (1TB+ recomendado)
- **Acesso de Rede**: VM deve alcançar ambos os gerenciadores oVirt (Produção e DR)

## Arquitetura

O sistema opera com dois ambientes oVirt:
- **Produção**: Ambiente de origem para backups
- **Recuperação de Desastres (DR)**: Ambiente de destino para restaurações

### Componentes Principais
- **DAGs do Airflow**: Orquestram workflows de backup/restore
- **Integração SDK oVirt**: Chamadas diretas da API para engines oVirt
- **Processamento QCOW2**: Cadeias de backup incremental com qemu-img
- **Gerenciamento de Certificados**: Manuseio de certificados SSL para conexões oVirt

## Instalação

### 1. Configuração Inicial da VM

```bash
# Clonar repositório
git clone <url-do-repositorio>
cd replicacao-olvm

# Montar disco de backup (exemplo com /dev/sdc)
sudo mkfs.ext4 /dev/sdc
sudo mkdir -p /backup
sudo mount /dev/sdc /backup

# Adicionar ao fstab para persistência
echo "/dev/sdc /backup ext4 defaults 0 2" | sudo tee -a /etc/fstab

# Definir permissões corretas para diretório de backup
sudo chown -R 50000:50000 /backup
```

### 2. Configuração de Ambiente

Copie e edite o arquivo de ambiente:
```bash
cp .env_example .env
```

Edite `.env` com seus valores:
```bash
# Configuração do Airflow
FERNET_KEY=sua-chave-fernet-aqui          # Gerar com: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
JWT_SECRET=seu-jwt-secret-aqui            # Gerar com: openssl rand -hex 32
AIRFLOW_UID=50000

# IPs dos Ambientes oVirt
DR_MANAGER_IP=10.x.x.x                   # IP do Gerenciador oVirt DR
PRD_MANAGER_IP=10.x.x.x                  # IP do Gerenciador oVirt Produção

# Diretório de Backup - Caminho para disco de backup montado
BACKUP_DIR=/backup                       # Alterar se usar ponto de montagem diferente
```

### 3. Configuração oVirt

Copie e edite o arquivo de variáveis:
```bash
cp variables.example.json variables.json
```

Edite `variables.json` com suas configurações oVirt:
```json
{
  "source_ovirt_url": "https://manager.exemplo.com/ovirt-engine/api",
  "source_ovirt_user": "admin@internal",
  "source_ovirt_password": "sua-senha",
  "source_ovirt_cert_path": "/opt/airflow/certs/PRD/ca.pem",
  "backup_directory": "/opt/airflow/backups",
  "checkpoints_file": "/opt/airflow/config/checkpoints.json",

  "target_ovirt_url": "https://dr-manager.exemplo.com/ovirt-engine/api",
  "target_ovirt_user": "admin@internal",
  "target_ovirt_password": "sua-senha",
  "target_ovirt_cert_path": "/opt/airflow/certs/DR/ca.pem",

  "vms_config": {
    "nome-vm-1": {
      "backup_policy": "1111111",    # String de 7 bits: 1=backup, 0=pular (Seg-Dom)
      "retention_days": "7"
    },
    "nome-vm-2": {
      "backup_policy": "1000001",    # Apenas fins de semana
      "retention_days": "30"
    }
  }
}
```

#### Formato da Política de Backup
- **String de 7 bits**: Cada bit representa um dia da semana (Segunda-Domingo)
- **1**: Fazer backup neste dia
- **0**: Pular backup neste dia
- **Exemplos**:
  - `"1111111"`: Backups diários
  - `"1000001"`: Backups apenas nos fins de semana
  - `"1000000"`: Apenas segunda-feira

### 4. Configuração de Certificados

Os certificados SSL do oVirt serão obtidos automaticamente pelas DAGs de gerenciamento:
```bash
# Criar diretórios para certificados (serão populados automaticamente)
mkdir -p certs/PRD
mkdir -p certs/DR
```

**Importante**: Após iniciar os serviços, execute as DAGs de atualização de certificados:
- `atualizar_certificado_ovirt_producao`: Para obter certificados de produção
- `atualizar_certificado_ovirt_dr`: Para obter certificados DR

### 5. Iniciar Serviços

```bash
# Construir e iniciar todos os serviços
docker compose up --build -d

# Verificar status dos serviços
docker compose ps

# Importar variáveis para o Airflow
docker compose exec airflow-webserver airflow variables import /opt/airflow/variables.json
```

### 6. Configurar Certificados Iniciais

Após iniciar os serviços, execute as DAGs para baixar certificados automaticamente:

```bash
# Disparar DAG para certificados de produção
docker compose exec airflow-webserver airflow dags trigger atualizar_certificado_ovirt_producao

# Disparar DAG para certificados DR
docker compose exec airflow-webserver airflow dags trigger atualizar_certificado_ovirt_dr
```

### 7. Obter Senha do Admin

```bash
# Obter a senha gerada do admin
docker compose exec airflow-webserver cat /opt/airflow/simple_auth_manager_passwords.json.generated
```

Acesse a interface web em: **http://localhost:8080**

## Uso dos DAGs

### 1. ovirt_backup_vm
- **Propósito**: Backups diários automatizados de VMs
- **Agendamento**: 2:00 AM diariamente
- **Tipo**: Automático (agendado)
- **Função**: Faz backup das VMs de acordo com sua política de backup

### 2. ovirt_list_checkpoints
- **Propósito**: Listar pontos de backup disponíveis para uma VM
- **Agendamento**: Apenas disparo manual
- **Configuração**: `{"vm_name": "nome-da-sua-vm"}`
- **Uso**: Disparar manualmente para ver pontos de restauração disponíveis

### 3. ovirt_restore_specific_checkpoint
- **Propósito**: Restaurar VM para um checkpoint específico
- **Agendamento**: Apenas disparo manual
- **Configuração**: 
  ```json
  {
    "vm_name": "nome-da-sua-vm",
    "checkpoint_id": 3,
    "force_restore": false
  }
  ```
- **Uso**: Restaurar para ponto de backup específico

### 4. ovirt_restore_vm
- **Propósito**: Restaurar VM para o backup mais recente
- **Agendamento**: Apenas disparo manual
- **Configuração**: `{"vm_name": "nome-da-sua-vm"}`
- **Uso**: Restauração rápida para backup mais recente

### 5. DAGs de Gerenciamento de Certificados
- **atualizar_certificado_ovirt_producao**: Baixa e atualiza automaticamente certificados de produção
- **atualizar_certificado_ovirt_dr**: Baixa e atualiza automaticamente certificados DR
- **Agendamento**: Disparo manual (executar após instalação inicial e quando certificados expirarem)
- **Função**: Eliminam necessidade de configuração manual de certificados

## Comandos de Gerenciamento

### Gerenciamento de Serviços
```bash
# Ver logs
docker compose logs airflow-webserver
docker compose logs airflow-scheduler

# Reiniciar serviços
docker compose restart airflow-scheduler
docker compose restart airflow-webserver

# Parar todos os serviços
docker compose down

# Atualizar configuração
docker compose exec airflow-webserver airflow variables import /opt/airflow/variables.json
```

### Operações DAG
```bash
# Listar todos os DAGs
docker compose exec airflow-webserver airflow dags list

# Disparar DAG manualmente
docker compose exec airflow-webserver airflow dags trigger ovirt_backup_vm

# Ver execuções do DAG
docker compose exec airflow-webserver airflow dags list-runs -d ovirt_backup_vm

# Testar sintaxe do DAG
docker compose exec airflow-webserver airflow dags test ovirt_backup_vm 2025-01-01
```

## Solução de Problemas

### Limpeza de Processos Zumbi do oVirt

Se encontrar snapshots, discos ou VMs travados no oVirt, execute estes comandos no Engine oVirt:

```bash
# Destravar snapshots travados
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -q -t snapshot

# Destravar discos travados  
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -q -t disk

# Destravar VMs travadas
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -q -t vm

# Destravar todas as entidades
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -t all
```

### Problemas Comuns

#### 1. Permissão Negada no Diretório de Backup
```bash
# Corrigir permissões do diretório de backup
sudo chown -R 50000:50000 /caminho/para/diretorio/backup
```

#### 2. Problemas de Certificado
- Verificar se certificados estão nos diretórios corretos
- Verificar permissões dos arquivos de certificado
- Garantir que certificados não estão expirados

#### 3. Problemas de Conexão
- Verificar conectividade de rede para gerenciadores oVirt
- Verificar regras de firewall
- Validar credenciais oVirt em variables.json

#### 4. Problemas de Espaço em Disco
- Monitorar uso de espaço do diretório de backup
- Configurar políticas de retenção adequadamente
- Limpar backups antigos se necessário

#### 5. Erros de Importação DAG
```bash
# Verificar sintaxe do DAG
docker compose exec airflow-webserver python /opt/airflow/dags/nome_do_dag.py

# Atualizar DAGs
docker compose exec airflow-webserver airflow dags list-import-errors
```

### Localizações de Log
- **Logs do Airflow**: `/opt/airflow/logs/` (dentro do container)
- **Arquivos de Backup**: `${BACKUP_DIR}/` (caminho configurado)
- **Checkpoints**: `${BACKUP_DIR}/checkpoints.json`
- **Arquivos de Certificado**: `/opt/airflow/certs/`

## Estrutura de Armazenamento

```
${BACKUP_DIR}/
├── nome-vm-1/
│   ├── nome-vm-1_20250101_full.qcow2
│   ├── nome-vm-1_20250102_incremental.qcow2
│   └── ...
├── nome-vm-2/
│   └── ...
├── checkpoints.json
└── restore_logs/
```

## Notas de Segurança

- Manter credenciais oVirt seguras em variables.json
- Rotacionar certificados regularmente
- Monitorar acesso ao diretório de backup
- Usar segredos Fernet e JWT fortes
- Restringir acesso de rede apenas às portas necessárias

## Estratégia de Backup

O sistema suporta:
- **Backups Completos**: Imagens completas do disco da VM
- **Backups Incrementais**: Apenas blocos alterados desde o último backup
- **Cadeias de Backup**: Série de backups incrementais baseados em um backup completo
- **Múltiplas Cadeias**: Novo backup completo inicia uma nova cadeia
- **Políticas de Retenção**: Limpeza automática de backups antigos

Ao restaurar para um checkpoint específico, o sistema automaticamente inclui todos os arquivos necessários da cadeia de backup.