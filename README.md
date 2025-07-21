# oVirt/OLVM Backup and Disaster Recovery System

Automated backup and disaster recovery solution for oVirt/OLVM virtualization environments using Apache Airflow. This system provides automated VM backup, restore, and certificate management for oVirt virtualization environments.

## Architecture

The system operates with two oVirt environments:
- **Production**: Source environment for backups (e.g., `manager.local`)
- **Disaster Recovery (DR)**: Target environment for restores (e.g., `dr-manager.local`)

### Key Components
- **Dynamic DAG Generation**: Template-based DAG creation using Jinja2
- **oVirt SDK Integration**: Direct API calls to oVirt engines
- **QCOW2 Processing**: Incremental backup chains with optimized qemu-img merging
- **Certificate Management**: SSL cert handling for oVirt connections
- **Checkpoint Management**: Backup chain tracking and validation

## Prerequisites

- **Docker and Docker Compose** (only dependency required)
- **VM in DR Environment**: System must run inside a VM in the DR cluster
- **Separate Backup Disk**: Dedicated disk for backup storage (1TB+ recommended)
- **Network Access**: VM must reach both oVirt managers (Production and DR)

## Installation

### 1. Initial VM Setup

```bash
# Clone repository
git clone <repository-url>
cd replicacao-olvm-joao

# Mount backup disk (example with /dev/sdc)
sudo mkfs.ext4 /dev/sdc
sudo mkdir -p /backup
sudo mount /dev/sdc /backup
sudo chown -R 50000:50000 /backup  # IMPORTANT: Use Airflow UID 50000

# Add to fstab for persistence
echo "/dev/sdc /backup ext4 defaults 0 2" | sudo tee -a /etc/fstab
```

### 2. Environment Configuration

Configure your environment variables:

```bash
# Backup Directory - Path to mounted backup disk
BACKUP_DIR=/backup

# Airflow Security
FERNET_KEY="your-fernet-key"          # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
JWT_SECRET="your-jwt-secret"          # Generate: openssl rand -hex 32
AIRFLOW_UID=50000

# oVirt Environment IPs
DR_MANAGER_IP="10.x.x.x"             # DR oVirt Manager IP
PRD_MANAGER_IP="10.x.x.x"            # Production oVirt Manager IP
```

### 3. oVirt Configuration

Copy and edit the configuration file:
```bash
cp variables.example.json variables.json
```

Configure your oVirt environments and VM backup policies in `variables.json`:

```json
{
  "source_ovirt_url": "https://manager.example.com/ovirt-engine/api",
  "source_ovirt_user": "admin@internal",
  "source_ovirt_password": "your-password",
  "source_ovirt_cert_path": "/opt/airflow/certs/PRD/ca.pem",
  "backup_directory": "/opt/airflow/backups",
  "checkpoints_file": "/opt/airflow/config/checkpoints.json",

  "target_ovirt_url": "https://dr-manager.example.com/ovirt-engine/api",
  "target_ovirt_user": "admin@internal",
  "target_ovirt_password": "your-password",
  "target_ovirt_cert_path": "/opt/airflow/certs/DR/ca.pem",

  "backup_settings": {
    "incremental_enabled": true,
    "max_incremental_chain_length": 10,
    "force_full_backup_days": 7,
    "default_backup_time": "02:00"
  },

  "restore_settings": {
    "target_cluster_name": "Default",
    "target_storage_domain_name": "storage-dr",
    "disk_interface": "SATA",
    "timeout_minutes": 60,
    "auto_start_restored_vm": false,
    "vm_name_suffix": "_restored",
    "replace_existing_vm": true,
    "backup_old_vm": true
  },

  "vms_config": {
    "VM_NAME": {
      "enabled": true,
      "schedule": {
        "type": "simple",
        "pattern": "daily",
        "time": "02:00"
      },
      "backup_type": {
        "incremental_enabled": true,
        "full_backup_frequency": "weekly",
        "full_backup_day": "sunday"
      },
      "retention": {
        "keep_daily": 7,
        "keep_weekly": 4,
        "keep_monthly": 3,
        "max_backup_age_days": 90
      },
      "auto_restore": true
    }
  }
}
```

