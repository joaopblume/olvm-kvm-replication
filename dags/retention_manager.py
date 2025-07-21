"""
Retention Manager - Physical Backup Cleanup
============================================

Este DAG gerencia a limpeza física de backups marcados como 'deleted' pelo CheckpointManager.
Executa a cada 15 minutos e remove efetivamente os arquivos do disco.

Features:
- Limpeza física de arquivos .qcow2 e .json
- Limpeza das entradas 'deleted' do checkpoints.json
- Logging detalhado dos backups encontrados para apagar
- Suporte para todas as configurações de retenção (diário, semanal, mensal)
- Validação de arquivos antes da remoção
- Backup de segurança do checkpoints.json antes da limpeza
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable
import logging
import os
import json
import shutil
from typing import Dict, List, Optional
import time

# Configuração do DAG
default_args = {
    'owner': 'retention-manager',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
    'catchup': False,
}

dag = DAG(
    'gerenciador_de_retencao_e_limpeza',
    default_args=default_args,
    description='Physical cleanup of backup files marked for deletion',
    schedule='*/15 * * * *',  # Executa a cada 15 minutos
    max_active_runs=1,
    tags=['retention', 'cleanup', 'backup']
)

class RetentionManager:
    """Gerenciador de retenção para limpeza física de backups"""
    
    def __init__(self, backup_dir: str):
        self.backup_dir = backup_dir
        self.checkpoints_file = os.path.join(backup_dir, 'checkpoints.json')
        self.backup_checkpoints_file = os.path.join(backup_dir, 'checkpoints_backup.json')
        
    def load_checkpoints(self) -> Dict:
        """Carrega o arquivo checkpoints.json"""
        if not os.path.exists(self.checkpoints_file):
            logging.warning(f'Checkpoints file not found: {self.checkpoints_file}')
            return {'checkpoints': [], 'next_id': 1}
        
        try:
            with open(self.checkpoints_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logging.error(f'Error loading checkpoints file: {e}')
            return {'checkpoints': [], 'next_id': 1}
    
    def save_checkpoints(self, data: Dict):
        """Salva o arquivo checkpoints.json"""
        try:
            with open(self.checkpoints_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.info(f'Checkpoints file updated: {self.checkpoints_file}')
        except Exception as e:
            logging.error(f'Error saving checkpoints file: {e}')
            raise
    
    def backup_checkpoints(self, data: Dict):
        """Cria backup do checkpoints.json antes da limpeza"""
        try:
            with open(self.backup_checkpoints_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.info(f'Backup created: {self.backup_checkpoints_file}')
        except Exception as e:
            logging.warning(f'Failed to create backup: {e}')
    
    def find_deleted_checkpoints(self) -> List[Dict]:
        """Encontra todos os checkpoints marcados como 'deleted'"""
        data = self.load_checkpoints()
        deleted_checkpoints = []
        
        for checkpoint in data['checkpoints']:
            if checkpoint.get('status') == 'deleted':
                deleted_checkpoints.append(checkpoint)
        
        return deleted_checkpoints
    
    def validate_checkpoint_files(self, checkpoint: Dict) -> Dict:
        """Valida se os arquivos do checkpoint existem"""
        backup_file = checkpoint.get('backup_file_path', '')
        metadata_file = backup_file.replace('.qcow2', '.json') if backup_file.endswith('.qcow2') else ''
        
        validation = {
            'backup_file': backup_file,
            'metadata_file': metadata_file,
            'backup_exists': os.path.exists(backup_file) if backup_file else False,
            'metadata_exists': os.path.exists(metadata_file) if metadata_file else False,
            'backup_size': 0,
            'metadata_size': 0
        }
        
        if validation['backup_exists']:
            try:
                validation['backup_size'] = os.path.getsize(backup_file)
            except Exception as e:
                logging.warning(f'Error getting backup file size: {e}')
        
        if validation['metadata_exists']:
            try:
                validation['metadata_size'] = os.path.getsize(metadata_file)
            except Exception as e:
                logging.warning(f'Error getting metadata file size: {e}')
        
        return validation
    
    def remove_checkpoint_files(self, checkpoint: Dict, validation: Dict) -> Dict:
        """Remove os arquivos físicos do checkpoint"""
        removal_result = {
            'backup_removed': False,
            'metadata_removed': False,
            'backup_error': None,
            'metadata_error': None,
            'space_freed': 0
        }
        
        # Remove backup file (.qcow2)
        if validation['backup_exists']:
            try:
                os.remove(validation['backup_file'])
                removal_result['backup_removed'] = True
                removal_result['space_freed'] += validation['backup_size']
                logging.info(f'✓ Backup file removed: {os.path.basename(validation["backup_file"])}')
            except Exception as e:
                removal_result['backup_error'] = str(e)
                logging.error(f'✗ Error removing backup file: {e}')
        
        # Remove metadata file (.json)
        if validation['metadata_exists']:
            try:
                os.remove(validation['metadata_file'])
                removal_result['metadata_removed'] = True
                removal_result['space_freed'] += validation['metadata_size']
                logging.info(f'✓ Metadata file removed: {os.path.basename(validation["metadata_file"])}')
            except Exception as e:
                removal_result['metadata_error'] = str(e)
                logging.error(f'✗ Error removing metadata file: {e}')
        
        return removal_result
    
    def cleanup_checkpoint_json(self, checkpoints_to_remove: List[int]) -> int:
        """Remove entradas 'deleted' do checkpoints.json"""
        data = self.load_checkpoints()
        original_count = len(data['checkpoints'])
        
        # Cria backup antes da limpeza
        self.backup_checkpoints(data)
        
        # Remove checkpoints marcados como 'deleted'
        data['checkpoints'] = [
            cp for cp in data['checkpoints'] 
            if cp.get('id') not in checkpoints_to_remove
        ]
        
        # Salva o arquivo atualizado
        self.save_checkpoints(data)
        
        cleaned_count = original_count - len(data['checkpoints'])
        logging.info(f'✓ Cleaned {cleaned_count} deleted entries from checkpoints.json')
        
        return cleaned_count
    
    def format_size(self, size_bytes: int) -> str:
        """Formata tamanho em bytes para formato legível"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"
    
    def get_vm_retention_info(self, vm_name: str) -> Dict:
        """Obtém informações de retenção da VM"""
        try:
            vms_config = Variable.get('vms_config', deserialize_json=True)
            vm_config = vms_config.get(vm_name, {})
            retention = vm_config.get('retention', {})
            
            return {
                'keep_daily': retention.get('keep_daily', 0),
                'keep_weekly': retention.get('keep_weekly', 0),
                'keep_monthly': retention.get('keep_monthly', 0),
                'max_backup_age_days': retention.get('max_backup_age_days', 0)
            }
        except Exception as e:
            logging.warning(f'Error getting VM retention info for {vm_name}: {e}')
            return {
                'keep_daily': 0,
                'keep_weekly': 0,
                'keep_monthly': 0,
                'max_backup_age_days': 0
            }

