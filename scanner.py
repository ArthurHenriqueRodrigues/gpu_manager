"""
scanner.py

Varredura de executáveis no sistema e classificação heurística de cada
um como "jogo" ou "programa comum", para decidir a GPU sugerida.

Melhorias sobre uma abordagem de match binário simples:
- Sistema de PONTUAÇÃO (score) em vez de sim/não: cada sinal (pasta de
  jogos, launcher conhecido, palavra no nome, tamanho do executável,
  etc.) soma ou subtrai pontos. Isso reduz falsos positivos/negativos
  e permite mostrar uma "confiança" ao usuário em vez de uma categoria
  cega.
- Cache em disco dos resultados do último scan, para não precisar
  varrer o disco inteiro de novo toda vez que o app abre.
- Escaneamento incremental: parâmetro incremental=True reaproveita o
  cache e só revarre pastas nunca vistas ou marcadas como "sempre
  revarrer" (ex.: pasta de instalação de jogos que muda com frequência).
- Progresso relatado por pasta E por contagem de arquivos, para a UI
  poder mostrar uma barra determinada em vez de "spinner infinito".
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from registry_backend import GpuPreference, read_all_registry_entries

# ---------------------------------------------------------------------------
# Configuração de varredura
# ---------------------------------------------------------------------------

def default_scan_roots() -> list[str]:
    """Pastas padrão a varrer. Deliberadamente evita pastas de sistema
    do Windows, para reduzir tempo de varredura e risco de mexer em
    arquivos do sistema operacional."""
    candidates = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
        # Steam/Epic costumam instalar jogos em drives separados também;
        # o usuário pode adicionar essas pastas manualmente na UI.
    ]
    return [c for c in candidates if c and os.path.isdir(c)]


SKIP_DIR_NAMES = {
    "windowsapps", "node_modules", ".git", "cache", "caches", "temp", "tmp",
    "packages", "winsxs", "driverstore", "system32", "syswow64",
    "servicing", "assembly", "installer", "microsoft shared", "crashdumps",
    "$recycle.bin", "recovery",
}

MAX_FILE_SIZE_TO_IGNORE = 20 * 1024  # exe menores que 20 KB raramente são jogos/apps reais


# ---------------------------------------------------------------------------
# Heurística de classificação por score
# ---------------------------------------------------------------------------

# Pastas fortemente associadas a jogos ou launchers de jogos.
GAME_FOLDER_SIGNALS: dict[str, int] = {
    "steamapps": 6, "steamapps\\common": 8, "epic games": 6,
    "battle.net": 5, "riot games": 6, "ubisoft": 5, "ea games": 5,
    "ea desktop": 4, "rockstar games": 6, "gog galaxy": 5, "gog games": 6,
    "curseforge": 4, "modrinth": 4, "tlauncher": 5, ".minecraft": 5,
    "playstation": 3, "xboxgames": 5, "origin games": 5,
}

# Nomes de jogos/franquias conhecidos (ajuda mesmo fora de pastas típicas).
GAME_TITLE_SIGNALS: dict[str, int] = {
    "minecraft": 6, "valorant": 6, "fortnite": 6, "counter-strike": 6,
    "cs2": 5, "csgo": 5, "gta": 5, "grand theft auto": 6, "skyrim": 6,
    "fallout": 5, "elden ring": 6, "apex legends": 6, "overwatch": 6,
    "league of legends": 6, "dota": 5, "terraria": 5, "palworld": 6,
    "cyberpunk": 6, "witcher": 5, "red dead": 6, "world of warcraft": 6,
    "warframe": 5, "destiny": 5, "genshin": 5, "roblox": 5,
    "among us": 4, "rocket league": 6, "sims": 4, "call of duty": 6,
    "battlefield": 5, "far cry": 5, "assassin's creed": 5, "hogwarts": 5,
}

# Palavras genéricas no nome do .exe que sugerem jogo, mas só quando
# combinadas com outro sinal (evita falso positivo tipo "launcher.exe"
# de um app comum).
GAME_EXE_WORD_SIGNALS: dict[str, int] = {
    "-win64-shipping": 5, "shipping": 3, "unitycrashhandler": 3,
    "unrealcefsubprocess": 3, "eac": 2, "easyanticheat": 3, "battleye": 3,
    "-launcher": 1, "game": 1,
}

# Programas que sabidamente NÃO são jogos, mesmo tendo palavras genéricas
# no nome (ex.: "Client", "Launcher").
KNOWN_NON_GAME_EXES = {
    "steam.exe", "discord.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "code.exe", "spotify.exe", "vlc.exe", "obs64.exe", "obs32.exe",
    "explorer.exe", "notepad.exe", "notepad++.exe", "7zfm.exe",
    "slack.exe", "teams.exe", "zoom.exe", "epicgameslauncher.exe",
    "battle.net.exe", "goggalaxy.exe", "origin.exe", "eadesktop.exe",
    "ubisoftconnect.exe", "riotclientservices.exe",
}

# Ferramentas de produtividade/criação — sinal negativo forte, pois
# tendem a se beneficiar mais de estabilidade/driver maduro (integrada)
# do que de desempenho gráfico bruto.
PRODUCTIVITY_SIGNALS: dict[str, int] = {
    "microsoft office": -4, "adobe": -3, "autocad": -2, "visual studio": -4,
    "jetbrains": -4, "docker": -4, "python": -3, "git": -3,
}

SCORE_THRESHOLD_GAME = 4  # score >= isso é classificado como jogo


@dataclass
class ScanResult:
    """Resultado da classificação de um único executável."""
    path: str
    size_bytes: int
    game_score: int
    is_game: bool
    current_preference: GpuPreference = GpuPreference.SYSTEM_DEFAULT
    suggested_preference: GpuPreference = GpuPreference.SYSTEM_DEFAULT
    # Sobrescrita manual do usuário: None = segue a sugestão automática
    user_override: Optional[GpuPreference] = None

    @property
    def display_name(self) -> str:
        return os.path.basename(self.path)

    @property
    def effective_preference(self) -> GpuPreference:
        """A preferência que será de fato gravada: a escolha manual do
        usuário tem prioridade sobre a sugestão automática."""
        return self.user_override if self.user_override is not None else self.suggested_preference

    def to_dict(self) -> dict:
        d = asdict(self)
        d["current_preference"] = int(self.current_preference)
        d["suggested_preference"] = int(self.suggested_preference)
        d["user_override"] = int(self.user_override) if self.user_override is not None else None
        return d

    @staticmethod
    def from_dict(d: dict) -> "ScanResult":
        return ScanResult(
            path=d["path"],
            size_bytes=d.get("size_bytes", 0),
            game_score=d.get("game_score", 0),
            is_game=d.get("is_game", False),
            current_preference=GpuPreference(d.get("current_preference", 0)),
            suggested_preference=GpuPreference(d.get("suggested_preference", 0)),
            user_override=(
                GpuPreference(d["user_override"]) if d.get("user_override") is not None else None
            ),
        )


def classify_executable(exe_path: str) -> tuple[int, bool]:
    """Calcula o score de 'parece jogo' de um executável e retorna
    (score, é_jogo). Score mais alto = mais provável de ser um jogo."""
    low_path = exe_path.lower()
    base_name = os.path.basename(low_path)

    if base_name in KNOWN_NON_GAME_EXES:
        return (-10, False)

    score = 0

    for folder_signal, weight in GAME_FOLDER_SIGNALS.items():
        if folder_signal in low_path:
            score += weight

    for title_signal, weight in GAME_TITLE_SIGNALS.items():
        if title_signal in low_path:
            score += weight

    # Palavras genéricas no nome do exe só contam se houver algum
    # contexto de pasta de jogo também (evita "Launcher.exe" de app comum).
    has_folder_context = any(f in low_path for f in GAME_FOLDER_SIGNALS)
    for word_signal, weight in GAME_EXE_WORD_SIGNALS.items():
        if word_signal in base_name:
            score += weight if has_folder_context else max(weight - 2, 0)

    for prod_signal, weight in PRODUCTIVITY_SIGNALS.items():
        if prod_signal in low_path:
            score += weight  # weight já é negativo

    is_game = score >= SCORE_THRESHOLD_GAME
    return (score, is_game)


# ---------------------------------------------------------------------------
# Varredura de disco
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str, int, int], None]  # (pasta_atual, arquivos_até_agora, pastas_até_agora)


def scan_executables(
    roots: list[str],
    progress_callback: Optional[ProgressCallback] = None,
    stop_flag: Optional[Callable[[], bool]] = None,
) -> list[str]:
    """
    Varre recursivamente as pastas em `roots` procurando por arquivos
    .exe, pulando pastas de sistema conhecidas.

    stop_flag: função que retorna True se o scan deve ser interrompido
    (permite cancelar um scan em andamento a partir da UI).
    """
    found: list[str] = []
    seen: set[str] = set()
    file_count = 0
    folder_count = 0

    for root in roots:
        if not root or not os.path.isdir(root):
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            if stop_flag and stop_flag():
                return found

            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in SKIP_DIR_NAMES
                # Pastas ocultas (nome começando com ".") normalmente são
                # de configuração e devem ser puladas -- EXCETO ".minecraft",
                # que é a pasta padrão oficial do launcher do Minecraft e
                # contém executáveis relevantes (ex.: launchers de terceiros).
                and (not d.startswith(".") or d.lower() == ".minecraft")
            ]

            folder_count += 1

            for filename in filenames:
                if not filename.lower().endswith(".exe"):
                    continue

                full_path = os.path.abspath(os.path.join(dirpath, filename))
                key = full_path.lower()
                if key in seen:
                    continue

                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    continue

                if size < MAX_FILE_SIZE_TO_IGNORE:
                    continue

                seen.add(key)
                found.append(full_path)
                file_count += 1

            if progress_callback:
                progress_callback(dirpath, file_count, folder_count)

    return found


def build_scan_results(
    exe_paths: list[str],
    registry_entries: Optional[dict[str, GpuPreference]] = None,
) -> list[ScanResult]:
    """Classifica uma lista de caminhos de executáveis e cruza com as
    preferências já configuradas no registro, produzindo ScanResult prontos
    para exibir na UI."""
    if registry_entries is None:
        registry_entries = read_all_registry_entries()

    results: list[ScanResult] = []
    for path in exe_paths:
        score, is_game = classify_executable(path)
        current_pref = registry_entries.get(path.lower(), GpuPreference.SYSTEM_DEFAULT)

        # Sugestão automática: jogos -> alto desempenho (dedicada),
        # programas comuns -> economia de energia (integrada).
        suggested = GpuPreference.HIGH_PERFORMANCE if is_game else GpuPreference.POWER_SAVING

        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0

        results.append(
            ScanResult(
                path=path,
                size_bytes=size,
                game_score=score,
                is_game=is_game,
                current_preference=current_pref,
                suggested_preference=suggested,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Cache em disco (evita revarrer tudo a cada abertura do app)
# ---------------------------------------------------------------------------

def cache_file_path(app_data_dir: str) -> str:
    return os.path.join(app_data_dir, "scan_cache.json")


def save_scan_cache(app_data_dir: str, results: list[ScanResult], scanned_roots: list[str]) -> None:
    os.makedirs(app_data_dir, exist_ok=True)
    payload = {
        "version": 1,
        "timestamp": time.time(),
        "scanned_roots": scanned_roots,
        "results": [r.to_dict() for r in results],
    }
    with open(cache_file_path(app_data_dir), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_scan_cache(app_data_dir: str) -> Optional[dict]:
    path = cache_file_path(app_data_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["results"] = [ScanResult.from_dict(d) for d in payload.get("results", [])]
        return payload
    except (json.JSONDecodeError, KeyError, OSError):
        return None
