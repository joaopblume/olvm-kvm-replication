from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable
import ovirtsdk4 as sdk
import ovirtsdk4.types as types
import logging
import os
import json
import time
from checkpoint_manager import CheckpointManager

# Configurações do DAG
default_args = {
    'owner': 'ovirt-restore-specific',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': timedelta(minutes=10),
}

dag = DAG(
    'restaura_checkpoint_especifico',
    default_args=default_args,
    description='Restore VM to a specific checkpoint by ID',
    schedule=None,  # Manual trigger only
    catchup=False,
    max_active_runs=1,
    tags=['restore', 'dr', 'specific', 'manual', 'target']
)

# Configurações globais
SOURCE_CONFIG = {
    'url': Variable.get('source_ovirt_url'),
    'user': Variable.get('source_ovirt_user'),
    'passwd': Variable.get('source_ovirt_password'),
    'certificate': Variable.get('source_ovirt_cert_path')
}

TARGET_CONFIG = {
    'url': Variable.get('target_ovirt_url'),
    'user': Variable.get('target_ovirt_user'),
    'passwd': Variable.get('target_ovirt_password'),
    'certificate': Variable.get('target_ovirt_cert_path')
}

BACKUP_DIR = Variable.get('backup_directory')

def create_ovirt_connection(config):
    """Cria conexão com oVirt Engine"""
    logging.info(f"Tentando conectar em: {config['url']}")
    logging.info(f"Usuário: {config['user']}")
    logging.info(f"Certificado: {config['certificate']}")
    
    try:
        connection = sdk.Connection(
            url=config['url'],
            username=config['user'],
            password=config['passwd'],
            ca_file=config['certificate'],
            log=logging.getLogger(),
            debug=True,
            insecure=True
        )
        logging.info("✓ Conexão oVirt criada com sucesso")
        return connection
    except Exception as e:
        logging.error(f"✗ Erro na conexão oVirt: {e}")
        
        # Tentar variações do usuário se falhar
        alt_users = [
            config['user'].replace('@internal', ''),
            'admin',
            config['user'].replace('@internal', '@ovirt.local')
        ]
        
        for alt_user in alt_users:
            if alt_user != config['user']:
                logging.info(f"Tentando usuário alternativo: {alt_user}")
                try:
                    connection = sdk.Connection(
                        url=config['url'],
                        username=alt_user,
                        password=config['passwd'],
                        ca_file=config['certificate'],
                        log=logging.getLogger(),
                        debug=True,
                        insecure=True
                    )
                    logging.info(f"✓ Conexão bem-sucedida com usuário: {alt_user}")
                    return connection
                except Exception as alt_e:
                    logging.warning(f"Falha com {alt_user}: {alt_e}")
        
        raise e

