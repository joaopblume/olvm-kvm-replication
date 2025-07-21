from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable
import ovirtsdk4 as sdk
import ovirtsdk4.types as types
import logging

# DAG Configuration
default_args = {
    'owner': 'ovirt-admin',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
}

dag = DAG(
    'finaliza_status_backup_vm',
    default_args=default_args,
    description='Finalize backup for a specific VM',
    schedule=None,  # Manual trigger only
    catchup=False,
    max_active_runs=1,
    tags=['util', 'manual', 'source']
)

def create_ovirt_connection():
    """Create connection to oVirt Engine"""
    config = {
        'url': Variable.get('source_ovirt_url'),
        'user': Variable.get('source_ovirt_user'),
        'passwd': Variable.get('source_ovirt_password'),
        'certificate': Variable.get('source_ovirt_cert_path')
    }
    
    return sdk.Connection(
        url=config['url'],
        username=config['user'],
        password=config['passwd'],
        ca_file=config['certificate'],
        log=logging.getLogger(),
        debug=False,
        timeout=30
    )

def get_backup(connection, vm_id):
    """Get active backup for VM"""
    system_service = connection.system_service()
    vms_service = system_service.vms_service()
    backups_service = vms_service.vm_service(id=vm_id).backups_service()
    
    # Get all backups for this VM
    backups = backups_service.list()
    
    logging.info(f"Found {len(backups)} backup(s) for VM {vm_id}")
    
    # Find active backup (check all possible phases)
    for backup in backups:
        logging.info(f"Backup {backup.id}: Phase={backup.phase}")
        # Common active phases: READY, INITIALIZING, STARTING
        if backup.phase in [types.BackupPhase.READY, types.BackupPhase.INITIALIZING, types.BackupPhase.STARTING]:
            logging.info(f"Found active backup: {backup.id} (Phase: {backup.phase})")
            return backup
    
    # If no active backup found, get the most recent one
    if backups:
        latest_backup = backups[-1]  # Usually the most recent
        logging.info(f"No active backup found, using latest: {latest_backup.id} (Phase: {latest_backup.phase})")
        return latest_backup
    
    raise RuntimeError(f"No backup found for VM {vm_id}")

def finalize_backup(connection, vm_id):
    """Finalize backup for VM"""
    bkp = get_backup(connection, vm_id)
    system_service = connection.system_service()
    vms_service = system_service.vms_service()
    backups_service = vms_service.vm_service(id=vm_id).backups_service()
    backups_service.backup_service(id=bkp.id).finalize()
    logging.info(f"✓ Backup {bkp.id} finalized for VM {vm_id}")

def finalize_vm_backup(**context):
    """Main function to finalize backup for specified VM"""
    # Get VM name from DAG run configuration
    vm_name = context['dag_run'].conf.get('vm_name') if context['dag_run'].conf else None
    
    if not vm_name:
        raise ValueError("VM name must be provided in DAG run configuration")
    
    logging.info(f"Finalizing backup for VM: {vm_name}")
    
    connection = create_ovirt_connection()
    
    try:
        # Find VM by name
        vms_service = connection.system_service().vms_service()
        vms = vms_service.list(search=f'name={vm_name}')
        
        if not vms:
            raise RuntimeError(f"VM '{vm_name}' not found")
        
        vm = vms[0]
        vm_id = vm.id
        
        logging.info(f"Found VM: {vm.name} (ID: {vm_id})")
        
        # Finalize backup
        finalize_backup(connection, vm_id)
        
        logging.info(f"✓ Backup finalization completed for VM '{vm_name}'")
        
        return {
            'vm_name': vm_name,
            'vm_id': vm_id,
            'status': 'completed',
            'finalized_at': datetime.now().isoformat()
        }
        
    finally:
        connection.close()

# Define task
finalize_task = PythonOperator(
    task_id='finalize_vm_backup',
    python_callable=finalize_vm_backup,
    dag=dag
)

finalize_task