import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from airflow.models import Variable
import ovirtsdk4 as sdk
import ovirtsdk4.types as types

class CheckpointManager:
    """Manages incremental backup checkpoints and chain tracking"""
    
    def __init__(self, backup_dir: str):
        self.backup_dir = backup_dir
        self.checkpoints_file = os.path.join(backup_dir, 'checkpoints.json')
        self._init_checkpoints_file()
    
    def _init_checkpoints_file(self):
        """Initialize JSON file for checkpoint tracking"""
        os.makedirs(self.backup_dir, exist_ok=True)
        
        if not os.path.exists(self.checkpoints_file):
            with open(self.checkpoints_file, 'w') as f:
                json.dump({'checkpoints': [], 'next_id': 1}, f, indent=2)
    
    def _load_checkpoints(self) -> Dict:
        """Load checkpoints from JSON file"""
        try:
            with open(self.checkpoints_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {'checkpoints': [], 'next_id': 1}
    
    def _save_checkpoints(self, data: Dict):
        """Save checkpoints to JSON file"""
        with open(self.checkpoints_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def get_last_checkpoint(self, vm_name: str) -> Optional[str]:
        """Get the most recent checkpoint ID for a VM (for incremental backups)"""
        data = self._load_checkpoints()
        
        vm_checkpoints = [
            cp for cp in data['checkpoints'] 
            if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
        ]
        
        if not vm_checkpoints:
            return None
        
        # Sort by backup_date and return the most recent checkpoint_id
        vm_checkpoints.sort(key=lambda x: x['backup_date'], reverse=True)
        return vm_checkpoints[0].get('checkpoint_id')
    
    def invalidate_checkpoint(self, vm_name: str, checkpoint_id: str):
        """Mark a checkpoint as invalid when backup fails"""
        if not checkpoint_id:
            return
            
        data = self._load_checkpoints()
        
        for checkpoint in data['checkpoints']:
            if (checkpoint.get('vm_name') == vm_name and 
                checkpoint.get('checkpoint_id') == checkpoint_id):
                checkpoint['status'] = 'invalid'
                checkpoint['invalidated_at'] = datetime.now().isoformat()
                logging.info(f"Invalidated checkpoint {checkpoint_id} for VM {vm_name}")
                break
        
        self._save_checkpoints(data)
    
    def force_full_backup_after_failure(self, vm_name: str):
        """Force next backup to be full after incremental failure"""
        data = self._load_checkpoints()
        
        # Mark all active checkpoints for this VM as requiring full backup
        for checkpoint in data['checkpoints']:
            if (checkpoint.get('vm_name') == vm_name and 
                checkpoint.get('status') == 'active'):
                checkpoint['force_full_next'] = True
                
        self._save_checkpoints(data)
        logging.info(f"Marked VM {vm_name} to force full backup on next run")
    
    def get_checkpoint_chain(self, vm_name: str) -> List[Dict]:
        """Get the full checkpoint chain for a VM"""
        data = self._load_checkpoints()
        
        vm_checkpoints = [
            cp for cp in data['checkpoints'] 
            if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
        ]
        
        # Sort by chain_level
        vm_checkpoints.sort(key=lambda x: x.get('chain_level', 0))
        return vm_checkpoints
    
    def should_do_full_backup(self, vm_name: str, vm_config: Dict) -> bool:
        """Determine if a full backup is needed"""
        # Get backup settings
        backup_settings = Variable.get('backup_settings', deserialize_json=True, default_var={})
        
        # Check if incremental is enabled globally and for this VM
        if not backup_settings.get('incremental_enabled', False):
            return True
        
        if not vm_config.get('backup_type', {}).get('incremental_enabled', False):
            return True
        
        # Get last checkpoint record
        data = self._load_checkpoints()
        vm_checkpoints = [
            cp for cp in data['checkpoints'] 
            if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
        ]
        
        if not vm_checkpoints:
            logging.info(f"No previous checkpoints found for {vm_name}, doing full backup")
            return True
        
        vm_checkpoints.sort(key=lambda x: x['backup_date'], reverse=True)
        last_checkpoint = vm_checkpoints[0]
        
        # Check if forced full backup is requested due to previous failure
        if last_checkpoint.get('force_full_next', False):
            logging.info(f"Forced full backup for {vm_name} due to previous failure")
            # Clear the force_full_next flag
            last_checkpoint['force_full_next'] = False
            self._save_checkpoints(data)
            return True
        
        # Check if chain is too long
        chain = self.get_checkpoint_chain(vm_name)
        max_chain_length = backup_settings.get('max_incremental_chain_length', 10)
        
        if len(chain) >= max_chain_length:
            logging.info(f"Chain length {len(chain)} exceeds max {max_chain_length}, forcing full backup")
            return True
        
        # Check if forced full backup is due
        last_backup_date = datetime.fromisoformat(last_checkpoint['backup_date'])
        days_since_backup = (datetime.now() - last_backup_date).days
        force_full_days = backup_settings.get('force_full_backup_days', 7)
        
        if days_since_backup >= force_full_days:
            logging.info(f"Days since last backup {days_since_backup} exceeds force full days {force_full_days}")
            return True
        
        # Check full backup frequency policy
        full_frequency = vm_config.get('backup_type', {}).get('full_backup_frequency', 'weekly')
        
        if full_frequency == 'daily':
            return True
        elif full_frequency == 'weekly':
            # Check if it's the designated full backup day
            full_day = vm_config.get('backup_type', {}).get('full_backup_day', 'sunday')
            current_day = datetime.now().strftime('%A').lower()
            
            if current_day == full_day.lower():
                logging.info(f"Today is {current_day}, designated full backup day")
                return True
        
        # Check if last backup was full and it's been a week
        if last_checkpoint['backup_type'] == 'full' and days_since_backup >= 7:
            return True
        
        logging.info(f"Conditions met for incremental backup of {vm_name}")
        return False
    
    def create_checkpoint(self, vm_name: str, vm_id: str, backup_type: str, 
                         backup_file_path: str, checkpoint_id: str = None,
                         parent_checkpoint_id: str = None, file_size: int = 0) -> int:
        """Create a new checkpoint record"""
        
        # Determine chain level
        if backup_type == 'full':
            chain_level = 0
            parent_checkpoint_id = None
        else:
            # Get full checkpoint record for chain level calculation
            data_temp = self._load_checkpoints()
            vm_checkpoints_temp = [
                cp for cp in data_temp['checkpoints'] 
                if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
            ]
            
            if vm_checkpoints_temp:
                vm_checkpoints_temp.sort(key=lambda x: x['backup_date'], reverse=True)
                last_checkpoint_record = vm_checkpoints_temp[0]
                chain_level = last_checkpoint_record['chain_level'] + 1
                parent_checkpoint_id = last_checkpoint_record.get('checkpoint_id')
            else:
                chain_level = 0
                parent_checkpoint_id = None
        
        data = self._load_checkpoints()
        
        checkpoint_record_id = data['next_id']
        new_checkpoint = {
            'id': checkpoint_record_id,
            'vm_name': vm_name,
            'vm_id': vm_id,
            'checkpoint_id': checkpoint_id,
            'backup_type': backup_type,
            'backup_date': datetime.now().isoformat(),
            'backup_file_path': backup_file_path,
            'parent_checkpoint_id': parent_checkpoint_id,
            'chain_level': chain_level,
            'file_size': file_size,
            'compressed': False,
            'verified': False,
            'status': 'active',
            'created_at': datetime.now().isoformat()
        }
        
        data['checkpoints'].append(new_checkpoint)
        data['next_id'] += 1
        self._save_checkpoints(data)
            
        logging.info(f"Created checkpoint record {checkpoint_record_id} for {vm_name} (type: {backup_type}, level: {chain_level})")
        return checkpoint_record_id
    
    def mark_checkpoint_verified(self, checkpoint_record_id: int):
        """Mark a checkpoint as verified"""
        data = self._load_checkpoints()
        
        for checkpoint in data['checkpoints']:
            if checkpoint['id'] == checkpoint_record_id:
                checkpoint['verified'] = True
                break
        
        self._save_checkpoints(data)
    
    def cleanup_old_checkpoints(self, vm_name: str, vm_config: Dict):
        """Clean up old checkpoints based on retention policy"""
        retention = vm_config.get('retention', {})
        
        # Get all checkpoints for this VM
        data = self._load_checkpoints()
        
        checkpoints = [
            cp for cp in data['checkpoints'] 
            if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
        ]
        
        # Sort by backup_date descending
        checkpoints.sort(key=lambda x: x['backup_date'], reverse=True)
        
        # Apply retention rules
        to_delete = []
        
        # Keep daily backups
        keep_daily = retention.get('keep_daily', 7)
        daily_kept = 0
        
        # Keep weekly backups
        keep_weekly = retention.get('keep_weekly', 4)
        weekly_kept = 0
        
        # Keep monthly backups
        keep_monthly = retention.get('keep_monthly', 3)
        monthly_kept = 0
        
        # Max age in days
        max_age_days = retention.get('max_backup_age_days', 90)
        cutoff_date = datetime.now() - timedelta(days=max_age_days)
        
        for checkpoint in checkpoints:
            backup_date = datetime.fromisoformat(checkpoint['backup_date'])
            
            # Delete if too old
            if backup_date < cutoff_date:
                to_delete.append(checkpoint['id'])
                continue
            
            # Apply retention logic (simplified)
            days_old = (datetime.now() - backup_date).days
            
            if days_old <= keep_daily:
                daily_kept += 1
            elif days_old <= keep_daily + (keep_weekly * 7):
                if weekly_kept < keep_weekly:
                    weekly_kept += 1
                else:
                    to_delete.append(checkpoint['id'])
            elif monthly_kept < keep_monthly:
                monthly_kept += 1
            else:
                to_delete.append(checkpoint['id'])
        
        # Mark checkpoints for deletion
        if to_delete:
            for checkpoint in data['checkpoints']:
                if checkpoint['id'] in to_delete:
                    checkpoint['status'] = 'deleted'
            
            self._save_checkpoints(data)
            logging.info(f"Marked {len(to_delete)} old checkpoints for deletion for VM {vm_name}")
    
    def get_backup_stats(self, vm_name: str) -> Dict:
        """Get backup statistics for a VM"""
        data = self._load_checkpoints()
        
        vm_checkpoints = [
            cp for cp in data['checkpoints'] 
            if cp.get('vm_name') == vm_name and cp.get('status') == 'active'
        ]
        
        stats = {}
        for checkpoint in vm_checkpoints:
            backup_type = checkpoint.get('backup_type', 'unknown')
            if backup_type not in stats:
                stats[backup_type] = {'count': 0, 'total_size': 0}
            
            stats[backup_type]['count'] += 1
            stats[backup_type]['total_size'] += checkpoint.get('file_size', 0)
        
        return stats
    
    def validate_checkpoint_chain(self, vm_name: str) -> bool:
        """Validate the integrity of the checkpoint chain"""
        chain = self.get_checkpoint_chain(vm_name)
        
        if not chain:
            return True  # Empty chain is valid
        
        # Check if chain starts with a full backup
        if chain[0]['backup_type'] != 'full':
            logging.error(f"Chain for {vm_name} doesn't start with full backup")
            return False
        
        # Check for gaps in chain levels
        for i, checkpoint in enumerate(chain):
            if checkpoint['chain_level'] != i:
                logging.error(f"Gap in chain level for {vm_name} at position {i}")
                return False
        
        # Check if backup files exist and fix paths if needed
        data = self._load_checkpoints()
        paths_updated = False
        
        for checkpoint in chain:
            backup_file_path = checkpoint['backup_file_path']
            if not os.path.exists(backup_file_path):
                # Try to find files with disk suffix pattern (for backward compatibility)
                import glob
                base_path = backup_file_path.replace('.qcow2', '')
                pattern = f"{base_path}_disk*.qcow2"
                logging.info(f"Looking for backup files with pattern: {pattern}")
                matches = glob.glob(pattern)
                logging.info(f"Found {len(matches)} matches: {matches}")
                
                if matches:
                    logging.info(f"Found backup file with disk suffix: {matches[0]} (original: {backup_file_path})")
                    # Update checkpoint record with actual file path
                    checkpoint['backup_file_path'] = matches[0]
                    
                    # Also update in the main data structure
                    for cp in data['checkpoints']:
                        if cp['id'] == checkpoint['id']:
                            cp['backup_file_path'] = matches[0]
                            paths_updated = True
                            break
                else:
                    logging.error(f"Missing backup file: {backup_file_path}")
                    return False
        
        # Save updated paths if any were changed
        if paths_updated:
            self._save_checkpoints(data)
            logging.info(f"Updated checkpoint file paths for {vm_name}")
        
        return True