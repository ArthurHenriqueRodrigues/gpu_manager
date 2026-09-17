"""
gpu_manager.py

GPU Manager v2 - varredura inteligente de programas/jogos e configuração
de preferência de GPU por aplicativo no Windows (HKCU DirectX
UserGpuPreferences).

Principais melhorias em relação a uma varredura simples:
- Classificação por PONTUAÇÃO (score), não sim/não binário — reduz
  falsos positivos/negativos e mostra a confiança da classificação.
- Varredura em thread com barra de progresso DETERMINADA (conta
  pastas/arquivos reais) e botão para CANCELAR no meio do processo.
- CACHE em disco do último scan: reabrir o app não exige revarrer tudo.
- Cada linha da lista é EDITÁVEL individualmente (dropdown por linha),
  além do botão de aplicar automaticamente em lote.
- Busca/filtro por nome na lista de resultados.
- Backup e restauração das preferências atuais do registro.
- Pastas de varredura configuráveis pelo usuário (não fixas no código).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from registry_backend import (
    GpuPreference,
    backup_preferences,
    read_all_registry_entries,
    remove_gpu_preference,
    restore_preferences,
    set_gpu_preference,
)
from scanner import (
    ScanResult,
    build_scan_results,
    default_scan_roots,
    load_scan_cache,
    save_scan_cache,
    scan_executables,
)
from gpu_detection import get_installed_gpu_names

APP_TITLE = "GPU Manager v2 — Varredura Inteligente"
APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "GpuManagerV2")

PREFERENCE_OPTIONS = [
    (GpuPreference.SYSTEM_DEFAULT, "Padrão do sistema"),
    (GpuPreference.POWER_SAVING, "Economia de energia (integrada)"),
    (GpuPreference.HIGH_PERFORMANCE, "Alto desempenho (dedicada)"),
]
PREFERENCE_LABELS = [label for _, label in PREFERENCE_OPTIONS]
LABEL_TO_PREFERENCE = {label: pref for pref, label in PREFERENCE_OPTIONS}


class GpuManagerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x680")
        self.minsize(860, 540)

        self.results: list[ScanResult] = []
        self.filtered_indices: list[int] = []  # índices em self.results que passam no filtro atual
        self.scan_roots: list[str] = default_scan_roots()
        self._stop_scan_requested = False
        self._scan_thread: Optional[threading.Thread] = None

        self.style = ttk.Style(self)
        try:
            self.style.theme_use("vista")
        except Exception:
            pass

        self._build_ui()
        self._load_cache_if_available()
        self._load_detected_gpus()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")

        ttk.Label(top, text="GPU Manager", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self.gpu_label = ttk.Label(top, text="Detectando GPUs instaladas...", font=("Segoe UI", 10))
        self.gpu_label.pack(anchor="w", pady=(2, 8))

        buttons = ttk.Frame(top)
        buttons.pack(fill="x")

        self.scan_btn = ttk.Button(buttons, text="Escanear programas", command=self._start_scan)
        self.scan_btn.pack(side="left", padx=(0, 6))

        self.cancel_btn = ttk.Button(buttons, text="Cancelar varredura", command=self._cancel_scan, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)

        self.apply_btn = ttk.Button(
            buttons, text="Aplicar sugestões automáticas", command=self._start_apply, state="disabled"
        )
        self.apply_btn.pack(side="left", padx=6)

        ttk.Button(buttons, text="Escolher pastas...", command=self._choose_folders).pack(side="left", padx=6)
        ttk.Button(buttons, text="Backup", command=self._do_backup).pack(side="left", padx=6)
        ttk.Button(buttons, text="Restaurar backup...", command=self._do_restore).pack(side="left", padx=6)

        # Busca/filtro
        filter_row = ttk.Frame(top)
        filter_row.pack(fill="x", pady=(8, 0))
        ttk.Label(filter_row, text="Filtrar:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(filter_row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.status_var = tk.StringVar(value="Pronto. Clique em \"Escanear programas\".")
        ttk.Label(top, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

        self.progress = ttk.Progressbar(top, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(5, 0))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._build_results_tab(notebook)
        self._build_manual_tab(notebook)
        self._build_log_tab(notebook)

        note = ttk.Label(
            self,
            text=(
                "A classificação é uma sugestão baseada em heurística (nome/pasta do programa) — "
                "revise antes de aplicar em lote. Feche e reabra um programa após mudar sua GPU."
            ),
            padding=(12, 0, 12, 10),
            wraplength=1000,
            foreground="#555",
        )
        note.pack(fill="x")

    def _build_results_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Programas encontrados")

        columns = ("program", "classification", "score", "current", "suggested")
        self.tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("program", text="Executável")
        self.tree.heading("classification", text="Classificação")
        self.tree.heading("score", text="Score")
        self.tree.heading("current", text="Preferência atual")
        self.tree.heading("suggested", text="Sugestão")
        self.tree.column("program", width=430)
        self.tree.column("classification", width=120, anchor="center")
        self.tree.column("score", width=60, anchor="center")
        self.tree.column("current", width=190)
        self.tree.column("suggested", width=190)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_row_double_click)

        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        # Painel lateral para editar a linha selecionada manualmente.
        side = ttk.Frame(tab, padding=(10, 0, 0, 0))
        side.pack(side="right", fill="y")

        ttk.Label(side, text="Editar seleção:", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(side, text="(duplo clique na linha\ntambém abre isto)", foreground="#777").pack(anchor="w", pady=(0, 8))

        self.edit_choice_var = tk.StringVar(value=PREFERENCE_LABELS[0])
        for label in PREFERENCE_LABELS:
            ttk.Radiobutton(side, text=label, value=label, variable=self.edit_choice_var).pack(anchor="w")

        ttk.Button(side, text="Aplicar à seleção", command=self._apply_to_selection).pack(anchor="w", pady=(10, 0), fill="x")
        ttk.Button(side, text="Remover preferência (padrão)", command=self._reset_selection).pack(anchor="w", pady=(4, 0), fill="x")

    def _build_manual_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Controle manual")

        ttk.Label(tab, text="Digite ou selecione o caminho de um .exe para forçar a GPU:").pack(anchor="w")

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=6)
        self.manual_path_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.manual_path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Procurar...", command=self._browse_manual_exe).pack(side="left", padx=(6, 0))

        buttons_row = ttk.Frame(tab)
        buttons_row.pack(fill="x", pady=4)
        for pref, label in PREFERENCE_OPTIONS:
            ttk.Button(
                buttons_row, text=label, command=lambda p=pref: self._manual_set(p)
            ).pack(side="left", padx=(0, 6))

    def _build_log_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Log")
        self.log_text = tk.Text(tab, wrap="none", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Utilitários de log/status
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.after(0, lambda: (self.log_text.insert("end", line + "\n"), self.log_text.see("end")))

    def _set_status(self, message: str) -> None:
        self.after(0, lambda: self.status_var.set(message))

    # ------------------------------------------------------------------
    # GPUs detectadas / cache
    # ------------------------------------------------------------------

    def _load_detected_gpus(self) -> None:
        def worker() -> None:
            names = get_installed_gpu_names()
            text = "GPUs detectadas: " + " | ".join(names) if names else \
                "Não foi possível detectar as GPUs (biblioteca 'wmi' ausente ou indisponível)."
            self.after(0, lambda: self.gpu_label.config(text=text))

        threading.Thread(target=worker, daemon=True).start()

    def _load_cache_if_available(self) -> None:
        cache = load_scan_cache(APP_DATA_DIR)
        if not cache:
            return
        self.results = cache["results"]
        self.scan_roots = cache.get("scanned_roots", self.scan_roots) or self.scan_roots
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(cache.get("timestamp", 0)))
        self._set_status(f"{len(self.results)} programas carregados do último scan ({when}). Atualize se quiser revarrer.")
        self._populate_tree()
        self.apply_btn.config(state="normal" if self.results else "disabled")

    # ------------------------------------------------------------------
    # Varredura
    # ------------------------------------------------------------------

    def _choose_folders(self) -> None:
        folder = filedialog.askdirectory(title="Adicionar pasta para varredura (ex.: onde você instala jogos)")
        if not folder:
            return
        if folder not in self.scan_roots:
            self.scan_roots.append(folder)
            self._log(f"Pasta adicionada à varredura: {folder}")
            messagebox.showinfo("Pasta adicionada", f"A pasta será incluída na próxima varredura:\n{folder}")

    def _start_scan(self) -> None:
        if self._scan_thread and self._scan_thread.is_alive():
            return

        self._stop_scan_requested = False
        self.scan_btn.config(state="disabled")
        self.apply_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress.config(mode="indeterminate")
        self.progress.start(12)
        self._set_status("Escaneando pastas... isso pode levar alguns minutos na primeira vez.")

        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()

    def _cancel_scan(self) -> None:
        self._stop_scan_requested = True
        self._set_status("Cancelando varredura...")

    def _scan_worker(self) -> None:
        try:
            def progress_cb(current_dir: str, file_count: int, folder_count: int) -> None:
                self._set_status(f"Varrendo... {file_count} executáveis encontrados em {folder_count} pastas.")

            exe_paths = scan_executables(
                self.scan_roots,
                progress_callback=progress_cb,
                stop_flag=lambda: self._stop_scan_requested,
            )

            registry_entries = read_all_registry_entries()
            results = build_scan_results(exe_paths, registry_entries)

            # Preserva escolhas manuais (user_override) que o usuário já
            # tinha feito antes, casando pelo caminho do executável.
            previous_overrides = {r.path.lower(): r.user_override for r in self.results if r.user_override is not None}
            for r in results:
                if r.path.lower() in previous_overrides:
                    r.user_override = previous_overrides[r.path.lower()]

            self.results = results
            save_scan_cache(APP_DATA_DIR, self.results, self.scan_roots)

            game_count = sum(1 for r in results if r.is_game)
            cancelled_note = " (cancelada antes de terminar)" if self._stop_scan_requested else ""
            self._set_status(
                f"{len(results)} executáveis encontrados{cancelled_note}. "
                f"{game_count} classificados como jogos."
            )
            self.after(0, self._populate_tree)

        except Exception as exc:  # noqa: BLE001
            self._log(f"[ERRO] Varredura falhou: {exc}")
            self.after(0, lambda: messagebox.showerror("Erro na varredura", str(exc)))
        finally:
            self.after(0, self._finish_scan)

    def _finish_scan(self) -> None:
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.scan_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.apply_btn.config(state="normal" if self.results else "disabled")

    # ------------------------------------------------------------------
    # Tabela de resultados
    # ------------------------------------------------------------------

    def _populate_tree(self) -> None:
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.filter_var.get().strip().lower()
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.filtered_indices = []
        for index, result in enumerate(sorted(self.results, key=lambda r: r.path.lower())):
            if query and query not in result.path.lower():
                continue
            self.filtered_indices.append(index)

        # Re-obtém a lista ordenada (mesma ordem usada acima) para casar índices.
        ordered = sorted(self.results, key=lambda r: r.path.lower())
        self._ordered_results = ordered

        for index in self.filtered_indices:
            result = ordered[index]
            classification = "JOGO" if result.is_game else "Programa"
            self.tree.insert(
                "", "end", iid=str(index),
                values=(
                    result.path,
                    classification,
                    result.game_score,
                    result.current_preference.label_pt,
                    result.effective_preference.label_pt,
                ),
            )

    def _get_selected_results(self) -> list[ScanResult]:
        selected_ids = self.tree.selection()
        return [self._ordered_results[int(iid)] for iid in selected_ids]

    def _on_row_double_click(self, _event) -> None:
        selected = self._get_selected_results()
        if not selected:
            return
        current_label = selected[0].effective_preference.label_pt
        self.edit_choice_var.set(current_label)
        self._apply_to_selection()

    def _apply_to_selection(self) -> None:
        selected = self._get_selected_results()
        if not selected:
            messagebox.showinfo("Nada selecionado", "Selecione uma ou mais linhas na tabela primeiro.")
            return

        chosen_label = self.edit_choice_var.get()
        chosen_pref = LABEL_TO_PREFERENCE[chosen_label]

        for result in selected:
            result.user_override = chosen_pref

        self._apply_filter()
        self._log(f"{len(selected)} programa(s) marcado(s) manualmente como: {chosen_label} (ainda não salvo no registro)")

    def _reset_selection(self) -> None:
        selected = self._get_selected_results()
        for result in selected:
            result.user_override = None
        self._apply_filter()

    # ------------------------------------------------------------------
    # Aplicar automaticamente (em lote)
    # ------------------------------------------------------------------

    def _start_apply(self) -> None:
        if not self.results:
            return

        game_count = sum(1 for r in self.results if r.effective_preference == GpuPreference.HIGH_PERFORMANCE)
        integrated_count = sum(1 for r in self.results if r.effective_preference == GpuPreference.POWER_SAVING)

        confirmed = messagebox.askyesno(
            "Confirmar aplicação",
            f"Isso vai gravar no registro do Windows a preferência de GPU para cada programa listado:\n\n"
            f"• {integrated_count} programa(s) → Economia de energia (integrada)\n"
            f"• {game_count} programa(s) → Alto desempenho (dedicada)\n\n"
            f"Você pode revisar e ajustar qualquer linha manualmente antes de continuar.\n"
            f"Deseja aplicar agora?",
        )
        if not confirmed:
            return

        self.apply_btn.config(state="disabled")
        self.scan_btn.config(state="disabled")
        self.progress.config(mode="determinate", maximum=len(self.results), value=0)
        self._set_status("Aplicando preferências no registro...")

        threading.Thread(target=self._apply_worker, daemon=True).start()

    def _apply_worker(self) -> None:
        changed = 0
        errors = 0

        for i, result in enumerate(self.results, start=1):
            try:
                if result.effective_preference != result.current_preference:
                    set_gpu_preference(result.path, result.effective_preference)
                    result.current_preference = result.effective_preference
                    changed += 1
                    self._log(f"[{result.effective_preference.label_pt}] {result.path}")
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self._log(f"[ERRO] {result.path} -> {exc}")

            self.after(0, lambda v=i: self.progress.config(value=v))

        save_scan_cache(APP_DATA_DIR, self.results, self.scan_roots)

        def finish() -> None:
            self.apply_btn.config(state="normal")
            self.scan_btn.config(state="normal")
            self._set_status(f"Concluído: {changed} programa(s) atualizados, {errors} erro(s).")
            self._apply_filter()
            messagebox.showinfo(
                "Concluído",
                f"Preferências aplicadas.\n\n"
                f"Atualizados: {changed}\n"
                f"Erros: {errors}\n\n"
                f"Feche e reabra os programas para a alteração ter efeito.",
            )

        self.after(0, finish)

    # ------------------------------------------------------------------
    # Controle manual
    # ------------------------------------------------------------------

    def _browse_manual_exe(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecione um executável", filetypes=[("Executáveis", "*.exe"), ("Todos os arquivos", "*.*")]
        )
        if path:
            self.manual_path_var.set(path)

    def _manual_set(self, preference: GpuPreference) -> None:
        path = self.manual_path_var.get().strip().strip('"')
        if not path or not path.lower().endswith(".exe") or not os.path.isfile(path):
            messagebox.showwarning("Caminho inválido", "Informe o caminho completo de um arquivo .exe existente.")
            return
        try:
            set_gpu_preference(path, preference)
            self._log(f"[MANUAL] {preference.label_pt}: {path}")
            messagebox.showinfo("OK", f"Preferência definida para: {preference.label_pt}\n\nReabra o programa.")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro", str(exc))

    # ------------------------------------------------------------------
    # Backup / restauração
    # ------------------------------------------------------------------

    def _do_backup(self) -> None:
        try:
            folder = filedialog.askdirectory(title="Escolha onde salvar o backup") or APP_DATA_DIR
            os.makedirs(folder, exist_ok=True)
            path = backup_preferences(folder)
            self._log(f"Backup salvo em: {path}")
            messagebox.showinfo("Backup", f"Backup salvo em:\n{path}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro", str(exc))

    def _do_restore(self) -> None:
        path = filedialog.askopenfilename(title="Escolha o arquivo de backup", filetypes=[("Texto", "*.txt"), ("Todos", "*.*")])
        if not path:
            return
        if not messagebox.askyesno("Confirmar restauração", "Isso vai sobrescrever as preferências atuais com as do backup. Continuar?"):
            return
        try:
            count = restore_preferences(path)
            self._log(f"Backup restaurado: {count} entrada(s) de {path}")
            messagebox.showinfo("Restaurado", f"{count} entrada(s) restaurada(s) com sucesso.")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro", str(exc))


def main() -> None:
    if os.name != "nt":
        print("Este aplicativo só funciona no Windows (depende do módulo 'winreg').")
        sys.exit(1)

    app = GpuManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