def execute_retention_cleanup(**context):
    """Executa a limpeza física dos backups marcados para deleção"""
    backup_dir = Variable.get('backup_directory', '/opt/airflow/backups')
    
    if not os.path.exists(backup_dir):
        logging.warning(f'Backup directory not found: {backup_dir}')
        return {
            'status': 'skipped',
            'message': 'Backup directory not found'
        }
    
    retention_manager = RetentionManager(backup_dir)
    
    logging.info('🧹 Starting retention cleanup process')
    logging.info(f'📁 Backup directory: {backup_dir}')
    
    # Encontrar checkpoints marcados como 'deleted'
    deleted_checkpoints = retention_manager.find_deleted_checkpoints()
    
    if not deleted_checkpoints:
        logging.info('✓ No deleted checkpoints found - nothing to clean')
        return {
            'status': 'success',
            'deleted_checkpoints': 0,
            'files_removed': 0,
            'space_freed': 0
        }
    
    logging.info(f'🔍 Found {len(deleted_checkpoints)} deleted checkpoints to process')
    
    # Processar cada checkpoint para limpeza
    cleanup_results = []
    total_space_freed = 0
    total_files_removed = 0
    checkpoints_to_remove = []
    
    for checkpoint in deleted_checkpoints:
        cp_id = checkpoint.get('id', 'unknown')
        vm_name = checkpoint.get('vm_name', 'unknown')
        backup_date = checkpoint.get('backup_date', 'unknown')
        backup_type = checkpoint.get('backup_type', 'unknown')
        
        logging.info(f'📋 Processing checkpoint {cp_id} ({vm_name})')
        logging.info(f'   Date: {backup_date}')
        logging.info(f'   Type: {backup_type}')
        
        # Obter informações de retenção da VM
        retention_info = retention_manager.get_vm_retention_info(vm_name)
        logging.info(f'   Retention Policy: {retention_info}')
        
        # Validar arquivos
        validation = retention_manager.validate_checkpoint_files(checkpoint)
        
        backup_file = os.path.basename(validation['backup_file']) if validation['backup_file'] else 'N/A'
        metadata_file = os.path.basename(validation['metadata_file']) if validation['metadata_file'] else 'N/A'
        
        logging.info(f'   Backup file: {backup_file} ({retention_manager.format_size(validation["backup_size"])})')
        logging.info(f'   Metadata file: {metadata_file} ({retention_manager.format_size(validation["metadata_size"])})')
        logging.info(f'   Files exist: backup={validation["backup_exists"]}, metadata={validation["metadata_exists"]}')
        
        # Remover arquivos físicos
        removal_result = retention_manager.remove_checkpoint_files(checkpoint, validation)
        
        # Compilar resultados
        cleanup_result = {
            'checkpoint_id': cp_id,
            'vm_name': vm_name,
            'backup_date': backup_date,
            'backup_type': backup_type,
            'validation': validation,
            'removal': removal_result,
            'retention_policy': retention_info
        }
        
        cleanup_results.append(cleanup_result)
        total_space_freed += removal_result['space_freed']
        
        files_removed = 0
        if removal_result['backup_removed']:
            files_removed += 1
        if removal_result['metadata_removed']:
            files_removed += 1
        
        total_files_removed += files_removed
        
        # Marcar para remoção do JSON se pelo menos um arquivo foi removido
        if removal_result['backup_removed'] or removal_result['metadata_removed']:
            checkpoints_to_remove.append(cp_id)
        
        logging.info(f'   Result: {files_removed} files removed, {retention_manager.format_size(removal_result["space_freed"])} freed')
        logging.info('   ' + '-' * 50)
    
    # Limpar entradas do checkpoints.json
    json_cleaned_count = 0
    if checkpoints_to_remove:
        json_cleaned_count = retention_manager.cleanup_checkpoint_json(checkpoints_to_remove)
    
    # Relatório final
    logging.info('📊 RETENTION CLEANUP SUMMARY')
    logging.info('=' * 60)
    logging.info(f'🔍 Deleted checkpoints found: {len(deleted_checkpoints)}')
    logging.info(f'📁 Physical files removed: {total_files_removed}')
    logging.info(f'💾 Total space freed: {retention_manager.format_size(total_space_freed)}')
    logging.info(f'🗑️  JSON entries cleaned: {json_cleaned_count}')
    logging.info('=' * 60)
    
    # Detalhes por VM
    vm_summary = {}
    for result in cleanup_results:
        vm_name = result['vm_name']
        if vm_name not in vm_summary:
            vm_summary[vm_name] = {
                'checkpoints': 0,
                'files_removed': 0,
                'space_freed': 0,
                'retention_policy': result['retention_policy']
            }
        
        vm_summary[vm_name]['checkpoints'] += 1
        vm_summary[vm_name]['space_freed'] += result['removal']['space_freed']
        
        if result['removal']['backup_removed']:
            vm_summary[vm_name]['files_removed'] += 1
        if result['removal']['metadata_removed']:
            vm_summary[vm_name]['files_removed'] += 1
    
    logging.info('📋 CLEANUP BY VM')
    for vm_name, summary in vm_summary.items():
        logging.info(f'  {vm_name}:')
        logging.info(f'    Checkpoints processed: {summary["checkpoints"]}')
        logging.info(f'    Files removed: {summary["files_removed"]}')
        logging.info(f'    Space freed: {retention_manager.format_size(summary["space_freed"])}')
        logging.info(f'    Retention: daily={summary["retention_policy"]["keep_daily"]}, weekly={summary["retention_policy"]["keep_weekly"]}, monthly={summary["retention_policy"]["keep_monthly"]}, max_age={summary["retention_policy"]["max_backup_age_days"]}d')
    
    return {
        'status': 'success',
        'deleted_checkpoints': len(deleted_checkpoints),
        'files_removed': total_files_removed,
        'space_freed': total_space_freed,
        'space_freed_formatted': retention_manager.format_size(total_space_freed),
        'json_entries_cleaned': json_cleaned_count,
        'vm_summary': vm_summary,
        'cleanup_results': cleanup_results
    }

