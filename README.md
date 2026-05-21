# Tarifas ANEEL — Extrator Automático

Extrai as tarifas homologadas de todas as distribuidoras de energia elétrica do Brasil a partir da base pública da ANEEL.

> **Nota:** O servidor da ANEEL bloqueia requisições de IPs de data center (AWS/GitHub).  
> Por isso, o script roda **localmente no seu Mac** e publica os dados via `git push`.

---

## Dados disponíveis

| Arquivo | Descrição |
|---|---|
| [`data/tarifas_aneel.json`](data/tarifas_aneel.json) | Tarifas por distribuidora (estruturado) |
| [`data/tarifas_aneel.csv`](data/tarifas_aneel.csv) | Uma linha por tarifa (flat) |

### URL pública para consumo externo

```
https://raw.githubusercontent.com/diogoobs/tarifas-aneel/main/data/tarifas_aneel.json
```

---

## Como atualizar os dados

### Opção 1 — Script automático (recomendado)

```bash
cd ~/Downloads/tarifas-aneel

# Extrai todas as distribuidoras e envia para o GitHub
./atualizar.sh

# Ou apenas uma distribuidora
./atualizar.sh ENEL-SP
```

### Opção 2 — Manual

```bash
cd ~/Downloads/tarifas-aneel

# Extrai
python3 scripts/extrator_tarifas_aneel.py --output data/tarifas_aneel.json --csv

# Envia para o GitHub
git add data/
git commit -m "chore: atualiza tarifas ANEEL $(date +%Y-%m-%d)"
git push
```

---

## Filtros aplicados

- **Tipo de outorga:** apenas concessionárias
- **Base tarifária:** Tarifa de Aplicação
- **REH:** resolução homologatória mais recente por distribuidora

---

## Estrutura do JSON

```json
{
  "gerado_em": "2025-05-21T06:12:33+00:00",
  "distribuidoras": {
    "ENEL-SP": {
      "reh": "3477/2025",
      "vigencia_inicio": "2025-04-16",
      "tarifas": [
        {
          "subgrupo": "B1",
          "modalidade": "Convencional",
          "vlr_tusd": 0.12345,
          "vlr_te": 0.45678,
          "vlr_total": 0.58023
        }
      ]
    }
  }
}
```

---

## Fonte dos dados

- **ANEEL Dados Abertos:** https://dadosabertos.aneel.gov.br/dataset/tarifas-distribuidoras-energia-eletrica
- **Licença:** Open Data Commons Open Database License (ODbL)