def validate_restore_request(**context):
    """Validate restore parameters and checkpoint availability"""
    dag_conf = context['dag_run'].conf if context['dag_run'].conf else {}
    
    vm_name = dag_conf.get('vm_name')
    checkpoint_id = dag_conf.get('checkpoint_id')
    force_restore = dag_conf.get('force_restore', False)
    
    if not vm_name:
        raise ValueError('VM name must be provided: {"vm_name": "your_vm_name", "checkpoint_id": 123}')
    
    if not checkpoint_id:
        raise ValueError('Checkpoint ID must be provided: {"vm_name": "your_vm_name", "checkpoint_id": 123}')
    
    try:
        checkpoint_id = int(checkpoint_id)
    except (ValueError, TypeError):
        raise ValueError('Checkpoint ID must be a valid integer')
    
    logging.info(f'Validating restore request for VM: {vm_name}, Checkpoint ID: {checkpoint_id}')
    
    # Initialize checkpoint manager
    checkpoint_manager = CheckpointManager(BACKUP_DIR)
    
    # Load all checkpoints
    data = checkpoint_manager._load_checkpoints()
    
    # Find the specific checkpoint
    target_checkpoint = None
    for cp in data['checkpoints']:
        if cp['id'] == checkpoint_id and cp.get('vm_name') == vm_name and cp.get('status') == 'active':
            target_checkpoint = cp
            break
    
    if not target_checkpoint:
        available_checkpoints = [
            f"ID {cp['id']} ({cp['backup_type']}, {cp['backup_date']})"
            for cp in data['checkpoints']
            if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
        ]
        raise ValueError(f'Checkpoint ID {checkpoint_id} not found for VM {vm_name}. Available checkpoints: {available_checkpoints}')
    
    # Build the checkpoint chain up to the target
    all_vm_checkpoints = [
        cp for cp in data['checkpoints']
        if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
    ]
    all_vm_checkpoints.sort(key=lambda x: x['backup_date'])
    
    # Find the full backup that starts the target checkpoint's chain
    target_date = datetime.fromisoformat(target_checkpoint['backup_date'])
    chain_start_full = None
    
    for cp in reversed(all_vm_checkpoints):
        cp_date = datetime.fromisoformat(cp['backup_date'])
        if cp['backup_type'] == 'full' and cp_date <= target_date:
            chain_start_full = cp
            break
    
    if not chain_start_full:
        raise ValueError(f'No full backup found before target checkpoint {checkpoint_id}')
    
    # Get chain from the full backup up to target checkpoint (chronologically)
    chain_start_date = datetime.fromisoformat(chain_start_full['backup_date'])
    restore_chain = [
        cp for cp in all_vm_checkpoints 
        if datetime.fromisoformat(cp['backup_date']) >= chain_start_date 
        and datetime.fromisoformat(cp['backup_date']) <= target_date
    ]
    
    if not restore_chain:
        raise ValueError(f'No valid restore chain found for checkpoint {checkpoint_id}')
    
    # Validate chain integrity
    if restore_chain[0]['backup_type'] != 'full':
        raise ValueError(f'Restore chain must start with a full backup')
    
    # Check if all files exist
    missing_files = []
    for cp in restore_chain:
        if not os.path.exists(cp['backup_file_path']):
            missing_files.append(cp['backup_file_path'])
    
    if missing_files:
        raise ValueError(f'Missing backup files: {missing_files}')
    
    # Get VM configuration
    vm_config = {}
    try:
        vms_config = Variable.get('vms_config', deserialize_json=True)
        vm_config = vms_config.get(vm_name, {})
    except Exception as e:
        logging.warning(f'Could not load VM config: {e}')
    
    # Get metadata from target checkpoint
    metadata_file = target_checkpoint['backup_file_path'].replace('.qcow2', '.json')
    backup_metadata = {}
    
    if os.path.exists(metadata_file):
        with open(metadata_file, 'r') as f:
            backup_metadata = json.load(f)
    else:
        logging.warning(f'No metadata file found: {metadata_file}')
    
    # Test connectivity
    try:
        target_conn = create_ovirt_connection(TARGET_CONFIG)
        target_conn.test()
        target_conn.close()
        logging.info('✓ Conectividade com ambiente de DR OK')
    except Exception as e:
        raise RuntimeError(f'Erro conectando com ambiente de DR: {e}')
    
    # Calculate total restore size
    total_size_mb = sum(cp.get('file_size', 0) for cp in restore_chain) / (1024 * 1024)
    
    # Format target checkpoint info
    target_date = datetime.fromisoformat(target_checkpoint['backup_date'])
    target_friendly_date = target_date.strftime('%Y-%m-%d %H:%M:%S')
    
    if target_checkpoint['backup_type'] == 'full':
        friendly_name = f"FULL BACKUP - {target_friendly_date}"
    else:
        friendly_name = f"INCREMENTAL #{target_checkpoint['chain_level']} - {target_friendly_date}"
    
    logging.info(f'✓ Validation complete')
    logging.info(f'  Target checkpoint: {friendly_name}')
    logging.info(f'  Restore chain: {len(restore_chain)} files')
    logging.info(f'  Total size: {total_size_mb:.1f} MB')
    
    return {
        'vm_name': vm_name,
        'checkpoint_id': checkpoint_id,
        'target_checkpoint': target_checkpoint,
        'restore_chain': restore_chain,
        'vm_config': vm_config,
        'backup_metadata': backup_metadata,
        'force_restore': force_restore,
        'total_size_mb': total_size_mb,
        'friendly_name': friendly_name
    }

