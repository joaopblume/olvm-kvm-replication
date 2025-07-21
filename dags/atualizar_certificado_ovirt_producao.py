from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Variable
import requests
import os
import logging
import tempfile
import shutil
from urllib.parse import urlparse

# Configurações do DAG
default_args = {
    'owner': 'ovirt-admin',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': timedelta(minutes=2),
}

dag = DAG(
    'atualiza_certificado_prd_source',
    default_args=default_args,
    description='Atualiza certificados CA do OLVM Produção (Source)',
    schedule=None,  # Manual
    catchup=False,
    max_active_runs=1,
    tags=['certificado', 'source', 'prd']
)

def get_ca_url_from_ovirt_url(ovirt_url):
    """Constrói URL do CA baseada na URL do oVirt"""
    parsed = urlparse(ovirt_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    ca_url = f"{base_url}/ovirt-engine/services/pki-resource?resource=ca-certificate&format=X509-PEM-CA"
    return ca_url

def validate_environment(**context):
    """Valida ambiente e configurações antes de iniciar"""
    
    # Carregar configurações
    source_ovirt_url = Variable.get('source_ovirt_url')
    cert_path = Variable.get('source_ovirt_cert_path')
    
    # Validar URL
    if not source_ovirt_url or 'http' not in source_ovirt_url:
        raise ValueError(f'URL do OLVM inválida: {source_ovirt_url}')
    
    # Validar diretório de destino
    cert_dir = os.path.dirname(cert_path)
    if not os.path.exists(cert_dir):
        logging.info(f'Criando diretório: {cert_dir}')
        os.makedirs(cert_dir, exist_ok=True)
    
    # Construir URL do CA
    ca_url = get_ca_url_from_ovirt_url(source_ovirt_url)
    
    logging.info(f'OLVM URL: {source_ovirt_url}')
    logging.info(f'CA URL: {ca_url}')
    logging.info(f'Destino: {cert_path}')
    
    return {
        'ca_url': ca_url,
        'cert_path': cert_path,
        'source_url': source_ovirt_url
    }

def backup_current_certificate(**context):
    """Faz backup do certificado atual se existir"""
    
    cert_info = context['task_instance'].xcom_pull(task_ids='validate_environment')
    cert_path = cert_info['cert_path']
    
    if os.path.exists(cert_path):
        backup_path = f"{cert_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(cert_path, backup_path)
        logging.info(f'Backup criado: {backup_path}')
        
        return {
            **cert_info,
            'backup_created': backup_path
        }
    else:
        logging.info('Certificado atual não existe, não será feito backup')
        return {
            **cert_info,
            'backup_created': None
        }

def download_ca_certificate(**context):
    """Baixa o certificado CA do OLVM"""
    
    cert_info = context['task_instance'].xcom_pull(task_ids='backup_current_certificate')
    ca_url = cert_info['ca_url']
    
    # Criar arquivo temporário
    with tempfile.NamedTemporaryFile(mode='w+b', suffix='.pem', delete=False) as tmp_file:
        tmp_path = tmp_file.name
    
    try:
        logging.info(f'Baixando certificado de: {ca_url}')
        
        # Download com verificação SSL desabilitada (necessário para renovação)
        response = requests.get(
            ca_url,
            verify=False,  # Necessário quando o cert atual está expirado
            timeout=30,
            stream=True
        )
        response.raise_for_status()
        
        # Salvar no arquivo temporário
        with open(tmp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        # Validar se arquivo não está vazio
        if os.path.getsize(tmp_path) == 0:
            raise ValueError('Certificado baixado está vazio')
        
        # Validar se parece com um certificado PEM
        with open(tmp_path, 'r') as f:
            content = f.read()
            if '-----BEGIN CERTIFICATE-----' not in content:
                raise ValueError('Arquivo baixado não parece ser um certificado PEM válido')
        
        logging.info(f'Certificado baixado com sucesso: {os.path.getsize(tmp_path)} bytes')
        
        return {
            **cert_info,
            'tmp_cert_path': tmp_path,
            'cert_size': os.path.getsize(tmp_path)
        }
        
    except Exception as e:
        # Limpar arquivo temporário em caso de erro
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise Exception(f'Falha ao baixar certificado: {str(e)}')

def install_new_certificate(**context):
    """Instala o novo certificado no local final"""
    
    cert_info = context['task_instance'].xcom_pull(task_ids='download_ca_certificate')
    tmp_cert_path = cert_info['tmp_cert_path']
    final_cert_path = cert_info['cert_path']
    
    try:
        # Mover certificado temporário para local final
        shutil.move(tmp_cert_path, final_cert_path)
        
        # Ajustar permissões
        os.chmod(final_cert_path, 0o644)
        
        # Validar instalação
        if not os.path.exists(final_cert_path):
            raise Exception('Falha ao instalar certificado')
        
        final_size = os.path.getsize(final_cert_path)
        logging.info(f'Certificado instalado com sucesso: {final_cert_path} ({final_size} bytes)')
        
        return {
            **cert_info,
            'installation_completed': True,
            'final_cert_size': final_size
        }
        
    except Exception as e:
        # Tentar restaurar backup se algo der errado
        backup_path = cert_info.get('backup_created')
        if backup_path and os.path.exists(backup_path):
            logging.error(f'Erro na instalação, restaurando backup: {backup_path}')
            shutil.copy2(backup_path, final_cert_path)
        
        # Limpar arquivo temporário
        if os.path.exists(tmp_cert_path):
            os.unlink(tmp_cert_path)
            
        raise Exception(f'Falha ao instalar certificado: {str(e)}')

def test_new_certificate(**context):
    """Testa se o novo certificado funciona corretamente"""
    
    cert_info = context['task_instance'].xcom_pull(task_ids='install_new_certificate')
    source_url = cert_info['source_url']
    cert_path = cert_info['cert_path']
    
    try:
        # Fazer uma requisição de teste usando o novo certificado
        test_url = f"{source_url.replace('/ovirt-engine/api', '')}/ovirt-engine/api"
        
        response = requests.get(
            test_url,
            verify=cert_path,
            timeout=10
        )
        
        if response.status_code in [200, 401]:  # 401 é OK (sem autenticação)
            logging.info('Teste do certificado: ✓ SUCESSO')
            return {
                **cert_info,
                'test_result': 'success',
                'test_status_code': response.status_code
            }
        else:
            raise Exception(f'Teste falhou: HTTP {response.status_code}')
            
    except requests.exceptions.SSLError as e:
        raise Exception(f'Erro SSL no teste do certificado: {str(e)}')
    except Exception as e:
        raise Exception(f'Falha no teste do certificado: {str(e)}')

def cleanup_old_backups(**context):
    """Remove backups antigos (manter apenas os 5 mais recentes)"""
    
    cert_info = context['task_instance'].xcom_pull(task_ids='test_new_certificate')
    cert_path = cert_info['cert_path']
    cert_dir = os.path.dirname(cert_path)
    cert_name = os.path.basename(cert_path)
    
    # Encontrar todos os backups
    backup_pattern = f"{cert_name}.backup."
    backups = []
    
    for filename in os.listdir(cert_dir):
        if filename.startswith(backup_pattern):
            backup_path = os.path.join(cert_dir, filename)
            backups.append((backup_path, os.path.getmtime(backup_path)))
    
    # Ordenar por data (mais recente primeiro)
    backups.sort(key=lambda x: x[1], reverse=True)
    
    # Manter apenas os 5 mais recentes
    if len(backups) > 5:
        for backup_path, _ in backups[5:]:
            os.unlink(backup_path)
            logging.info(f'Backup antigo removido: {backup_path}')
    
    logging.info(f'Limpeza concluída. {len(backups[:5])} backups mantidos.')

# Definição das tasks
validate_task = PythonOperator(
    task_id='validate_environment',
    python_callable=validate_environment,
    dag=dag
)

backup_task = PythonOperator(
    task_id='backup_current_certificate',
    python_callable=backup_current_certificate,
    dag=dag
)

download_task = PythonOperator(
    task_id='download_ca_certificate',
    python_callable=download_ca_certificate,
    dag=dag
)

install_task = PythonOperator(
    task_id='install_new_certificate',
    python_callable=install_new_certificate,
    dag=dag
)

test_task = PythonOperator(
    task_id='test_new_certificate',
    python_callable=test_new_certificate,
    dag=dag
)

cleanup_task = PythonOperator(
    task_id='cleanup_old_backups',
    python_callable=cleanup_old_backups,
    dag=dag
)

# Definir fluxo
validate_task >> backup_task >> download_task >> install_task >> test_task >> cleanup_task