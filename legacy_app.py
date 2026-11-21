"""
BrainByte - jogo de perguntas e respostas por rondas (Flask + POO).

Executar:  python app.py   ->   http://127.0.0.1:5000

Classes (diagrama do projeto):
    Question (abstrata) -> MultipleChoiceQuestion, TrueFalseQuestion, CodeOutputQuestion
    Player, ScoreBoard, Game
"""
from __future__ import annotations

import json
import os
import random
import re
import tempfile
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
QUESTIONS_FILE = BASE_DIR / "questions.json"
HALL_OF_FAME_FILE = BASE_DIR / "hall_of_fame.json"


# --------------------------------------------------------------------------
# Excecoes proprias (permitem tratar cada tipo de erro com try/except)
# --------------------------------------------------------------------------
class BrainByteError(Exception):
    """Erro base da aplicacao."""


class InvalidAnswerError(BrainByteError, ValueError):
    """A resposta introduzida pelo utilizador nao e valida."""


class InvalidQuestionError(BrainByteError, ValueError):
    """Uma pergunta do ficheiro JSON esta mal formada."""


class InvalidNicknameError(BrainByteError, ValueError):
    """O nickname nao cumpre as regras."""


class QuestionLoadError(BrainByteError):
    """Nao foi possivel carregar o ficheiro de perguntas."""


class GameStateError(BrainByteError):
    """Operacao incompativel com o estado atual do jogo."""


# --------------------------------------------------------------------------
# Hierarquia de perguntas (R1 encapsulamento, R2 heranca e polimorfismo)
# --------------------------------------------------------------------------
class Question(ABC):
    """Classe base abstrata. Os atributos protegidos so se acedem por getters."""

    TYPE_KEY = "base"
    TYPE_LABEL = "Pergunta"
    MAX_ANSWER_LENGTH = 200

    def __init__(self, prompt, category, base_points, correct_answer, explanation=""):
        self._prompt = self._clean_text(prompt, "prompt")
        self._category = self._clean_text(category, "category")
        self._correct_answer = self._clean_text(correct_answer, "correct_answer")
        if isinstance(base_points, bool) or not isinstance(base_points, int) or base_points <= 0:
            raise InvalidQuestionError("'base_points' tem de ser um inteiro positivo.")
        self._base_points = base_points
        self._explanation = explanation.strip() if isinstance(explanation, str) else ""

    # ---- utilitarios ----
    @staticmethod
    def _clean_text(value, field):
        if not isinstance(value, str) or not value.strip():
            raise InvalidQuestionError(f"Campo '{field}' em falta ou invalido.")
        return value.strip()

    @classmethod
    def _require_text(cls, user_input):
        """Valida a resposta do utilizador: texto nao vazio e de tamanho razoavel."""
        if not isinstance(user_input, str) or not user_input.strip():
            raise InvalidAnswerError("Escreve ou escolhe uma resposta antes de submeter.")
        text = user_input.strip()
        if len(text) > cls.MAX_ANSWER_LENGTH:
            raise InvalidAnswerError(f"A resposta e demasiado longa (maximo {cls.MAX_ANSWER_LENGTH} caracteres).")
        return text

    # ---- getters ----
    def get_prompt(self):
        return self._prompt

    def get_category(self):
        return self._category

    def get_base_points(self):
        return self._base_points

    def get_explanation(self):
        return self._explanation

    def get_correct_answer(self):
        return self._correct_answer

    def get_type_label(self):
        return self.TYPE_LABEL

    @property
    def kind(self):
        return self.TYPE_KEY

    def format_answer(self, user_input):
        """Texto legivel da resposta dada (usado no feedback)."""
        return self._require_text(user_input)

    # ---- interface polimorfica ----
    @abstractmethod
    def evaluate_answer(self, user_input: str) -> bool:
        """True se a resposta estiver certa. Levanta InvalidAnswerError se for invalida."""

    @abstractmethod
    def calculate_points(self) -> int:
        """Pontos base que a pergunta vale (antes do multiplicador de sequencia)."""

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict) -> "Question":
        """Cria a pergunta a partir de um dicionario lido do JSON."""