def perform_specific_restore(**context):
    """Perform the restore to the specific checkpoint"""
    validation_info = context['task_instance'].xcom_pull(task_ids='validate_restore_request')
    
    vm_name = validation_info['vm_name']
    target_checkpoint = validation_info['target_checkpoint']
    restore_chain = validation_info['restore_chain']
    backup_metadata = validation_info['backup_metadata']
    vm_config = validation_info['vm_config']
    force_restore = validation_info['force_restore']
    friendly_name = validation_info['friendly_name']
    
    logging.info(f'Starting restore of VM {vm_name} to DR environment')
    logging.info(f'Target checkpoint: {friendly_name}')
    
    # Connect to DR environment
    connection = create_ovirt_connection(TARGET_CONFIG)
    restored_vm_id = None
    
    try:
        # Check if VM exists in DR - but we'll ALWAYS create a new VM with _clone suffix
        vms_service = connection.system_service().vms_service()
        existing_vms = vms_service.list(search=f'name={vm_name}')
        
        # Check for old cloned VMs and temporarily rename if exists
        old_cloned_vms = vms_service.list(search=f'name={vm_name}_clone')
        old_clone_temp_name = None
        old_clone_id = None
        
        if old_cloned_vms:
            # Temporarily rename the old clone to avoid conflicts
            old_clone_vm = old_cloned_vms[0]
            old_clone_id = old_clone_vm.id
            old_clone_temp_name = f"{vm_name}_clone_temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            old_clone_service = vms_service.vm_service(old_clone_id)
            old_clone_service.update(types.Vm(name=old_clone_temp_name))
            logging.info(f'Temporarily renamed old clone to: {old_clone_temp_name}')
        
        restore_action = 'create'  # ALWAYS create new VM with _clone suffix
        existing_vm_id = existing_vms[0].id if existing_vms else None
        
        logging.info(f'Original VM exists: {len(existing_vms) > 0}')
        logging.info(f'Old cloned VM exists: {len(old_cloned_vms) > 0}')
        logging.info(f'Will create new VM with _clone suffix - original VM preserved')
        
        # Create new VM in DR
        restored_vm_id = create_vm_in_dr_complete(
            connection=connection,
            vm_name=vm_name,
            backup_metadata=backup_metadata,
            restore_action=restore_action,
            existing_vm_id=existing_vm_id
        )
        
        try:
            # Restore disks
            restore_vm_disks_to_checkpoint(
                connection=connection,
                vm_id=restored_vm_id,
                restore_chain=restore_chain,
                backup_metadata=backup_metadata,
                vm_config=vm_config
            )
            
            # Finalize restore
            old_vm_backup_id = finalize_vm_restore_complete(
                connection=connection,
                vm_name=vm_name,
                restored_vm_id=restored_vm_id,
                restore_action=restore_action,
                existing_vm_id=existing_vm_id,
                old_clone_id=old_clone_id,
                old_clone_temp_name=old_clone_temp_name,
                target_checkpoint=target_checkpoint
            )
            
            # Cleanup old cloned VM if exists (NEVER cleanup original VM)
            if old_vm_backup_id:
                cleanup_old_vm_complete(connection, old_vm_backup_id, old_clone_temp_name or f"{vm_name}_clone_old")
            
            result = {
                'vm_name': vm_name,
                'restored_vm_id': restored_vm_id,
                'restore_action': restore_action,
                'target_checkpoint_id': target_checkpoint['id'],
                'backup_date': target_checkpoint['backup_date'],
                'backup_type': target_checkpoint['backup_type'],
                'chain_level': target_checkpoint['chain_level'],
                'friendly_name': friendly_name,
                'files_restored': len(restore_chain)
            }
            
            logging.info(f'✓ Restore completed successfully')
            logging.info(f'  Original VM preserved with name: {vm_name}')
            logging.info(f'  Restored VM created with name: {vm_name}_clone')
            logging.info(f'  Checkpoint: {friendly_name}')
            logging.info(f'  Files restored: {len(restore_chain)}')
            
            return result
            
        except Exception as disk_error:
            # Clean up failed VM
            logging.error(f'Disk restore failed for {vm_name}: {disk_error}')
            if restored_vm_id:
                cleanup_failed_vm_complete(connection, restored_vm_id, f"{vm_name}_restore_failed")
            
            # Restore old clone name if it was renamed
            if old_clone_id and old_clone_temp_name:
                try:
                    old_clone_service = vms_service.vm_service(old_clone_id)
                    old_clone_service.update(types.Vm(name=f"{vm_name}_clone"))
                    logging.info(f'Restored old clone name back to: {vm_name}_clone')
                except Exception as restore_error:
                    logging.error(f'Failed to restore old clone name: {restore_error}')
            
            raise disk_error
        
    finally:
        connection.close()

