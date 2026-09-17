"""
registry_backend.py

Toda a leitura/escrita na chave do Registro do Windows responsável
pelas preferências de GPU por aplicativo:

    HKEY_CURRENT_USER\\Software\\Microsoft\\DirectX\\UserGpuPreferences

Essa é a mesma chave usada pela tela nativa do Windows em
Configurações > Sistema > Vídeo > Configurações gráficas.
"""

from __future__ import annotations

import os
import time
from enum import IntEnum
from typing import Optional

try:
    import winreg
except ImportError:  # ambiente não-Windows (ex.: só para leitura de código)
    winreg = None  # type: ignore

REG_PATH = r"Software\Microsoft\DirectX\UserGpuPreferences"


class GpuPreference(IntEnum):
    SYSTEM_DEFAULT = 0
    POWER_SAVING = 1       # geralmente a GPU integrada
    HIGH_PERFORMANCE = 2   # geralmente a GPU dedicada

    @property
    def label_pt(self) -> str:
        return {
            GpuPreference.SYSTEM_DEFAULT: "Não definida",
            GpuPreference.POWER_SAVING: "Economia de energia (integrada)",
            GpuPreference.HIGH_PERFORMANCE: "Alto desempenho (dedicada)",
        }[self]


def _require_winreg() -> None:
    if winreg is None:
        raise RuntimeError("winreg indisponível: este recurso só funciona no Windows.")


def set_gpu_preference(exe_path: str, preference: GpuPreference) -> None:
    """Grava a preferência de GPU para um executável. SYSTEM_DEFAULT remove a entrada."""
    _require_winreg()
    exe_path = os.path.abspath(exe_path)

    if preference == GpuPreference.SYSTEM_DEFAULT:
        remove_gpu_preference(exe_path)
        return

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE) as key:
        value = f"GpuPreference={int(preference)};"
        winreg.SetValueEx(key, exe_path, 0, winreg.REG_SZ, value)


def get_gpu_preference(exe_path: str) -> GpuPreference:
    """Lê a preferência atual de um executável. Retorna SYSTEM_DEFAULT se não configurado."""
    _require_winreg()
    exe_path = os.path.abspath(exe_path)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as key:
            raw_value, _ = winreg.QueryValueEx(key, exe_path)
            return _parse_preference_string(raw_value)
    except (FileNotFoundError, OSError):
        return GpuPreference.SYSTEM_DEFAULT


def remove_gpu_preference(exe_path: str) -> None:
    """Remove a entrada de um executável, voltando ao padrão do sistema."""
    _require_winreg()
    exe_path = os.path.abspath(exe_path)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE) as key:
            try:
                winreg.DeleteValue(key, exe_path)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def read_all_registry_entries() -> dict[str, GpuPreference]:
    """Lê todas as entradas já configuradas no registro de uma vez (mais eficiente
    do que chamar get_gpu_preference() executável por executável)."""
    _require_winreg()
    result: dict[str, GpuPreference] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, raw_value, _type = winreg.EnumValue(key, index)
                except OSError:
                    break
                if name and isinstance(raw_value, str):
                    result[os.path.abspath(name).lower()] = _parse_preference_string(raw_value)
                index += 1
    except FileNotFoundError:
        pass
    return result


def backup_preferences(destination_folder: str) -> str:
    """Salva um backup em texto de todas as entradas atuais do registro."""
    _require_winreg()
    path = os.path.join(destination_folder, "gpu_preferences_backup.txt")
    lines = ["GPU Manager - backup de preferências", time.strftime("%Y-%m-%d %H:%M:%S"), ""]

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, raw_value, _type = winreg.EnumValue(key, index)
                    lines.append(f"{name}\t{raw_value}")
                    index += 1
                except OSError:
                    break
    except FileNotFoundError:
        pass

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def restore_preferences(backup_file: str) -> int:
    """Restaura entradas a partir de um arquivo de backup gerado por backup_preferences().
    Retorna o número de entradas restauradas."""
    _require_winreg()
    restored = 0
    with open(backup_file, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE) as key:
        for line in lines:
            if "\t" not in line:
                continue
            name, _, value = line.strip().partition("\t")
            if not name or not value:
                continue
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            restored += 1

    return restored


def _parse_preference_string(raw_value: str) -> GpuPreference:
    prefix = "GpuPreference="
    start = raw_value.lower().find(prefix.lower())
    if start < 0:
        return GpuPreference.SYSTEM_DEFAULT
    start += len(prefix)
    end = raw_value.find(";", start)
    number_part = raw_value[start:end] if end > start else raw_value[start:]
    try:
        return GpuPreference(int(number_part.strip()))
    except (ValueError, KeyError):
        return GpuPreference.SYSTEM_DEFAULT
