# Engenharia Reversa Omron HBF-222T (Protocolo WLC 3.0)

Esta síntese consolida a engenharia reversa da balança Omron HBF-222T. Use estas informações para replicar a comunicação em qualquer projeto BLE (Python/Bleak, ESP32, Android Nativo).

---

## 1. Identificação e Conectividade (GATT)
* **Modelo:** HBF-222T (Séries E, AP ou LA).
* **UUID de Unlock (Cadeado):** `b305b680-aee7-11e1-a730-0002a5d5c51b`
* **UUID de Escrita (TX):** `db5b55e0-aee7-11e1-965e-0002a5d5c51b`
* **UUIDs de Resposta (RX - 4 Canais):** 
    1. `49123040-aee8-11e1-a74d-0002a5d5c51b` (CH0: Sistema/ACK)
    2. `4d0bf320-aee8-11e1-a0d9-0002a5d5c51b` (CH1: Eventos Tempo Real)
    3. `5128ce60-aee8-11e1-b84b-0002a5d5c51b` (CH2: Dados Vitais - Bulk)
    4. `560f1420-aee8-11e1-8184-0002a5d5c51b` (CH3: Logs/Debug)

## 2. Handshake de Desbloqueio (Unlock)
A balança ignora comandos se não for desbloqueada após a conexão:
1. Ativar Notificações no `UUID_UNLOCK`.
2. **Enviar Desafio:** Escrever `[0x02]` + 16 bytes zero no `UUID_UNLOCK`.
3. **Enviar Chave:** Após receber a notificação de resposta, escrever `[0x01]` + `a635f6b5d2f947a3a7a3ebcd6b2ae964` (Chave mestre da HBF-222T) no `UUID_UNLOCK`.

## 3. Anatomia do Comando de Leitura (GETDATA)
Comando oficial: `[08][01] [ADDR_3BYTES_LE] [20][00] [00] [BCC]`
* **ADDR (Little Endian):**
    * P1: `C0 02 00` (0x2C0)
    * P2: `A0 06 00` (0x6A0)
    * P3: `80 0A 00` (0xA80)
    * P4: `60 0E 00` (0xE60)
* **20 00:** Tamanho do bloco (32 bytes).
* **BCC:** Checksum XOR de todos os 7 bytes anteriores.

## 4. Decodificação de Bits (Big Endian Bit-Packing)
Os dados estão compactados em bits dentro do Payload de 32 bytes da resposta (após o header `28 81`). **Diferente das notificações real-time, os dados da EEPROM usam Big Endian para os campos de bits.**

| Dado | Offset (Payload) | Bits | Start Bit | Cálculo Final |
| :--- | :--- | :--- | :--- | :--- |
| **Peso (kg)** | 26 | 12 | 4 | `(valor_extraido) * 0.05` |
| **Gordura (%)** | 2 | 10 | 6 | `(valor_extraido) * 0.1` |
| **Músculo (%)** | 6 | 10 | 6 | `(valor_extraido) * 0.1` |
| **BMI (IMC)** | 8 | 10 | 6 | `(valor_extraido) * 0.1` |
| **BMR (Kcal)** | 4 | 12 | 4 | `(valor_extraido)` |
| **Visceral** | 10 | 7 | 0 | `(valor_extraido)` |

**Data e Hora (Parsing):**
* **Ano:** `(Payload[7] & 0x3F) + 2000`
* **Minuto:** `(Payload[9] & 0x3F)`
* **Mês:** `(Payload[11] & 0x0F)`
* **Dia:** `(Payload[12] >> 3) & 0x1F`
* **Hora:** `((Payload[12] << 8 | Payload[13]) >> 6) & 0x1F` (Nota: Cálculo LE dos bytes 12/13)

## 5. Estratégia de Buffer BLE
Como o pacote tem 40 bytes, ele chega fragmentado nos 4 canais. O segredo é unificar os canais `RX CH0` a `CH3` em um único buffer e buscar pela assinatura `28 81`.