def create_vm_in_dr_complete(connection, vm_name, backup_metadata, restore_action, existing_vm_id=None):
    """Create VM in DR environment - ALWAYS with _clone suffix"""
    temp_vm_name = f"{vm_name}_clone"
    
    # Get restore settings from variables.json
    try:
        restore_settings = Variable.get('restore_settings', deserialize_json=True)
    except Exception:
        restore_settings = {}
    
    # Get cluster
    clusters_service = connection.system_service().clusters_service()
    target_cluster_name = restore_settings.get('target_cluster_name', 'Default')
    
    target_cluster = None
    for cluster in clusters_service.list():
        if cluster.name == target_cluster_name:
            target_cluster = cluster
            break
    
    if not target_cluster:
        # Fallback to first cluster
        clusters = clusters_service.list()
        if not clusters:
            raise RuntimeError('No cluster found in DR environment')
        target_cluster = clusters[0]
        logging.warning(f'Target cluster "{target_cluster_name}" not found, using: {target_cluster.name}')
    
    # Get blank template
    templates_service = connection.system_service().templates_service()
    blank_template = templates_service.list(search='name=Blank')[0]
    
    # VM configuration from backup metadata
    vm_config = backup_metadata.get('vm_config', {})
    
    # Get chipset and firmware from restore settings
    chipset_type = restore_settings.get('chipset_type', 'q35')
    firmware_type = restore_settings.get('firmware_type', 'bios')
    
    # Create VM
    vms_service = connection.system_service().vms_service()
    vm_create = types.Vm(
        name=temp_vm_name,
        cluster=types.Cluster(id=target_cluster.id),
        template=types.Template(id=blank_template.id),
        memory=int(vm_config.get('memory_mb', 2048) * 1024 * 1024),
        cpu=types.Cpu(
            topology=types.CpuTopology(
                cores=vm_config.get('cpu_cores', 2),
                sockets=vm_config.get('cpu_sockets', 1),
                threads=vm_config.get('cpu_threads', 1)
            )
        ),
        bios=types.Bios(
            type=types.BiosType.Q35_SEA_BIOS if chipset_type == 'q35' and firmware_type == 'bios' else types.BiosType.I440FX_SEA_BIOS
        ),
        description=f"Restored VM using specific checkpoint restore on {datetime.now().isoformat()}"
    )
    
    # Set chipset if supported
    if hasattr(types, 'ChipsetType'):
        vm_create.chipset = types.Chipset(
            type=getattr(types.ChipsetType, chipset_type.upper(), types.ChipsetType.Q35)
        )
    
    new_vm = vms_service.add(vm_create)
    logging.info(f'VM created in DR: {new_vm.name} (ID: {new_vm.id})')
    
    # Wait for VM to be ready
    vm_service = vms_service.vm_service(new_vm.id)
    timeout = 300
    start_time = time.time()
    
    while True:
        vm = vm_service.get()
        if vm.status == types.VmStatus.DOWN:
            break
        elif time.time() - start_time > timeout:
            raise RuntimeError('Timeout waiting for VM to be ready')
        time.sleep(5)
    
    return new_vm.id