class MultipleChoiceQuestion(Question):
    TYPE_KEY = "multiple_choice"
    TYPE_LABEL = "Múltipla escolha"
    LETTERS = "ABCDEFGH"

    def __init__(self, prompt, category, base_points, correct_answer, options, explanation=""):
        super().__init__(prompt, category, base_points, correct_answer, explanation)
        if (
            not isinstance(options, list)
            or not 2 <= len(options) <= len(self.LETTERS)
            or not all(isinstance(o, str) and o.strip() for o in options)
        ):
            raise InvalidQuestionError(f"'options' tem de ser uma lista de 2 a {len(self.LETTERS)} textos.")
        cleaned = [o.strip() for o in options]
        if len({o.casefold() for o in cleaned}) != len(cleaned):
            raise InvalidQuestionError("'options' tem opcoes repetidas.")
        match = next((o for o in cleaned if o.casefold() == self._correct_answer.casefold()), None)
        if match is None:
            raise InvalidQuestionError("'correct_answer' tem de ser uma das 'options'.")
        self._options = cleaned
        self._correct_answer = match  # texto canonico, igual ao da opcao

    def display_options(self):
        return [
            {"key": str(i), "letter": self.LETTERS[i - 1], "text": text}
            for i, text in enumerate(self._options, start=1)
        ]

    def _resolve_choice(self, user_input):
        """Aceita o numero (1..n), o texto da opcao ou a letra (A..)."""
        text = self._require_text(user_input)
        if text.isascii() and text.isdigit():
            index = int(text)
            if 1 <= index <= len(self._options):
                return self._options[index - 1]
        for option in self._options:
            if option.casefold() == text.casefold():
                return option
        if len(text) == 1 and text.isalpha():
            index = self.LETTERS.find(text.upper())
            if 0 <= index < len(self._options):
                return self._options[index]
        raise InvalidAnswerError("Escolhe uma das opções disponíveis.")

    def format_answer(self, user_input):
        return self._resolve_choice(user_input)

    def evaluate_answer(self, user_input: str) -> bool:
        return self._resolve_choice(user_input) == self._correct_answer

    def calculate_points(self) -> int:
        return self._base_points

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("prompt"), data.get("category"), data.get("base_points", 10),
            data.get("correct_answer"), data.get("options"), data.get("explanation", ""),
        )


class TrueFalseQuestion(Question):
    TYPE_KEY = "true_false"
    TYPE_LABEL = "Verdadeiro ou falso"
    _TRUE_WORDS = {"verdadeiro", "v", "true", "t", "sim", "s"}
    _FALSE_WORDS = {"falso", "f", "false", "nao", "não", "n"}

    def __init__(self, prompt, category, base_points, correct_answer, explanation=""):
        try:
            self._correct_bool = self._to_bool(correct_answer)
        except InvalidAnswerError as exc:
            raise InvalidQuestionError("'correct_answer' tem de ser Verdadeiro ou Falso.") from exc
        super().__init__(
            prompt, category, base_points, "Verdadeiro" if self._correct_bool else "Falso", explanation
        )

    @classmethod
    def _to_bool(cls, value):
        if isinstance(value, bool):
            return value
        word = cls._require_text(value).casefold()
        if word in cls._TRUE_WORDS:
            return True
        if word in cls._FALSE_WORDS:
            return False
        raise InvalidAnswerError("Responde «Verdadeiro» ou «Falso».")

    def format_answer(self, user_input):
        return "Verdadeiro" if self._to_bool(user_input) else "Falso"

    def evaluate_answer(self, user_input: str) -> bool:
        return self._to_bool(user_input) == self._correct_bool

    def calculate_points(self) -> int:
        # Pergunta mais facil (50 % de acertar ao acaso): vale metade.
        return max(1, self._base_points // 2)

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("prompt"), data.get("category"), data.get("base_points", 10),
            data.get("correct_answer"), data.get("explanation", ""),
        )


class CodeOutputQuestion(Question):
    TYPE_KEY = "code_output"
    TYPE_LABEL = "Output de código"

    def __init__(self, prompt, category, base_points, correct_answer, code_snippet, explanation=""):
        super().__init__(prompt, category, base_points, correct_answer, explanation)
        if not isinstance(code_snippet, str) or not code_snippet.strip():
            raise InvalidQuestionError("Campo 'code_snippet' em falta ou invalido.")
        self._code_snippet = code_snippet.strip("\n").rstrip()

    def get_code_snippet(self):
        return self._code_snippet

    @staticmethod
    def _normalize(text):
        # Ignora diferencas de espacos e mudancas de linha.
        return " ".join(text.split())

    def evaluate_answer(self, user_input: str) -> bool:
        answer = self._require_text(user_input)
        return self._normalize(answer) == self._normalize(self._correct_answer)

    def calculate_points(self) -> int:
        # Exige executar o codigo mentalmente: vale o dobro.
        return self._base_points * 2

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("prompt"), data.get("category"), data.get("base_points", 10),
            data.get("correct_answer"), data.get("code_snippet"), data.get("explanation", ""),
        )


