"""
gpu_detection.py

Detecta as GPUs físicas instaladas via WMI, apenas para exibir os
nomes reais na interface (ex.: "AMD Radeon Graphics" e "NVIDIA GeForce
GTX 1650"). É puramente informativo — o Windows decide sozinho qual
GPU física corresponde a "economia de energia" e qual corresponde a
"alto desempenho", com base no driver instalado.
"""

from __future__ import annotations


def get_installed_gpu_names() -> list[str]:
    try:
        import wmi  # import tardio: só necessário aqui
    except ImportError:
        return []

    try:
        connection = wmi.WMI()
        controllers = connection.Win32_VideoController()
        return [c.Name for c in controllers if getattr(c, "Name", None)]
    except Exception:
        return []