def merge_backup_chain_complete(backup_chain, temp_dir):
    """Merge incremental backup chain into a single optimized disk"""
    import subprocess
    import tempfile
    
    if len(backup_chain) == 1:
        # Single file, no merge needed
        return backup_chain[0]['backup_file_path']
    
    # Sort chain by level to ensure proper order
    sorted_chain = sorted(backup_chain, key=lambda x: x['chain_level'])
    
    logging.info(f'Merging {len(sorted_chain)} files in backup chain')
    
    logging.info(f'Chain: {" → ".join([os.path.basename(cp["backup_file_path"]) for cp in sorted_chain])}')
    
    # We need to manually rebuild the backing chain for qemu-img to work
    # Create temporary copies with correct backing file relationships
    temp_files = []
    
    try:
        # Start with the full backup (base)
        base_file = sorted_chain[0]['backup_file_path']
        current_base = os.path.join(temp_dir, f"base_{int(time.time())}.qcow2")
        
        # Copy the full backup as our base
        logging.info(f'Copying base file: {os.path.basename(base_file)}')
        subprocess.run(['cp', base_file, current_base], check=True)
        temp_files.append(current_base)
        
        # Apply each incremental on top, building the chain
        for i, checkpoint in enumerate(sorted_chain[1:], 1):
            incremental_file = checkpoint['backup_file_path']
            temp_incremental = os.path.join(temp_dir, f"incremental_{i}_{int(time.time())}.qcow2")
            
            logging.info(f'Processing incremental {i}: {os.path.basename(incremental_file)}')
            
            # Copy incremental file
            subprocess.run(['cp', incremental_file, temp_incremental], check=True)
            temp_files.append(temp_incremental)
            
            # Rebase the incremental to point to our current base
            cmd = [
                'qemu-img', 'rebase',
                '-u',  # Unsafe mode (don't check backing file format)
                '-b', current_base,  # New backing file
                '-F', 'qcow2',  # Backing file format
                temp_incremental
            ]
            logging.info(f'Rebasing: qemu-img rebase -u -b {os.path.basename(current_base)} {os.path.basename(temp_incremental)}')
            subprocess.run(cmd, check=True)
            
            # Now commit this incremental into the base to create a new merged base
            new_base = os.path.join(temp_dir, f"merged_{i}_{int(time.time())}.qcow2")
            
            # Convert the incremental + backing into a single standalone file
            cmd = [
                'qemu-img', 'convert',
                '-O', 'qcow2',
                temp_incremental,  # This will follow backing chain
                new_base
            ]
            logging.info(f'Merging: qemu-img convert {os.path.basename(temp_incremental)} {os.path.basename(new_base)}')
            subprocess.run(cmd, check=True)
            
            temp_files.append(new_base)
            current_base = new_base
        
        # Skip final compression - the merged file is already optimized
        # Final compression can actually increase file size in many cases
        optimized_file = current_base
        
        # Verify the final file
        cmd = ['qemu-img', 'info', optimized_file]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logging.info(f'Final file info: {result.stdout}')
        
        # Get final file size for reporting
        final_size = os.path.getsize(optimized_file)
        original_total = sum(os.path.getsize(cp['backup_file_path']) for cp in backup_chain)
        
        logging.info(f'✓ Chain merged successfully (no additional compression)')
        logging.info(f'  Original total: {original_total/(1024**2):.1f} MB')
        logging.info(f'  Merged result: {final_size/(1024**2):.1f} MB')
        if original_total > 0:
            logging.info(f'  Space difference: {((original_total - final_size) / original_total * 100):.1f}%')
        
        return optimized_file
        
    except Exception as e:
        logging.error(f'Error during chain merge: {e}')
        # Cleanup temp files on error
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
        raise