QUESTION_TYPES = {
    cls.TYPE_KEY: cls for cls in (MultipleChoiceQuestion, TrueFalseQuestion, CodeOutputQuestion)
}


# --------------------------------------------------------------------------
# Jogador
# --------------------------------------------------------------------------
class Player:
    STREAK_STEP = 0.2       # cada resposta certa seguida soma +0.2 ao multiplicador
    MAX_MULTIPLIER = 2.0

    def __init__(self, nickname: str):
        self._nickname = nickname
        self._score = 0
        self._streak = 0
        self._best_streak = 0
        self._correct_answers = 0

    @classmethod
    def multiplier_for(cls, streak: int) -> float:
        steps = max(streak, 1) - 1
        return min(round(1 + cls.STREAK_STEP * steps, 2), cls.MAX_MULTIPLIER)

    def add_score(self, points: int) -> int:
        """Regista uma resposta certa; devolve os pontos efetivamente ganhos."""
        if isinstance(points, bool) or not isinstance(points, int) or points < 0:
            raise ValueError("Os pontos tem de ser um inteiro nao negativo.")
        self._streak += 1
        self._best_streak = max(self._best_streak, self._streak)
        self._correct_answers += 1
        awarded = int(round(points * self.get_multiplier()))
        self._score += awarded
        return awarded

    def reset_streak(self) -> None:
        self._streak = 0

    def get_score(self) -> int:
        return self._score

    def get_nickname(self) -> str:
        return self._nickname

    def get_streak(self) -> int:
        return self._streak

    def get_best_streak(self) -> int:
        return self._best_streak

    def get_correct_answers(self) -> int:
        return self._correct_answers

    def get_multiplier(self) -> float:
        return self.multiplier_for(self._streak)

    def get_next_multiplier(self) -> float:
        return self.multiplier_for(self._streak + 1)


# --------------------------------------------------------------------------
# Hall of Fame (persistencia em JSON)
# --------------------------------------------------------------------------
class ScoreBoard:
    MAX_ENTRIES = 10

    def __init__(self, filename):
        self._filename = Path(filename)
        self._top_scores: list[dict] = []
        self._lock = threading.RLock()
        self.load_scores()

    def load_scores(self) -> None:
        """Le o ficheiro. Se faltar ou estiver corrompido, comeca com lista vazia."""
        with self._lock:
            self._top_scores = []
            try:
                with self._filename.open(encoding="utf-8") as fh:
                    data = json.load(fh)
            except FileNotFoundError:
                return
            except (ValueError, OSError) as exc:  # JSON invalido, encoding ou I/O
                print(f"[aviso] Nao foi possivel ler {self._filename.name}: {exc}")
                return
            raw = data.get("scores", []) if isinstance(data, dict) else data
            if not isinstance(raw, list):
                return
            valid = []
            for entry in raw:
                score = entry.get("score") if isinstance(entry, dict) else None
                if isinstance(entry, dict) and isinstance(entry.get("nickname"), str) \
                        and isinstance(score, int) and not isinstance(score, bool):
                    valid.append({"nickname": entry["nickname"], "score": score,
                                  "date": str(entry.get("date", ""))})
            valid.sort(key=lambda e: e["score"], reverse=True)
            self._top_scores = valid[: self.MAX_ENTRIES]

    def save_score(self, player_name: str, score: int):
        """Guarda o resultado. Devolve a posicao (1..10) ou None se nao entrou no top."""
        if isinstance(score, bool) or not isinstance(score, int) or score < 0:
            raise ValueError("A pontuacao tem de ser um inteiro nao negativo.")
        if score == 0:
            return None
        with self._lock:
            self.load_scores()  # apanha alteracoes feitas ao ficheiro entretanto
            entry = {"nickname": player_name, "score": score,
                     "date": datetime.now().strftime("%d/%m/%Y")}
            self._top_scores.append(entry)
            self._top_scores.sort(key=lambda e: e["score"], reverse=True)  # ordenacao estavel
            self._top_scores = self._top_scores[: self.MAX_ENTRIES]
            rank = next((i for i, e in enumerate(self._top_scores, 1) if e is entry), None)
            self._write()
            return rank

    def _write(self) -> None:
        """Escrita atomica: ficheiro temporario + os.replace (evita JSON a meio)."""
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=self._filename.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"scores": self._top_scores}, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._filename)
        except OSError as exc:
            print(f"[aviso] Nao foi possivel gravar {self._filename.name}: {exc}")
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    def display_top(self, limit: int = MAX_ENTRIES) -> list[dict]:
        with self._lock:
            return [{"rank": i, **e} for i, e in enumerate(self._top_scores[:limit], 1)]