## Backup Configuration

VMs are configured with flexible backup policies supporting both modern structured configuration and legacy formats:

### Modern Configuration Format

```json
{
  "VM_NAME": {
    "enabled": true,
    "schedule": {
      "type": "simple",           // simple, advanced, cron, custom (legacy)
      "pattern": "daily",         // daily, weekdays, weekends, weekly, biweekly, monthly
      "time": "02:00"
    },
    "backup_type": {
      "incremental_enabled": true,
      "full_backup_frequency": "weekly",
      "full_backup_day": "sunday"
    },
    "retention": {
      "keep_daily": 7,
      "keep_weekly": 4,
      "keep_monthly": 3,
      "max_backup_age_days": 90
    },
    "auto_restore": true
  }
}
```

### Legacy 7-bit Policy Format

```json
{
  "VM_NAME": {
    "enabled": true,
    "schedule": {
      "type": "custom",
      "pattern": "1010101",       // 7-bit string: 1=backup, 0=skip (Monday-Sunday)
      "time": "22:00"
    },
    "backup_type": {
      "incremental_enabled": true,
      "full_backup_frequency": "weekly",
      "full_backup_day": "monday"
    },
    "retention": {
      "keep_daily": 7,
      "keep_weekly": 4,
      "keep_monthly": 3,
      "max_backup_age_days": 90
    },
    "auto_restore": true
  }
}
```

### Schedule Types

- **simple**: Common patterns (daily, weekdays, weekends, weekly, biweekly, monthly)
- **advanced**: Complex monthly patterns (specific days, weeks)
- **cron**: Direct cron expressions
- **custom**: Traditional 7-bit string format

### 7-bit Pattern Examples
- `"1111111"`: Daily backups
- `"1000001"`: Weekend backups only (Monday + Sunday)
- `"1000000"`: Monday only
- `"0000010"`: Saturday only

### Retention Configuration

- **keep_daily**: Number of daily backups to retain
- **keep_weekly**: Number of weekly backups to retain
- **keep_monthly**: Number of monthly backups to retain
- **max_backup_age_days**: Maximum age in days before deletion

## Service Management

### Initial Deployment

```bash
# Build and start all services
docker compose up --build -d

# Check service status
docker compose ps

# Get admin password for web interface
docker compose exec airflow-webserver cat /opt/airflow/simple_auth_manager_passwords.json.generated
```

### Certificate Setup

```bash
# Certificate directories are created automatically
# After initial startup, run certificate DAGs to populate them:
docker compose exec airflow-webserver airflow dags trigger atualizar_certificado_ovirt_producao
docker compose exec airflow-webserver airflow dags trigger atualizar_certificado_ovirt_dr
```

### DAG Development

```bash
# Generate dynamic DAGs from templates
docker compose exec airflow-webserver airflow dags trigger generate_complete_dynamic_dags

# Test DAG syntax
docker compose exec airflow-webserver airflow dags list

# Trigger specific VM backup DAG
docker compose exec airflow-webserver airflow dags trigger backup_vm_name

# View DAG runs
docker compose exec airflow-webserver airflow dags list-runs -d backup_vm_name
```

### Configuration Management

```bash
# Import variables after changes to variables.json
docker compose exec airflow-webserver airflow variables import /opt/airflow/variables.json

# List current variables
docker compose exec airflow-webserver airflow variables list
```

## DAG Architecture

Each VM gets its own dynamically generated DAG with:

### Backup Tasks
1. `validate_environment` → Check oVirt connection and VM status
2. `get_vm_info` → Retrieve VM information and disk details
3. `check_vm_lock` → Ensure VM is not locked for operations
4. `create_vm_snapshot` → Create VM snapshot for consistent backup
5. `backup_vm_disks` → Backup VM disks (full or incremental)
6. `cleanup_snapshot` → Remove temporary snapshot