def restore_vm_disks_to_checkpoint(connection, vm_id, restore_chain, backup_metadata, vm_config):
    """Restore disks up to specific checkpoint using optimized chain merging"""
    logging.info(f'Restoring VM {vm_id} using {len(restore_chain)} backup files in chain')
    
    # Log additional restore context
    if backup_metadata:
        logging.info(f'Backup metadata available for VM configuration')
    if vm_config:
        logging.info(f'VM configuration available for restore validation')
    
    # Import required modules
    try:
        from ovirt_imageio import client
        from helpers import imagetransfer
        import subprocess
    except ImportError as e:
        logging.error(f"Missing required modules for disk restore: {e}")
        raise RuntimeError(f"Missing required modules for disk restore: {e}")
    
    # Get storage domain from restore settings
    try:
        restore_settings = Variable.get('restore_settings', deserialize_json=True)
        target_storage_name = restore_settings.get('target_storage_domain_name', 'storage-dr')
    except Exception:
        target_storage_name = 'storage-dr'
    
    storage_domains_service = connection.system_service().storage_domains_service()
    storage_domains = storage_domains_service.list()
    
    data_storage = None
    for sd in storage_domains:
        if sd.type == types.StorageDomainType.DATA and sd.name == target_storage_name:
            data_storage = sd
            break
    
    if not data_storage:
        # Fallback to first data storage domain
        for sd in storage_domains:
            if sd.type == types.StorageDomainType.DATA:
                data_storage = sd
                break
    
    if not data_storage:
        raise RuntimeError('No active data storage domain found')
    
    logging.info(f'Using storage domain: {data_storage.name}')
    
    # Group files by disk (assuming single disk for now)
    disks_service = connection.system_service().disks_service()
    vm_service = connection.system_service().vms_service().vm_service(vm_id)
    
    # Create temp directory for merge operations
    temp_dir = f"/tmp/merge_chain_{int(time.time())}"
    os.makedirs(temp_dir, exist_ok=True)
    
    # Merge the backup chain for optimization
    merged_file = merge_backup_chain_complete(restore_chain, temp_dir)
    
    try:
        # Get disk info from merged file
        out = subprocess.check_output(['qemu-img', 'info', '--output', 'json', merged_file]).decode('utf-8')
        disk_info = json.loads(out)
        initial_size = disk_info.get('actual-size', 0)
        virtual_size = disk_info.get('virtual-size', 1024 * 1024 * 1024)
        
        # Create disk
        upload_disk = disks_service.add(
            types.Disk(
                name=f'{vm_id}_disk_merged_cp_{restore_chain[-1]["id"]}',
                format=types.DiskFormat.COW,
                initial_size=initial_size,
                backup=types.DiskBackup.INCREMENTAL,
                content_type=types.DiskContentType.DATA,
                provisioned_size=virtual_size,
                storage_domains=[types.StorageDomain(name=data_storage.name)]
            )
        )
        
        # Wait for disk to be ready
        disk_service = disks_service.disk_service(upload_disk.id)
        timeout = 300
        start_time = time.time()
        
        while True:
            disk = disk_service.get()
            if disk.status == types.DiskStatus.OK:
                break
            elif time.time() - start_time > timeout:
                raise RuntimeError('Timeout creating disk')
            time.sleep(10)
        
        # Upload merged disk
        host = imagetransfer.find_host(connection, data_storage.name)
        transfer = imagetransfer.create_transfer(
            connection,
            upload_disk,
            types.ImageTransferDirection.UPLOAD,
            host=host,
            inactivity_timeout=3600
        )
        
        try:
            with client.ProgressBar() as pb:
                client.upload(
                    merged_file,
                    transfer.transfer_url,
                    TARGET_CONFIG['certificate'],
                    buffer_size=client.BUFFER_SIZE,
                    progress=pb
                )
            
        except Exception as upload_error:
            imagetransfer.cancel_transfer(connection, transfer)
            raise upload_error
        
        imagetransfer.finalize_transfer(connection, transfer, disk)
        logging.info(f'✓ Merged disk uploaded successfully')
        
        # Attach disk to VM
        disk_attachments_service = vm_service.disk_attachments_service()
        
        # Get disk interface from restore settings
        disk_interface = getattr(types.DiskInterface, restore_settings.get('disk_interface', 'SATA'))
        
        disk_attachment = types.DiskAttachment(
            disk=types.Disk(id=upload_disk.id),
            interface=disk_interface,
            bootable=True,
            active=True
        )
        disk_attachments_service.add(disk_attachment)
        logging.info(f'✓ Disk attached to VM')
        
    finally:
        # Cleanup merged file
        if os.path.exists(merged_file):
            os.remove(merged_file)
        # Cleanup temp directory
        temp_dir = os.path.dirname(merged_file)
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir)
    
    logging.info(f'✓ Disk restoration completed with chain optimization')