def validate_backup_directory(**context):
    """Valida o diretório de backup antes da limpeza"""
    backup_dir = Variable.get('backup_directory', '/opt/airflow/backups')
    
    validations = {
        'backup_dir': backup_dir,
        'exists': os.path.exists(backup_dir),
        'readable': False,
        'writable': False,
        'checkpoints_file_exists': False,
        'disk_usage': {}
    }
    
    if validations['exists']:
        validations['readable'] = os.access(backup_dir, os.R_OK)
        validations['writable'] = os.access(backup_dir, os.W_OK)
        
        checkpoints_file = os.path.join(backup_dir, 'checkpoints.json')
        validations['checkpoints_file_exists'] = os.path.exists(checkpoints_file)
        
        try:
            stat = shutil.disk_usage(backup_dir)
            validations['disk_usage'] = {
                'total': stat.total,
                'used': stat.used,
                'free': stat.free,
                'used_percent': (stat.used / stat.total) * 100
            }
        except Exception as e:
            logging.warning(f'Error getting disk usage: {e}')
    
    logging.info('🔍 BACKUP DIRECTORY VALIDATION')
    logging.info(f'  Directory: {backup_dir}')
    logging.info(f'  Exists: {validations["exists"]}')
    logging.info(f'  Readable: {validations["readable"]}')
    logging.info(f'  Writable: {validations["writable"]}')
    logging.info(f'  Checkpoints file: {validations["checkpoints_file_exists"]}')
    
    if validations['disk_usage']:
        total_gb = validations['disk_usage']['total'] / (1024**3)
        used_gb = validations['disk_usage']['used'] / (1024**3)
        free_gb = validations['disk_usage']['free'] / (1024**3)
        used_pct = validations['disk_usage']['used_percent']
        
        logging.info(f'  Disk usage: {used_gb:.1f}GB / {total_gb:.1f}GB ({used_pct:.1f}%)')
        logging.info(f'  Free space: {free_gb:.1f}GB')
    
    if not validations['exists']:
        raise RuntimeError(f'Backup directory does not exist: {backup_dir}')
    
    if not validations['readable']:
        raise RuntimeError(f'Backup directory is not readable: {backup_dir}')
    
    if not validations['writable']:
        raise RuntimeError(f'Backup directory is not writable: {backup_dir}')
    
    return validations

# Definir tasks
validate_directory_task = PythonOperator(
    task_id='validate_backup_directory',
    python_callable=validate_backup_directory,
    dag=dag
)

cleanup_task = PythonOperator(
    task_id='execute_retention_cleanup',
    python_callable=execute_retention_cleanup,
    dag=dag
)

# Dependências das tasks
validate_directory_task >> cleanup_task