### Restore Tasks (if `auto_restore: true`)
1. `validate_restore_environment` → Check DR environment readiness
2. `detect_new_backups` → Identify new backups for restore
3. `process_vm_restores` → Execute restore operations

## Storage Structure

```
${BACKUP_DIR}/
├── vm-name-1/
│   ├── vm-name-1_20250101_full.qcow2
│   ├── vm-name-1_20250102_incremental.qcow2
│   └── ...
├── vm-name-2/
│   └── ...
├── checkpoints.json
└── restore_logs/
```

### Backup Directory Configuration

The backup directory is fully configurable and designed for a separate disk:
- **Environment Variable**: `BACKUP_DIR` in `.env` file (default: `/backup`)
- **Docker Volume**: `${BACKUP_DIR}:/opt/airflow/backups` in docker-compose.yaml
- **Airflow Variable**: `backup_directory` in variables.json
- **Recommended Setup**: Mount 1TB+ disk to `/backup` for adequate storage

## Restore Optimization

The system includes optimized restore functionality that merges incremental backup chains:

### Problem Solved
- **Before**: Restoring incremental chains created multiple full-sized disks (3x storage usage)
- **After**: Chains are merged into single optimized disk using `qemu-img` operations

### Implementation
- **Chain Merging**: `merge_backup_chain_complete()` in template merges incrementals
- **Storage Savings**: Compressed, optimized final disk vs. multiple separate disks
- **Process**: Copy base → rebase incrementals → commit changes → compress final result

## Troubleshooting

### oVirt Entity Lock Issues

If snapshots, disks, or VMs are locked in oVirt, run these commands on the oVirt Engine:

```bash
# Unlock locked snapshots
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -q -t snapshot

# Unlock locked disks
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -q -t disk

# Unlock locked VMs
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -q -t vm

# Unlock all entities
/usr/share/ovirt-engine/setup/dbutils/unlock_entity.sh -t all
```

### Common Issues

#### Permission Errors
```bash
# Fix backup directory permissions
sudo chown -R 50000:50000 /backup
```

#### DAG Import Errors
```bash
# Check DAG syntax
docker compose exec airflow-webserver python /opt/airflow/dags/dag_name.py

# List import errors
docker compose exec airflow-webserver airflow dags list-import-errors
```

#### Connection Issues
- Verify network connectivity to oVirt managers
- Check firewall rules
- Validate credentials in variables.json
- Ensure certificates are not expired

### Log Locations
- **Airflow logs**: `/logs/` directory mapped from container
- **DAG failures**: Check Airflow web UI → DAGs → specific run
- **oVirt connection issues**: Verify certificates in `/certs/` and variables.json
- **Backup failures**: Check disk space in backup directory

## Technical Notes

- Uses **LocalExecutor** (not CeleryExecutor) for simplicity
- **PostgreSQL** backend for Airflow metadata
- **Custom Dockerfile** installs oVirt SDK + imageio dependencies
- **Host networking** for oVirt manager access via extra_hosts
- **Dynamic DAG Generation** using Jinja2 templates
- **Optimized Restore Process** with incremental chain merging to reduce storage usage
- Web interface accessible at **http://localhost:8080**

## Security

- Keep oVirt credentials secure in variables.json
- Rotate certificates regularly
- Monitor backup directory access
- Use strong Fernet and JWT secrets
- Restrict network access to necessary ports only

## Backup Strategy

The system supports:
- **Full Backups**: Complete VM disk images
- **Incremental Backups**: Only changed blocks since last backup
- **Backup Chains**: Series of incremental backups based on a full backup
- **Multiple Chains**: New full backup starts a new chain
- **Retention Policies**: Automatic cleanup of old backups

When restoring to a specific checkpoint, the system automatically includes all necessary files from the backup chain.