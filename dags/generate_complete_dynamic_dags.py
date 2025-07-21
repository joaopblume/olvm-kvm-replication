"""
COMPLETE Dynamic DAG Generator

This creates YAML files from variables.json and generates complete DAGs with ALL functionality
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import logging
import os
import json
import re
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

# DAG Configuration
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
    'catchup': False,
}

dag = DAG(
    'gerar_dags_dinamicas',
    default_args=default_args,
    description='Generate COMPLETE dynamic DAGs with YAML and Jinja2',
    schedule=None,
    max_active_runs=1,
    tags=['gerador', 'manual', 'dinamico'],
)

def to_python_identifier(value):
    """Convert string to valid Python identifier"""
    identifier = re.sub(r'[^a-zA-Z0-9_]', '_', str(value))
    if identifier and identifier[0].isdigit():
        identifier = f"vm_{identifier}"
    return identifier.lower()

def convert_backup_policy_to_cron(policy, time_str):
    """Convert 7-bit backup policy to cron expression"""
    hour, minute = time_str.split(':')
    hour = int(hour)
    minute = int(minute)
    
    # 7-bit policy: Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
    # Cron format: Sunday=0, Monday=1, Tuesday=2, Wednesday=3, Thursday=4, Friday=5, Saturday=6
    days = []
    for i in range(7):
        if policy[i] == '1':
            if i == 6:  # Sunday in policy (index 6) → Sunday in cron (0)
                days.append('0')
            else:  # Monday-Saturday in policy (index 0-5) → Monday-Saturday in cron (1-6)
                days.append(str(i + 1))
    
    if not days:
        return f"0 2 * * *"  # Default daily
    
    if len(days) == 7:
        return f"{minute} {hour} * * *"
    else:
        day_list = ','.join(sorted(days))
        return f"{minute} {hour} * * {day_list}"

def convert_schedule_to_cron(schedule_config):
    """Convert all schedule types to cron expressions"""
    schedule_type = schedule_config.get('type', 'legacy')
    schedule_time = schedule_config.get('time', '02:00')
    hour, minute = schedule_time.split(':')
    hour, minute = int(hour), int(minute)
    
    if schedule_type == "legacy":
        backup_policy = schedule_config.get('backup_policy', '1111111')
        return convert_backup_policy_to_cron(backup_policy, schedule_time)
    
    elif schedule_type == "simple":
        pattern = schedule_config.get('pattern', 'daily')
        
        if pattern == "daily":
            return f"{minute} {hour} * * *"
        
        elif pattern == "weekdays":
            return f"{minute} {hour} * * 1-5"  # Monday to Friday
        
        elif pattern == "weekends":
            return f"{minute} {hour} * * 0,6"  # Saturday and Sunday
        
        elif pattern == "weekly":
            day = schedule_config.get('day', 'sunday')
            day_map = {
                'sunday': '0', 'monday': '1', 'tuesday': '2', 'wednesday': '3',
                'thursday': '4', 'friday': '5', 'saturday': '6'
            }
            cron_day = day_map.get(day.lower(), '0')
            return f"{minute} {hour} * * {cron_day}"
        
        elif pattern == "biweekly":
            # Every 2 weeks (using week 1 and 3 of month as approximation)
            day = schedule_config.get('day', 'sunday')
            day_map = {
                'sunday': '0', 'monday': '1', 'tuesday': '2', 'wednesday': '3',
                'thursday': '4', 'friday': '5', 'saturday': '6'
            }
            cron_day = day_map.get(day.lower(), '0')
            return f"{minute} {hour} 1-7,15-21 * {cron_day}"  # Week 1 and 3
        
        elif pattern == "monthly":
            day_of_month = schedule_config.get('day_of_month', 1)
            return f"{minute} {hour} {day_of_month} * *"
        
        else:
            # Unknown pattern, default to daily
            return f"{minute} {hour} * * *"
    
    elif schedule_type == "hourly":
        # Every N hours within a time range
        interval = schedule_config.get('interval', 1)
        start_hour = schedule_config.get('start_hour', 0)
        end_hour = schedule_config.get('end_hour', 23)
        days = schedule_config.get('days', None)  # None means use all days by default
        
        # Generate hours list based on interval
        hours = []
        current_hour = start_hour
        while current_hour <= end_hour:
            hours.append(str(current_hour))
            current_hour += interval
        
        if not hours:
            hours = [str(hour)]  # Fallback to specified time
        
        hours_str = ','.join(hours)
        
        # Convert day names to cron format
        day_map = {
            'sunday': '0', 'monday': '1', 'tuesday': '2', 'wednesday': '3',
            'thursday': '4', 'friday': '5', 'saturday': '6'
        }
        
        if days is None:
            days_str = '*'  # All days if not specified
        elif isinstance(days, list) and days:
            day_numbers = []
            for day in days:
                if isinstance(day, str) and day.lower() in day_map:
                    day_numbers.append(day_map[day.lower()])
                elif isinstance(day, int) and 0 <= day <= 6:
                    day_numbers.append(str(day))
            
            if day_numbers:
                days_str = ','.join(sorted(set(day_numbers)))
            else:
                days_str = '*'  # All days if none valid
        else:
            days_str = '*'  # All days if not specified properly
        
        return f"{minute} {hours_str} * * {days_str}"
    
    elif schedule_type == "advanced":
        pattern = schedule_config.get('pattern', 'monthly')
        
        if pattern == "monthly":
            # Specific weeks + day of week (check first)
            weeks = schedule_config.get('weeks', [])
            week_day = schedule_config.get('day', 'sunday')
            if weeks:
                day_map = {
                    'sunday': '0', 'monday': '1', 'tuesday': '2', 'wednesday': '3',
                    'thursday': '4', 'friday': '5', 'saturday': '6'
                }
                cron_day = day_map.get(week_day.lower(), '0')
                
                # Convert weeks to day ranges (approximation)
                day_ranges = []
                for week in weeks:
                    start_day = (week - 1) * 7 + 1
                    end_day = min(week * 7, 31)
                    day_ranges.append(f"{start_day}-{end_day}")
                
                days_str = ','.join(day_ranges)
                return f"{minute} {hour} {days_str} * {cron_day}"
            
            # Specific days of month (if no weeks specified)
            days = schedule_config.get('days', [1])
            if isinstance(days, list) and days:
                days_str = ','.join(map(str, sorted(days)))
                return f"{minute} {hour} {days_str} * *"
        
        # Fallback to daily if advanced pattern not recognized
        return f"{minute} {hour} * * *"
    
    elif schedule_type == "cron":
        # Direct cron expression
        expression = schedule_config.get('expression', f"{minute} {hour} * * *")
        return expression
    
    # Default fallback for unknown types
    return f"{minute} {hour} * * *"

def generate_yaml_files(**context):
    """Generate YAML files from variables.json"""
    variables_file = '/opt/airflow/variables.json'
    yaml_dir = Path('/opt/airflow/includes/virtual_machines')
    
    # Create directories
    yaml_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean old YAML files
    for yaml_file in yaml_dir.glob("*.yaml"):
        yaml_file.unlink()
    
    # Read variables.json
    with open(variables_file, 'r') as f:
        data = json.load(f)
    
    vms_config = data.get('vms_config', {})
    if not vms_config:
        raise RuntimeError("No VM configurations found in variables.json")
    
    generated_count = 0
    
    for vm_name, vm_config in vms_config.items():
        if not vm_config.get('enabled', True):
            logging.info(f"Skipping disabled VM: {vm_name}")
            continue
        
        logging.info(f"Generating YAML for VM: {vm_name}")
        
        # Extract schedule configuration
        schedule_config = vm_config.get('schedule', {})
        schedule_type = schedule_config.get('type', 'legacy')
        schedule_time = schedule_config.get('time', '02:00')
        
        # Generate cron expression using new unified function
        cron_expression = convert_schedule_to_cron(schedule_config)
        
        # Generate DAG ID
        dag_id = f"backup_{to_python_identifier(vm_name)}"
        
        # Create YAML content
        yaml_content = {
            'vm_name': vm_name,
            'dag_id': dag_id,
            'enabled': vm_config.get('enabled', True),
            'schedule': {
                'type': schedule_type,
                'time': schedule_time,
                'cron_expression': cron_expression,
                'pattern': schedule_config.get('pattern', 'daily')
            },
            'backup_type': vm_config.get('backup_type', {
                'incremental_enabled': True,
                'full_backup_frequency': 'weekly',
                'full_backup_day': 'sunday'
            }),
            'retention': vm_config.get('retention', {
                'keep_daily': 7,
                'keep_weekly': 4,
                'keep_monthly': 3,
                'max_backup_age_days': 90
            }),
            'auto_restore': vm_config.get('auto_restore', False),
            'retries': vm_config.get('retries', 0),
            'retry_delay_minutes': vm_config.get('retry_delay_minutes', 5),
            'vm_config': vm_config,
            'generation_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Write YAML file
        yaml_file = yaml_dir / f"{vm_name}.yaml"
        with open(yaml_file, 'w') as f:
            yaml.dump(yaml_content, f, default_flow_style=False, indent=2)
        
        logging.info(f"Generated YAML: {yaml_file}")
        generated_count += 1
    
    logging.info(f"Generated {generated_count} YAML files")
    context['task_instance'].xcom_push(key='yaml_count', value=generated_count)
    context['task_instance'].xcom_push(key='yaml_dir', value=str(yaml_dir))

def generate_dags_from_yaml(**context):
    """Generate complete DAGs from YAML files using Jinja2 template"""
    yaml_dir = Path(context['task_instance'].xcom_pull(key='yaml_dir', task_ids='generate_yaml_files'))
    output_dir = Path('/opt/airflow/dags')
    template_dir = Path('/opt/airflow/includes/templates')
    
    # Setup Jinja2 environment
    jinja_env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True
    )
    
    # Add custom filters
    jinja_env.filters['to_python_identifier'] = to_python_identifier
    
    def python_json(obj):
        """Convert object to JSON with Python boolean values"""
        json_str = json.dumps(obj, indent=2)
        # Replace JSON booleans with Python booleans
        json_str = json_str.replace('true', 'True').replace('false', 'False').replace('null', 'None')
        return json_str
    
    jinja_env.filters['python_json'] = python_json
    
    # Load template
    template = jinja_env.get_template('complete_vm_dag.jinja2')
    
    # Process each YAML file
    yaml_files = list(yaml_dir.glob("*.yaml"))
    generated_count = 0
    
    for yaml_file in yaml_files:
        try:
            logging.info(f"Processing YAML file: {yaml_file}")
            
            # Load YAML
            with open(yaml_file, 'r') as f:
                vm_data = yaml.safe_load(f)
            
            vm_name = vm_data['vm_name']
            dag_id = vm_data['dag_id']
            
            logging.info(f"Generating DAG for VM: {vm_name}")
            
            # Render template
            dag_content = template.render(
                vm_name=vm_name,
                dag_id=dag_id,
                cron_expression=vm_data['schedule']['cron_expression'],
                vm_config=vm_data['vm_config'],
                generation_time=vm_data['generation_time']
            )
            
            # Write DAG file
            dag_file = output_dir / f"{dag_id}.py"
            with open(dag_file, 'w') as f:
                f.write(dag_content)
            
            logging.info(f"Generated complete DAG: {dag_file}")
            generated_count += 1
            
        except Exception as e:
            logging.error(f"Failed to generate DAG from {yaml_file}: {str(e)}")
            raise RuntimeError(f"Failed to generate DAG from {yaml_file}: {str(e)}")
    
    logging.info(f"Generated {generated_count} complete DAGs with ALL functionality")
    context['task_instance'].xcom_push(key='dag_count', value=generated_count)

def log_final_results(**context):
    """Log final results"""
    yaml_count = context['task_instance'].xcom_pull(key='yaml_count', task_ids='generate_yaml_files')
    dag_count = context['task_instance'].xcom_pull(key='dag_count', task_ids='generate_dags_from_yaml')
    
    logging.info("=" * 60)
    logging.info("COMPLETE DYNAMIC DAG GENERATION FINISHED")
    logging.info("=" * 60)
    logging.info(f"YAML files generated: {yaml_count}")
    logging.info(f"Complete DAGs generated: {dag_count}")
    logging.info("=" * 60)
    logging.info("Each DAG includes:")
    logging.info("  BACKUP: validate_task >> vm_info_task >> lock_check_task >> snapshot_task >> backup_task >> cleanup_task")
    logging.info("  RESTORE: validate_restore_task >> detect_backups_task >> process_restores_task (if auto_restore enabled)")
    logging.info("=" * 60)
    logging.info("SUCCESS! Refresh Airflow UI to see your complete DAGs!")
    logging.info("=" * 60)

# Define tasks
generate_yaml_task = PythonOperator(
    task_id='generate_yaml_files',
    python_callable=generate_yaml_files,
    dag=dag,
)

generate_dags_task = PythonOperator(
    task_id='generate_dags_from_yaml',
    python_callable=generate_dags_from_yaml,
    dag=dag,
)

log_results_task = PythonOperator(
    task_id='log_final_results',
    python_callable=log_final_results,
    dag=dag,
)

# Task dependencies
generate_yaml_task >> generate_dags_task >> log_results_task