# --------------------------------------------------------------------------
# Jogo
# --------------------------------------------------------------------------
class Game:
    ROUNDS_PER_GAME = 8
    NICKNAME_RE = re.compile(r"[\w \-]{2,16}")

    def __init__(self, questions_file, scoreboard: ScoreBoard):
        self._scoreboard = scoreboard
        self._all_questions: list[Question] = []
        self._questions: list[Question] = []
        self._player: Player | None = None
        self._index = 0
        self._answered = False
        self._answered_count = 0
        self._ended = False
        self._last_feedback: dict | None = None
        self._score_saved = False
        self._rank = None
        self.load_questions(questions_file)

    # ---- carregamento ----
    def load_questions(self, filename) -> int:
        try:
            with Path(filename).open(encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError as exc:
            raise QuestionLoadError(f"Ficheiro de perguntas não encontrado: {filename}") from exc
        except (ValueError, OSError) as exc:
            raise QuestionLoadError(f"Não foi possível ler {Path(filename).name}: {exc}") from exc

        raw = data.get("questions") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise QuestionLoadError("O ficheiro de perguntas deve conter uma lista 'questions'.")

        loaded = []
        for position, item in enumerate(raw, start=1):
            try:
                if not isinstance(item, dict):
                    raise InvalidQuestionError("não é um objeto JSON.")
                kind = item.get("type")
                cls = QUESTION_TYPES.get(kind) if isinstance(kind, str) else None
                if cls is None:
                    raise InvalidQuestionError(f"tipo desconhecido: {kind!r}.")
                loaded.append(cls.from_dict(item))
            except InvalidQuestionError as exc:  # uma pergunta ma nao derruba as outras
                print(f"[aviso] Pergunta #{position} ignorada: {exc}")
        if not loaded:
            raise QuestionLoadError("Não há perguntas válidas em questions.json.")
        self._all_questions = loaded
        return len(loaded)

    # ---- menu / inicio ----
    def display_menu(self) -> dict:
        return {
            "total_questions": len(self._all_questions),
            "rounds": min(self.ROUNDS_PER_GAME, len(self._all_questions)),
            "categories": sorted({q.get_category() for q in self._all_questions}),
            "top": self._scoreboard.display_top(3),
        }

    def start_game(self, nickname) -> None:
        nick = " ".join(str(nickname or "").split())
        if not self.NICKNAME_RE.fullmatch(nick):
            raise InvalidNicknameError(
                "O nickname deve ter entre 2 e 16 caracteres (letras, números, espaço, _ ou -)."
            )
        pool = list(self._all_questions)
        random.shuffle(pool)
        self._questions = pool[: self.ROUNDS_PER_GAME]
        self._player = Player(nick)
        self._index = 0
        self._answered = False
        self._answered_count = 0
        self._ended = False
        self._last_feedback = None
        self._score_saved = False
        self._rank = None

    # ---- ciclo de rondas ----
    def play_round(self, user_input) -> dict:
        """Avalia a resposta da ronda atual (polimorfismo: nao conhece o tipo concreto)."""
        question = self.current_question
        if question is None or self._player is None:
            raise GameStateError("Não há nenhum jogo em curso.")
        if self._answered:
            raise GameStateError("Esta pergunta já foi respondida.")

        correct = question.evaluate_answer(user_input)   # pode levantar InvalidAnswerError
        chosen = question.format_answer(user_input)
        if correct:
            awarded = self._player.add_score(question.calculate_points())
        else:
            awarded = 0
            self._player.reset_streak()
        self._answered = True
        self._answered_count += 1
        self._last_feedback = {
            "correct": correct,
            "points": awarded,
            "multiplier": self._player.get_multiplier() if correct else 1.0,
            "chosen_text": chosen,
            "correct_text": question.get_correct_answer(),
            "explanation": question.get_explanation(),
        }
        return self._last_feedback

    def next_round(self) -> bool:
        if not self._answered:
            raise GameStateError("Responde primeiro à pergunta atual.")
        self._index += 1
        self._answered = False
        self._last_feedback = None
        return not self.is_over

    def end_early(self) -> None:
        self._ended = True

    def finish(self):
        """Guarda o resultado no Hall of Fame (uma unica vez) e devolve a posicao."""
        if self._player is None or not self.is_over:
            raise GameStateError("O jogo ainda não terminou.")
        if not self._score_saved:
            self._rank = self._scoreboard.save_score(self._player.get_nickname(), self._player.get_score())
            self._score_saved = True
        return self._rank

    # ---- estado ----
    @property
    def player(self):
        return self._player

    @property
    def scoreboard(self):
        return self._scoreboard

    @property
    def current_question(self):
        if self._player is None or self.is_over:
            return None
        return self._questions[self._index]

    @property
    def is_over(self) -> bool:
        return self._ended or self._index >= len(self._questions)

    @property
    def round_number(self) -> int:
        return min(self._index + 1, len(self._questions))

    @property
    def total_rounds(self) -> int:
        return len(self._questions)

    @property
    def answered_count(self) -> int:
        return self._answered_count

    @property
    def last_feedback(self):
        return self._last_feedback


# --------------------------------------------------------------------------
# Aplicacao Flask
# --------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("BRAINBYTE_SECRET", "brainbyte-chave-de-desenvolvimento")

SCOREBOARD = ScoreBoard(HALL_OF_FAME_FILE)
GAMES: dict[str, Game] = {}     # jogos em memoria, um por sessao do browser
MAX_GAMES = 200


def new_game() -> Game:
    return Game(QUESTIONS_FILE, SCOREBOARD)


def current_game():
    return GAMES.get(session.get("game_id", ""))


def register_game(game: Game) -> None:
    GAMES.pop(session.get("game_id", ""), None)
    game_id = uuid.uuid4().hex
    GAMES[game_id] = game
    session["game_id"] = game_id
    while len(GAMES) > MAX_GAMES:               # evita crescer sem limite
        GAMES.pop(next(iter(GAMES)))


def render_index(error=None, nickname="", status=200):
    active = current_game()
    return render_template(
        "index.html",
        menu=new_game().display_menu(),
        error=error,
        nickname=nickname,
        has_active_game=bool(active and active.player and not active.is_over),
    ), status


@app.errorhandler(QuestionLoadError)
def handle_question_error(exc):
    return f"<h1>Erro ao carregar as perguntas</h1><p>{exc}</p>", 500


@app.errorhandler(404)
def handle_404(_exc):
    return redirect(url_for("index"))


@app.get("/")
def index():
    return render_index()


@app.post("/start")
def start():
    nickname = request.form.get("nickname", "")
    game = new_game()
    try:
        game.start_game(nickname)
    except InvalidNicknameError as exc:
        return render_index(error=str(exc), nickname=nickname[:40], status=400)
    register_game(game)
    return redirect(url_for("game_view"))


@app.get("/game")
def game_view():
    game = current_game()
    if game is None or game.player is None:
        return redirect(url_for("index"))
    if game.is_over:
        return redirect(url_for("gameover"))
    return render_template(
        "game.html", game=game, player=game.player,
        q=game.current_question, feedback=game.last_feedback,
    )


@app.post("/answer")
def answer():
    game = current_game()
    if game is None:
        return redirect(url_for("index"))
    try:
        game.play_round(request.form.get("answer", ""))
    except InvalidAnswerError as exc:
        flash(str(exc), "warning")
    except GameStateError as exc:
        flash(str(exc), "info")
    return redirect(url_for("game_view"))


@app.post("/next")
def next_question():
    game = current_game()
    if game is None:
        return redirect(url_for("index"))
    try:
        game.next_round()
    except GameStateError as exc:
        flash(str(exc), "info")
    return redirect(url_for("gameover") if game.is_over else url_for("game_view"))


@app.post("/finish")
def finish():
    game = current_game()
    if game is None:
        return redirect(url_for("index"))
    game.end_early()
    return redirect(url_for("gameover"))


@app.get("/gameover")
def gameover():
    game = current_game()
    if game is None or game.player is None:
        return redirect(url_for("index"))
    try:
        rank = game.finish()
    except GameStateError:
        return redirect(url_for("game_view"))
    return render_template(
        "gameover.html", game=game, player=game.player,
        rank=rank, top=game.scoreboard.display_top(),
    )


if __name__ == "__main__":
    new_game()  # falha cedo, com mensagem clara, se o questions.json estiver mal
    app.run(host="127.0.0.1", port=5000, debug=True)
