# BrainByte Evolution

Uma evolução do projeto original BrainByte, mantendo o jogo de perguntas em Flask e a identidade visual de oito bits, agora com perfis persistentes, quatro modos de jogo, XP, níveis, conquistas, estatísticas e ranking.

## Requisitos

- Python 3.10 ou superior
- pip

## Instalação

```bash
python -m venv .venv
```

Ativar o ambiente:

**Windows (PowerShell)**
```powershell
.venv\Scripts\Activate.ps1
```

**macOS / Linux**
```bash
source .venv/bin/activate
```

Instalar dependências e iniciar:

```bash
pip install -r requirements.txt
python app.py
```

Abrir http://127.0.0.1:5000.

A base de dados `brainbyte.sqlite3` é criada automaticamente na primeira execução. Os dados ficam guardados localmente.

## Configuração para produção

Define uma chave secreta forte através da variável `BRAINBYTE_SECRET`. Não uses a chave de desenvolvimento incluída no código em produção. Define também `BRAINBYTE_DB` para um caminho persistente e mantém `FLASK_DEBUG=0`. Para publicar na Internet, utiliza um servidor WSGI (por exemplo, Waitress ou Gunicorn, conforme o sistema operativo) atrás de HTTPS; não utilizes o servidor de desenvolvimento do Flask como servidor público.

Exemplo PowerShell:
```powershell
$env:BRAINBYTE_SECRET = "uma-chave-aleatoria-longa-e-segura"
$env:FLASK_DEBUG = "0"
python app.py
```

## Funcionalidades incluídas

- Perfil persistente identificado por nickname (sem palavra-passe/conta autenticada nesta versão).
- Modo Clássico: 8 perguntas e multiplicador por sequência.
- Contra o tempo: 60 segundos.
- Sobrevivência: três vidas.
- Especialista: dá preferência às perguntas marcadas como difíceis/especialista.
- Banco inicial com 31 perguntas em quatro categorias e três tipos de pergunta.
- Pontos, XP, níveis, histórico, estatísticas e conquistas.
- Hall of Fame global.
- Base de dados SQLite, validação de entradas e token CSRF nas operações POST.
- Estado da partida guardado na base de dados para sobreviver a reinícios do processo.

## Estrutura

```text
brainbyte_evolution/
├── app.py
├── questions.json
├── requirements.txt
├── README.md
├── .env.example
├── legacy_app.py
├── static/style.css
├── templates/
└── tests/
```

`legacy_app.py` contém a versão original recebida, para consulta e comparação. `questions_original.json` e `hall_of_fame_original.json` são cópias de referência dos dados originais. A aplicação utiliza `questions.json` e cria a sua própria base de dados SQLite.

## Personalizar perguntas

Edita `questions.json`. Cada pergunta deve ter `id`, `type`, `category`, `prompt`, `correct_answer`, `base_points`, `explanation` e `difficulty`. Tipos aceites: `multiple_choice` (também exige `options`), `true_false` e `code_output` (também exige `code_snippet`). IDs têm de ser únicos.

## Limitações conhecidas / próximos passos

- O perfil é identificado apenas pelo nickname; não existe autenticação individual nem recuperação de palavra-passe.
- O modo Contra o tempo utiliza um temporizador de interface e valida o prazo no servidor quando há novo pedido. Uma experiência cronometrada totalmente contínua pode evoluir para atualizações assíncronas.
- O modo Especialista usa as perguntas marcadas como Difícil/Especialista; se não houver perguntas suficientes, recorre ao banco completo.
- Ainda não estão incluídos IA, multiplayer em tempo real, torneios, execução de código submetido pelo utilizador ou integração externa de pagamentos.
- Não é executado código Python escrito pelo jogador. Os exercícios de output são avaliados por comparação com respostas guardadas.

## Testes

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```
