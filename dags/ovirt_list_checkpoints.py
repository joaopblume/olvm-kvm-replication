from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable
import logging
import json
from checkpoint_manager import CheckpointManager

# Configurações do DAG
default_args = {
    'owner': 'ovirt-checkpoints',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'lista_checkpoints',
    default_args=default_args,
    description='List all available checkpoints for a specific VM',
    schedule=None,  # Manual trigger only
    catchup=False,
    max_active_runs=1,
    tags=['checkpoints', 'list', 'manual']
)

BACKUP_DIR = Variable.get('backup_directory')

def list_vm_checkpoints(**context):
    """List all available checkpoints for a VM with datetime and friendly names"""
    # Get VM name from DAG run configuration
    vm_name = context['dag_run'].conf.get('vm_name') if context['dag_run'].conf else None
    
    if not vm_name:
        raise ValueError('VM name must be provided in DAG run configuration: {"vm_name": "your_vm_name"}')
    
    logging.info(f'Listing checkpoints for VM: {vm_name}')
    
    # Initialize checkpoint manager
    checkpoint_manager = CheckpointManager(BACKUP_DIR)
    
    # Get checkpoint chain
    checkpoint_chain = checkpoint_manager.get_checkpoint_chain(vm_name)
    
    if not checkpoint_chain:
        logging.info(f'No checkpoints found for VM: {vm_name}')
        return {
            'vm_name': vm_name,
            'checkpoints': [],
            'total_checkpoints': 0,
            'message': f'No checkpoints found for VM {vm_name}'
        }
    
    # Sort checkpoints by backup date for chronological display
    checkpoint_chain.sort(key=lambda x: x['backup_date'])
    
    # Format checkpoints for display
    formatted_checkpoints = []
    
    for checkpoint in checkpoint_chain:
        # Parse datetime
        backup_date = datetime.fromisoformat(checkpoint['backup_date'])
        friendly_date = backup_date.strftime('%Y-%m-%d %H:%M:%S')
        
        # Create friendly name
        backup_type = checkpoint['backup_type'].upper()
        chain_level = checkpoint['chain_level']
        
        if backup_type == 'FULL':
            friendly_name = f"FULL BACKUP - {friendly_date}"
        else:
            friendly_name = f"INCREMENTAL #{chain_level} - {friendly_date}"
        
        # File size in MB
        file_size_mb = checkpoint.get('file_size', 0) / (1024 * 1024)
        
        formatted_checkpoint = {
            'id': checkpoint['id'],
            'checkpoint_id': checkpoint.get('checkpoint_id'),
            'friendly_name': friendly_name,
            'backup_type': backup_type,
            'chain_level': chain_level,
            'backup_date': checkpoint['backup_date'],
            'backup_date_formatted': friendly_date,
            'file_size_mb': round(file_size_mb, 2),
            'backup_file_path': checkpoint['backup_file_path'],
            'verified': checkpoint.get('verified', False),
            'status': checkpoint.get('status', 'unknown')
        }
        
        formatted_checkpoints.append(formatted_checkpoint)
    
    # Calculate summary statistics
    total_size_mb = sum(cp['file_size_mb'] for cp in formatted_checkpoints)
    full_backups = len([cp for cp in formatted_checkpoints if cp['backup_type'] == 'FULL'])
    incremental_backups = len([cp for cp in formatted_checkpoints if cp['backup_type'] == 'INCREMENTAL'])
    
    result = {
        'vm_name': vm_name,
        'checkpoints': formatted_checkpoints,
        'total_checkpoints': len(formatted_checkpoints),
        'full_backups': full_backups,
        'incremental_backups': incremental_backups,
        'total_size_mb': round(total_size_mb, 2),
        'oldest_backup': formatted_checkpoints[0]['backup_date_formatted'] if formatted_checkpoints else None,
        'newest_backup': formatted_checkpoints[-1]['backup_date_formatted'] if formatted_checkpoints else None
    }
    
    # Log summary
    logging.info(f'✓ Found {len(formatted_checkpoints)} checkpoints for VM {vm_name}')
    logging.info(f'  Full backups: {full_backups}')
    logging.info(f'  Incremental backups: {incremental_backups}')
    logging.info(f'  Total size: {total_size_mb:.2f} MB')
    logging.info(f'  Date range: {result["oldest_backup"]} to {result["newest_backup"]}')
    
    # Log individual checkpoints for easy viewing
    logging.info('Available checkpoints:')
    for cp in formatted_checkpoints:
        status_icon = '✓' if cp['verified'] else '?'
        logging.info(f'  [{cp["id"]:2d}] {status_icon} {cp["friendly_name"]} ({cp["file_size_mb"]:.1f} MB)')
    
    return result

def validate_vm_configuration(**context):
    """Validate that the VM exists in configuration"""
    vm_name = context['dag_run'].conf.get('vm_name') if context['dag_run'].conf else None
    
    if not vm_name:
        raise ValueError('VM name must be provided in DAG run configuration')
    
    # Check if VM exists in configuration
    try:
        # Get the full variables.json structure
        all_vars = Variable.get('vms_config', deserialize_json=True, default_var={})
        
        # Handle both legacy and new structure
        vms_config = all_vars
        if 'vms_config' in all_vars:
            vms_config = all_vars['vms_config']
        
        if vm_name not in vms_config:
            logging.warning(f'VM {vm_name} not found in vms_config, but proceeding with checkpoint listing')
            return {
                'vm_name': vm_name,
                'configured': False,
                'message': f'VM {vm_name} not in configuration but may have backups'
            }
        
        vm_config = vms_config[vm_name]
        logging.info(f'VM {vm_name} found in configuration')
        
        # Parse schedule information for display
        schedule_info = vm_config.get('schedule', {})
        schedule_type = schedule_info.get('type', 'unknown')
        
        if schedule_type == 'simple':
            schedule_desc = f"{schedule_info.get('pattern', 'unknown')} at {schedule_info.get('time', 'unknown')}"
        elif schedule_type == 'custom':
            schedule_desc = f"Custom pattern: {schedule_info.get('pattern', 'unknown')} at {schedule_info.get('time', 'unknown')}"
        elif schedule_type == 'legacy':
            schedule_desc = f"Legacy 7-bit: {schedule_info.get('backup_policy', 'unknown')} at {schedule_info.get('time', 'unknown')}"
        else:
            schedule_desc = f"Type: {schedule_type}"
        
        return {
            'vm_name': vm_name,
            'configured': True,
            'vm_config': vm_config,
            'schedule_description': schedule_desc,
            'auto_restore': vm_config.get('auto_restore', False),
            'incremental_enabled': vm_config.get('backup_type', {}).get('incremental_enabled', False)
        }
        
    except Exception as e:
        logging.warning(f'Error checking VM configuration: {e}')
        return {
            'vm_name': vm_name,
            'configured': False,
            'error': str(e)
        }

# Define tasks
validate_vm_task = PythonOperator(
    task_id='validate_vm_configuration',
    python_callable=validate_vm_configuration,
    dag=dag
)

list_checkpoints_task = PythonOperator(
    task_id='list_vm_checkpoints',
    python_callable=list_vm_checkpoints,
    dag=dag
)

# Task dependencies
validate_vm_task >> list_checkpoints_task