def finalize_vm_restore_complete(connection, vm_name, restored_vm_id, restore_action, existing_vm_id, old_clone_id, old_clone_temp_name, target_checkpoint):
    """Finalize restore process - NEVER touch the original VM"""
    vms_service = connection.system_service().vms_service()
    
    # ALWAYS ensure restored VM has _clone suffix and original VM is NEVER touched
    restored_vm_service = vms_service.vm_service(restored_vm_id)
    new_vm_name = f"{vm_name}_clone"
    restored_vm_service.update(types.Vm(name=new_vm_name))
    
    logging.info(f'Original VM preserved with name: {vm_name}')
    logging.info(f'Restored VM created with name: {new_vm_name}')
    
    # Record restore
    restore_record = {
        'vm_name': vm_name,
        'restored_vm_id': restored_vm_id,
        'restore_date': datetime.now().isoformat(),
        'target_checkpoint_id': target_checkpoint['id'],
        'backup_date': target_checkpoint['backup_date'],
        'backup_type': target_checkpoint['backup_type'],
        'chain_level': target_checkpoint['chain_level'],
        'restore_action': restore_action,
        'old_vm_backup_id': existing_vm_id,
        'status': 'completed',
        'restore_type': 'specific_checkpoint'
    }
    
    # Save restore log
    restore_log_file = os.path.join(BACKUP_DIR, f'restore_log_{vm_name}.json')
    restore_logs = []
    
    if os.path.exists(restore_log_file):
        with open(restore_log_file, 'r') as f:
            restore_logs = json.load(f)
    
    restore_logs.append(restore_record)
    
    with open(restore_log_file, 'w') as f:
        json.dump(restore_logs, f, indent=2)
    
    logging.info(f'Restore finalized for {vm_name} - checkpoint: {target_checkpoint["id"]}')
    
    # Return old clone ID for cleanup if exists (NOT the original VM)
    old_vm_backup_id = old_clone_id if old_clone_id else None
    
    return old_vm_backup_id

def cleanup_failed_vm_complete(connection, vm_id, vm_name):
    """Remove VM that failed during restore"""
    try:
        logging.info(f'Removing failed VM: {vm_name} (ID: {vm_id})')
        vms_service = connection.system_service().vms_service()
        vm_service = vms_service.vm_service(vm_id)
        
        try:
            vm = vm_service.get()
            if vm.status == types.VmStatus.UP:
                vm_service.stop()
                time.sleep(10)
        except Exception as e:
            logging.warning(f'Error stopping VM {vm_name}: {e}')
        
        vm_service.remove()
        logging.info(f'✓ Failed VM removed: {vm_name}')
        
    except Exception as e:
        logging.error(f'Error removing failed VM {vm_name}: {e}')

def cleanup_old_vm_complete(connection, vm_id, vm_name):
    """Remove old VM after successful restore - Only removes old cloned VMs, never the original"""
    print('Waiting for 60 seconds before cleanup...')
    time.sleep(60)
    try:
        logging.info(f'Removing old VM: {vm_name} (ID: {vm_id})')
        vms_service = connection.system_service().vms_service()
        vm_service = vms_service.vm_service(vm_id)
        
        try:
            vm = vm_service.get()
            if vm.status == types.VmStatus.UP:
                vm_service.stop()
                time.sleep(10)
        except Exception as e:
            logging.warning(f'Error stopping old VM {vm_name}: {e}')
        
        vm_service.remove()
        logging.info(f'✓ Old VM removed: {vm_name}')
        
    except Exception as e:
        logging.error(f'Error removing old VM {vm_name}: {e}')

# Define tasks
validate_request_task = PythonOperator(
    task_id='validate_restore_request',
    python_callable=validate_restore_request,
    dag=dag
)

perform_restore_task = PythonOperator(
    task_id='perform_specific_restore',
    python_callable=perform_specific_restore,
    dag=dag
)

# Task dependencies
validate_request_task >> perform_restore_task