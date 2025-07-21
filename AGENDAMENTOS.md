# 🕒 Guia de Agendamentos e Retenção de Backup

Este guia explica como configurar agendamentos de backup e políticas de retenção para suas VMs no sistema oVirt/OLVM.

## 📋 Índice

- [Configuração Básica](#configuração-básica)
- [Tipos de Agendamento](#tipos-de-agendamento)
- [Backup Incremental vs Full](#backup-incremental-vs-full)
- [Políticas de Retenção](#políticas-de-retenção)
- [Exemplos Práticos](#exemplos-práticos)
- [Múltiplos Agendamentos](#múltiplos-agendamentos)

---

## 🚀 Configuração Básica

Para adicionar uma nova VM ao sistema de backup, edite o arquivo `variables.json`:

```json
{
  "vms_config": {
    "minha_vm": {
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
      "auto_restore": false
    }
  }
}
```

---

## 📅 Tipos de Agendamento

### 1. **Simple** - Padrões Básicos

#### ✅ Backup Diário
```json
"schedule": {
  "type": "simple",
  "pattern": "daily",
  "time": "02:00"
}
```
**Resultado**: Backup todos os dias às 02:00

#### ✅ Apenas Dias Úteis (Segunda a Sexta)
```json
"schedule": {
  "type": "simple", 
  "pattern": "weekdays",
  "time": "22:00"
}
```
**Resultado**: Backup segunda a sexta às 22:00

#### ✅ Apenas Finais de Semana
```json
"schedule": {
  "type": "simple",
  "pattern": "weekends", 
  "time": "06:00"
}
```
**Resultado**: Backup sábado e domingo às 06:00

#### ✅ Uma Vez por Semana
```json
"schedule": {
  "type": "simple",
  "pattern": "weekly",
  "day": "sunday",
  "time": "03:00"
}
```
**Resultado**: Backup todo domingo às 03:00

#### ✅ Uma Vez por Mês
```json
"schedule": {
  "type": "simple",
  "pattern": "monthly",
  "day_of_month": 15,
  "time": "01:00"
}
```
**Resultado**: Backup dia 15 de cada mês às 01:00

### 2. **Hourly** - Intervalos de Horas

#### ✅ A Cada 2 Horas (Horário Comercial)
```json
"schedule": {
  "type": "hourly",
  "interval": 2,
  "start_hour": 10,
  "end_hour": 20,
  "days": ["monday","tuesday","wednesday","thursday","friday"],
  "time": "00:00"
}
```
**Resultado**: Backup às 10h, 12h, 14h, 16h, 18h, 20h de segunda a sexta

#### ✅ A Cada 4 Horas (24 horas)
```json
"schedule": {
  "type": "hourly",
  "interval": 4,
  "start_hour": 0,
  "end_hour": 23,
  "time": "00:00"
}
```
**Resultado**: Backup às 00h, 04h, 08h, 12h, 16h, 20h todos os dias

#### ✅ De Hora em Hora (Horário Específico)
```json
"schedule": {
  "type": "hourly",
  "interval": 1,
  "start_hour": 9,
  "end_hour": 17,
  "days": ["monday","tuesday","wednesday","thursday","friday"],
  "time": "00:00"
}
```
**Resultado**: Backup de hora em hora das 09h às 17h, segunda a sexta

### 3. **Advanced** - Agendamentos Complexos

#### ✅ Dias Específicos do Mês
```json
"schedule": {
  "type": "advanced",
  "pattern": "monthly",
  "days": [1, 15],
  "time": "03:00"
}
```
**Resultado**: Backup nos dias 1 e 15 de cada mês às 03:00

#### ✅ Semanas Específicas
```json
"schedule": {
  "type": "advanced",
  "pattern": "monthly", 
  "weeks": [1, 3],
  "day": "sunday",
  "time": "02:00"
}
```
**Resultado**: Backup nos domingos da 1ª e 3ª semana do mês às 02:00

### 4. **Cron** - Expressões Personalizadas

#### ✅ Expressão Cron Direta
```json
"schedule": {
  "type": "cron",
  "expression": "0 */3 * * *"
}
```
**Resultado**: Backup a cada 3 horas

#### ✅ Agendamento Complexo
```json
"schedule": {
  "type": "cron", 
  "expression": "15 6,18 * * 1-5"
}
```
**Resultado**: Backup às 06:15 e 18:15 de segunda a sexta

### 5. **Legacy** - Formato 7-bit

#### ✅ Padrão Semanal com 7 Bits
```json
"schedule": {
  "type": "legacy",
  "backup_policy": "1111100",
  "time": "02:00"
}
```
**Formato**: `[Seg][Ter][Qua][Qui][Sex][Sab][Dom]`
- `1` = Fazer backup
- `0` = Não fazer backup

**Exemplos**:
- `"1111111"` = Todos os dias
- `"1111100"` = Segunda a sexta
- `"0000011"` = Sábado e domingo
- `"1000000"` = Apenas segunda-feira

---

## 🔄 Backup Incremental vs Full

### Configuração de Tipos de Backup

```json
"backup_type": {
  "incremental_enabled": true,
  "full_backup_frequency": "weekly",
  "full_backup_day": "sunday"
}
```

### Opções Disponíveis

#### ✅ **incremental_enabled**
- `true`: Habilita backups incrementais
- `false`: Sempre backup full

#### ✅ **full_backup_frequency**
- `"daily"`: Sempre backup full
- `"weekly"`: Backup full uma vez por semana
- `"always"`: Sempre backup full

#### ✅ **full_backup_day**
- `"sunday"`, `"monday"`, etc.: Dia da semana para backup full

### Exemplos de Configuração

#### 🔹 Backup Incremental com Full Semanal
```json
"backup_type": {
  "incremental_enabled": true,
  "full_backup_frequency": "weekly", 
  "full_backup_day": "sunday"
}
```
**Resultado**: Incremental de segunda a sábado, full no domingo

#### 🔹 Sempre Backup Full
```json
"backup_type": {
  "incremental_enabled": false
}
```
**Resultado**: Todos os backups são full

#### 🔹 Backup Diário Full
```json
"backup_type": {
  "incremental_enabled": true,
  "full_backup_frequency": "daily"
}
```
**Resultado**: Backup full todos os dias

---

## 🗂️ Políticas de Retenção

### Configuração Básica
```json
"retention": {
  "keep_daily": 7,
  "keep_weekly": 4,
  "keep_monthly": 3,
  "max_backup_age_days": 90
}
```

### Parâmetros Disponíveis

#### ✅ **keep_daily**
Quantos backups diários manter
- Exemplo: `7` = Manter últimos 7 dias

#### ✅ **keep_weekly**  
Quantos backups semanais manter após período daily
- Exemplo: `4` = Manter 4 semanas após os 7 dias

#### ✅ **keep_monthly**
Quantos backups mensais manter após período weekly
- Exemplo: `3` = Manter 3 meses após as 4 semanas

#### ✅ **max_backup_age_days**
Idade máxima de qualquer backup
- Exemplo: `90` = Deletar backups com mais de 90 dias

### Exemplos de Políticas

#### 🔹 Retenção Conservadora (Longo Prazo)
```json
"retention": {
  "keep_daily": 14,
  "keep_weekly": 8, 
  "keep_monthly": 12,
  "max_backup_age_days": 365
}
```
**Resultado**: 14 dias + 8 semanas + 12 meses (1 ano total)

#### 🔹 Retenção Agressiva (Curto Prazo)
```json
"retention": {
  "keep_daily": 3,
  "keep_weekly": 2,
  "keep_monthly": 1, 
  "max_backup_age_days": 30
}
```
**Resultado**: 3 dias + 2 semanas + 1 mês (máximo 30 dias)

#### 🔹 Retenção Balanceada
```json
"retention": {
  "keep_daily": 7,
  "keep_weekly": 4,
  "keep_monthly": 3,
  "max_backup_age_days": 90
}
```
**Resultado**: 1 semana + 1 mês + 3 meses (máximo 90 dias)

---

## 💡 Exemplos Práticos

### 🎯 **Servidor de Produção** - Backup Crítico
```json
"servidor_producao": {
  "enabled": true,
  "schedule": {
    "type": "hourly",
    "interval": 4,
    "start_hour": 0,
    "end_hour": 23,
    "time": "00:00"
  },
  "backup_type": {
    "incremental_enabled": true,
    "full_backup_frequency": "weekly",
    "full_backup_day": "sunday"
  },
  "retention": {
    "keep_daily": 14,
    "keep_weekly": 8,
    "keep_monthly": 6,
    "max_backup_age_days": 180
  },
  "auto_restore": false
}
```
**Resultado**: Backup a cada 4 horas, incremental + full semanal, retenção 6 meses

### 🎯 **Servidor de Desenvolvimento** - Backup Simples
```json
"servidor_dev": {
  "enabled": true,
  "schedule": {
    "type": "simple",
    "pattern": "weekdays",
    "time": "23:00"
  },
  "backup_type": {
    "incremental_enabled": true,
    "full_backup_frequency": "weekly",
    "full_backup_day": "friday"
  },
  "retention": {
    "keep_daily": 5,
    "keep_weekly": 2,
    "keep_monthly": 1,
    "max_backup_age_days": 30
  },
  "auto_restore": false
}
```
**Resultado**: Backup nos dias úteis às 23h, retenção 1 mês

### 🎯 **Servidor de Arquivo** - Backup Intensivo
```json
"servidor_arquivos": {
  "enabled": true,
  "schedule": {
    "type": "hourly",
    "interval": 2,
    "start_hour": 10,
    "end_hour": 20,
    "days": ["monday","tuesday","wednesday","thursday","friday"],
    "time": "00:00"
  },
  "backup_type": {
    "incremental_enabled": true,
    "full_backup_frequency": "weekly",
    "full_backup_day": "saturday"
  },
  "retention": {
    "keep_daily": 10,
    "keep_weekly": 6,
    "keep_monthly": 4,
    "max_backup_age_days": 120
  },
  "auto_restore": true
}
```
**Resultado**: Backup a cada 2h das 10-20h em dias úteis + full aos sábados

### 🎯 **Base de Dados** - Backup com Cron Personalizado
```json
"servidor_db": {
  "enabled": true,
  "schedule": {
    "type": "cron",
    "expression": "0 2,14 * * *"
  },
  "backup_type": {
    "incremental_enabled": true,
    "full_backup_frequency": "weekly",
    "full_backup_day": "sunday"
  },
  "retention": {
    "keep_daily": 7,
    "keep_weekly": 4,
    "keep_monthly": 6,
    "max_backup_age_days": 180
  },
  "auto_restore": false
}
```
**Resultado**: Backup às 02:00 e 14:00 todos os dias

---

## 🔄 Múltiplos Agendamentos

Para ter diferentes agendamentos (incremental + full), crie entradas separadas:

### Exemplo: Incremental Frequente + Full Semanal

#### VM para Backup Incremental
```json
"minha_vm_incremental": {
  "enabled": true,
  "schedule": {
    "type": "hourly",
    "interval": 2,
    "start_hour": 10,
    "end_hour": 20,
    "days": ["monday","tuesday","wednesday","thursday","friday"],
    "time": "00:00"
  },
  "backup_type": {
    "incremental_enabled": true
  },
  "retention": {
    "keep_daily": 7,
    "keep_weekly": 4,
    "keep_monthly": 3,
    "max_backup_age_days": 90
  }
}
```

#### VM para Backup Full
```json
"minha_vm_full": {
  "enabled": true,
  "schedule": {
    "type": "simple",
    "pattern": "weekly",
    "day": "saturday",
    "time": "06:00"
  },
  "backup_type": {
    "incremental_enabled": false
  },
  "retention": {
    "keep_daily": 0,
    "keep_weekly": 8,
    "keep_monthly": 6,
    "max_backup_age_days": 180
  }
}
```

---

## 🛠️ Aplicar Configurações

Após editar o `variables.json`:

1. **Importe as variáveis**:
   ```bash
   docker compose exec airflow-webserver airflow variables import /opt/airflow/variables.json
   ```

2. **Gere os DAGs**:
   ```bash
   docker compose exec airflow-webserver airflow dags trigger generate_complete_dynamic_dags
   ```

3. **Verifique no Airflow UI**:
   - Acesse `http://localhost:8080`
   - Procure pelos DAGs `backup_nome_da_vm`

---

## 📋 Checklist de Configuração

### ✅ Antes de Configurar
- [ ] VM existe no oVirt e está acessível
- [ ] Espaço suficiente em `/backup` (ou `BACKUP_DIR`)
- [ ] Certificados oVirt atualizados
- [ ] Conectividade de rede com oVirt managers

### ✅ Configuração Mínima
- [ ] `enabled: true`
- [ ] `schedule` definido
- [ ] `backup_type` configurado
- [ ] `retention` definida

### ✅ Após Configurar
- [ ] Variables importadas no Airflow
- [ ] DAGs gerados com sucesso
- [ ] DAG aparece no Airflow UI
- [ ] Primeiro backup executado com sucesso

---

## 🚨 Dicas Importantes

### ⚠️ **Horários de Backup**
- Evite horários de pico da VM
- Considere o tempo de execução de backups anteriores
- Deixe intervalo entre backups incrementais e full

### ⚠️ **Retenção**
- Monitore espaço em disco regularmente
- Ajuste retenção baseado no crescimento dos dados
- Teste restores periodicamente

### ⚠️ **Performance**
- Backups incrementais são mais rápidos
- Backups full consomem mais espaço e tempo
- Múltiplos backups simultâneos podem impactar performance

### ⚠️ **Troubleshooting**
- Verifique logs em `/logs/` se backup falhar
- Use `docker compose logs airflow-scheduler` para debug
- Snapshots órfãos podem ser limpos com scripts oVirt

---

## 📞 Suporte

Em caso de problemas:

1. **Verifique logs**: `/logs/dag_id=backup_vm_name/`
2. **Status dos containers**: `docker compose ps`
3. **Logs do scheduler**: `docker compose logs airflow-scheduler`
4. **Variables importadas**: `airflow variables list`

---

**🎉 Agora você pode configurar agendamentos complexos de backup para suas VMs!**