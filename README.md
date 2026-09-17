# GPU Manager

Ferramenta para Windows que resolve um problema chato de notebooks e PCs com placa de vídeo dupla (integrada + dedicada): decidir, programa por programa, qual GPU deve ser usada.

Em vez de abrir Configurações do Windows toda vez que instala algo novo, o GPU Manager varre o seu computador, sugere automaticamente qual GPU cada programa deveria usar (integrada para tarefas comuns, dedicada para jogos) e deixa você revisar e ajustar antes de aplicar qualquer coisa.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Por que isso existe

O Windows já tem essa configuração nativa (Configurações → Sistema → Vídeo → Configurações gráficas), mas ela é manual: você precisa abrir a tela, clicar em "Procurar", achar o `.exe` e configurar um por um. Em um PC com dezenas de jogos e programas instalados, isso vira trabalho.

O GPU Manager varre as pastas onde programas costumam ser instalados, classifica cada executável encontrado (jogo ou programa comum) usando um sistema de pontuação baseado em nome de pasta, franquias conhecidas e padrões de nomenclatura, e te entrega tudo pronto para revisar em uma lista — com a opção de aplicar em lote ou ajustar manualmente qualquer item.

## Funcionalidades

- **Varredura automática** de `Program Files`, `Program Files (x86)`, `AppData\Local` e `AppData\Roaming`, com opção de adicionar outras pastas (útil se você instala jogos em outro drive).
- **Classificação por pontuação**, não um simples sim/não — cada programa mostra o score que motivou a sugestão, então você entende o porquê e pode confiar (ou corrigir) a decisão.
- **Progresso real e cancelável**: a varredura mostra quantos arquivos e pastas já foram processados, e pode ser interrompida a qualquer momento.
- **Cache do último scan**: fechar e abrir o programa não obriga a escanear tudo de novo.
- **Edição manual por linha**, individual ou em lote, com prioridade sobre a sugestão automática.
- **Busca/filtro** na lista de resultados.
- **Backup e restauração** das preferências de GPU atuais do Windows, para desfazer mudanças com segurança.
- **Controle manual direto**, sem precisar escanear nada, para configurar um único programa rapidamente.

## Como funciona por baixo dos panos

O Windows guarda a preferência de GPU por aplicativo em uma chave do registro:

```
HKEY_CURRENT_USER\Software\Microsoft\DirectX\UserGpuPreferences
```

É a mesma chave usada pela tela nativa de Configurações Gráficas — ou seja, qualquer coisa que você configurar aqui aparece lá, e vice-versa. O GPU Manager só automatiza a leitura e escrita nessa chave, não mexe em drivers, BIOS ou qualquer configuração de hardware.

## Requisitos

| Requisito | Versão mínima |
|---|---|
| Windows | 10 (build 1803+) ou 11 |
| Python | 3.10 |

Bibliotecas Python usadas (instaladas via `requirements.txt`):

| Biblioteca | Uso | Obrigatória? |
|---|---|---|
| `tkinter` | Interface gráfica | Já vem com o Python no Windows |
| `WMI` | Mostrar o nome real das suas GPUs na tela | Opcional |
| `pywin32` | Dependência da biblioteca WMI | Opcional |

Se você não instalar `WMI`/`pywin32`, o programa funciona normalmente — só não mostra os nomes das placas detectadas no topo da janela.

## Instalação

1. Instale o [Python 3.10 ou superior](https://www.python.org/downloads/), marcando a opção **"Add python.exe to PATH"** durante a instalação.
2. Baixe ou clone este repositório.
3. Abra um terminal (PowerShell ou Prompt de Comando) dentro da pasta do projeto.
4. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

## Uso

Rode o programa com:


Um Duplo clique em `GPU_Manager.exe`.

**Passo a passo dentro do programa:**

1. *(Opcional)* Clique em **"Escolher pastas..."** para incluir alguma pasta extra na varredura, como um HD onde você instala jogos.
2. Clique em **"Escanear programas"** e aguarde. É possível cancelar a qualquer momento.
3. Revise a aba **"Programas encontrados"** — cada linha mostra a classificação, o score, a preferência atual e a sugestão.
4. Discordou de alguma sugestão? Selecione a linha (ou várias, com Ctrl/Shift) e use o painel à direita para forçar manualmente **Padrão**, **Integrada** ou **Dedicada**, depois clique em **"Aplicar à seleção"**.
5. Quando a lista estiver do jeito que você quer, clique em **"Aplicar sugestões automáticas"** para gravar tudo de uma vez no registro do Windows.
6. Use a aba **"Controle manual"** para configurar um único `.exe` sem precisar escanear o PC inteiro.
7. Faça um **Backup** antes de mudanças grandes — dá para restaurar depois pelo botão **"Restaurar backup..."**.

Feche e reabra os programas depois de mudar a preferência: o Windows só aplica a GPU escolhida quando o processo é iniciado.

## Estrutura do projeto

```
gpu_manager_v2/
├── gpu_manager.py         Interface gráfica e orquestração geral
├── scanner.py             Varredura de disco, classificação por score e cache
├── registry_backend.py    Leitura, escrita, backup e restauração no Registro
├── gpu_detection.py       Detecção das GPUs instaladas via WMI (opcional)
├── requirements.txt       Dependências Python
├── Abrir_GPU_Manager.bat  Atalho para abrir o programa
└── README.md
```

## Limitações conhecidas

- A classificação de "jogo" é uma heurística baseada em nome de pasta e padrões conhecidos. Ela é conservadora, mas não é perfeita — revise a lista antes de aplicar em lote, principalmente na primeira vez.
- Se um jogo é iniciado por um launcher que troca o processo em execução (Steam, Epic Games, Battle.net etc.), aponte a configuração para o `.exe` real do jogo dentro da pasta de instalação, não para o launcher.
- Alguns jogos e aplicativos têm seletor de GPU próprio, que pode ter prioridade sobre a preferência definida no Windows.
- A varredura não modifica nada em pastas de sistema do Windows (`System32`, `WinSxS` etc.) — isso é intencional, para evitar riscos desnecessários.

## Aviso

Este programa só escreve na chave de registro do **seu usuário** (`HKEY_CURRENT_USER`), não requer privilégios de administrador e não altera drivers, BIOS ou qualquer configuração de hardware. Ainda assim, é sempre recomendável fazer um backup (botão dentro do próprio programa) antes de aplicar mudanças em lote.

## Licença

Este projeto está sob a licença MIT — sinta-se livre para usar, modificar e distribuir.
