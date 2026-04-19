import sys
import asyncio
import struct
import datetime
import logging
from sharedDriver import sharedDeviceDriverCode

logger = logging.getLogger("omblepy")

class deviceSpecificDriver(sharedDeviceDriverCode):
    """
    Driver HBF-222T: Implementação otimizada para o protocolo WLC 3.0.
    """

    # Configurações do Dispositivo
    deviceEndianess = "big"
    recordByteSize  = 32
    
    # Chave Mestre de Desbloqueio (HBF-222T)
    UNLOCK_KEY = bytes.fromhex("a635f6b5d2f947a3a7a3ebcd6b2ae964")
    
    # Mapa de Memória: Perfil -> Endereço EEPROM
    # P1: 0x2C0, P2: 0x6A0, P3: 0xA80, P4: 0xE60
    PROFILE_ADDRESSES = {
        1: 0x02C0,
        2: 0x06A0,
        3: 0x0A80,
        4: 0x0E60
    }

    def __init__(self):
        super().__init__()

    async def _custom_unlock(self, btobj):
        """Executa o handshake de segurança obrigatório da Omron."""
        logger.debug("HBF-222T: Iniciando protocolo de desbloqueio...")
        
        # Acesso ao bleClient global gerenciado pelo omblepy.py
        omblepy_mod = sys.modules.get("omblepy") or sys.modules["__main__"]
        ble = omblepy_mod.bleClient
        
        q = asyncio.Queue()
        def _cb(_, data): q.put_nowait(bytes(data))
        
        try:
            await ble.start_notify(btobj.deviceUnlock_UUID, _cb)
            
            # Passo 1: Enviar desafio (0x02 + 16 bytes nulos)
            await ble.write_gatt_char(btobj.deviceUnlock_UUID, bytes([0x02] + [0]*16), response=True)
            await asyncio.wait_for(q.get(), timeout=3.0)
            
            # Passo 2: Enviar chave mestre (0x01 + key)
            await ble.write_gatt_char(btobj.deviceUnlock_UUID, bytes([0x01]) + self.UNLOCK_KEY, response=True)
            await asyncio.wait_for(q.get(), timeout=3.0)
            
            logger.debug("HBF-222T: Desbloqueio concluído com sucesso.")
        except Exception as e:
            logger.error(f"HBF-222T: Falha no desbloqueio: {e}")
            raise
        finally:
            try: await ble.stop_notify(btobj.deviceUnlock_UUID)
            except: pass

    @staticmethod
    def _extract_bits(raw, offset, bit_size, start_bit):
        """Helper para extração de bit-packing em Big Endian."""
        val = struct.unpack_from(">H", raw, offset)[0]
        mask = (1 << bit_size) - 1
        return (val >> start_bit) & mask

    def _decode_record(self, raw):
        """
        Decodifica o payload vital de 32 bytes da EEPROM.
        Layout de bits extraído do VitalDataIndexes.json oficial.
        """
        if len(raw) < 32 or raw[0:2] == b'\xff\xff':
            return None
            
        try:
            # --- Métricas Físicas ---
            # Peso: Byte 26, 12 bits, start 4 -> Res: 0.05kg
            weight   = self._extract_bits(raw, 26, 12, 4) * 0.05
            # Gordura: Byte 2, 10 bits, start 6 -> Res: 0.1%
            fat_pct  = self._extract_bits(raw, 2,  10, 6) * 0.1
            # Musculo: Byte 6, 10 bits, start 6 -> Res: 0.1%
            muscle   = self._extract_bits(raw, 6,  10, 6) * 0.1
            # IMC/BMI: Byte 8, 10 bits, start 6 -> Res: 0.1
            bmi      = self._extract_bits(raw, 8,  10, 6) * 0.1
            # Visceral: Byte 10, 7 bits, start 0 -> Res: 1 nível
            visceral = raw[10] & 0x7F
            # BMR: Byte 4, 12 bits, start 4 -> Res: 1 kcal
            bmr      = self._extract_bits(raw, 4,  12, 4)
            # Idade Corporal: Byte 11, 4 bits, start 4
            body_age = (raw[11] >> 4) & 0x0F

            # --- Timestamp (YY MM DD HH MI SS) ---
            year   = (raw[7]  & 0x3F) + 2000
            month  = raw[11]  & 0x0F
            day    = (raw[12] >> 3) & 0x1F
            hour   = self._extract_bits(raw, 12, 5, 6)
            minute = raw[9]   & 0x3F
            second = raw[13]  & 0x3F # Bits 0-5 do byte 13

            # Validação básica de sanidade
            if not (30.0 < weight < 200.0) or not (1 <= month <= 12):
                return None

            return {
                "datetime":     datetime.datetime(year, month, day, hour, minute, min(second, 59)),
                "weight":       round(weight, 2),
                "fat_pct":      round(fat_pct, 1),
                "fat_mass":     round(weight * fat_pct / 100.0, 2),
                "muscle":       round(muscle, 1),
                "bmi":          round(bmi, 1),
                "visceral_fat": visceral,
                "bmr":          bmr,
                "body_age":     body_age
            }
        except Exception as e:
            logger.debug(f"HBF-222T: Falha ao decodificar bloco: {e}")
            return None

    async def getRecords(self, btobj, useUnreadCounter, syncTime):
        """Lê o histórico de todos os perfis ativos na balança."""
        try:
            await self._custom_unlock(btobj)
            await btobj.startTransmission()

            user_records = {uid: [] for uid in self.PROFILE_ADDRESSES}

            for uid, base_addr in self.PROFILE_ADDRESSES.items():
                logger.info(f"HBF-222T: Coletando dados do Perfil {uid}...")
                
                # Varre os últimos 30 slots de cada perfil
                for slot in range(30):
                    addr = base_addr + (slot * 32)
                    raw_data = await btobj.readContinuousEepromData(addr, 32, 32)
                    
                    # Interrompe se o slot estiver vazio (0xFF) ou falhar
                    if not raw_data or all(b == 0xFF for b in raw_data):
                        break
                    
                    res = self._decode_record(raw_data)
                    if res:
                        user_records[uid].append(res)
                        logger.info(f"HBF-222T [P{uid}]: {res['datetime']} -> {res['weight']}kg")
                    
                    await asyncio.sleep(0.01) # Breve pausa para estabilidade do barramento

            await btobj.endTransmission()
            
            # Retorna lista de listas (compatível com sharedDriver)
            return [user_records[i] for i in range(1, 5)]
            
        except Exception as e:
            logger.error(f"HBF-222T: Falha na extração de registros: {e}")
            raise
