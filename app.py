from __future__ import annotations
import json, os, random, re, secrets, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for, g

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('BRAINBYTE_DB', BASE / 'brainbyte.sqlite3'))
QUESTIONS_PATH = Path(os.environ.get('BRAINBYTE_QUESTIONS', BASE / 'questions.json'))
MODES = {
    'classic': {'label':'Clássico', 'rounds':8, 'description':'O desafio BrainByte original, com sequências e multiplicadores.', 'icon':'✦'},
    'timed': {'label':'Contra o tempo', 'rounds':20, 'seconds':60, 'description':'60 segundos para somar o máximo de pontos.', 'icon':'◷'},
    'survival': {'label':'Sobrevivência', 'rounds':20, 'lives':3, 'description':'Tens três vidas. Quantas perguntas consegues superar?', 'icon':'♥'},
    'expert': {'label':'Especialista', 'rounds':8, 'description':'Perguntas difíceis e recompensas maiores.', 'icon':'◆'},
}
ACHIEVEMENTS = [
    ('first_game','Primeira partida','Completa a tua primeira partida.','🎮'),
    ('first_win','Primeira vitória','Acerta pelo menos uma pergunta.','🏅'),
    ('streak_5','Imparável','Consegue uma sequência de 5 respostas certas.','⚡'),
    ('score_100','Centenário','Consegue 100 pontos numa partida.','💯'),
    ('games_10','Presença constante','Completa 10 partidas.','🔥'),
    ('perfect','Mente brilhante','Termina uma partida sem respostas erradas.','🧠'),
]

app = Flask(__name__)
app.secret_key = os.environ.get('BRAINBYTE_SECRET', 'dev-only-change-this-secret')
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')


def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY, nickname TEXT NOT NULL UNIQUE COLLATE NOCASE,
            xp INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY, player_id INTEGER NOT NULL REFERENCES players(id),
            mode TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0, correct INTEGER NOT NULL DEFAULT 0,
            answered INTEGER NOT NULL DEFAULT 0, best_streak INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, state_json TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, xp_awarded INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_games_player ON games(player_id, finished_at);
        CREATE TABLE IF NOT EXISTS achievements (
            player_id INTEGER NOT NULL REFERENCES players(id), code TEXT NOT NULL,
            unlocked_at TEXT NOT NULL, PRIMARY KEY(player_id, code)
        );
        ''')


def load_questions():
    try:
        raw = json.loads(QUESTIONS_PATH.read_text(encoding='utf-8'))
        items = raw.get('questions', raw) if isinstance(raw, (dict, list)) else []
        result = []
        for i, q in enumerate(items):
            if not isinstance(q, dict) or not q.get('prompt') or not q.get('correct_answer'):
                continue
            q = dict(q); q.setdefault('id', f'q{i+1:03d}'); q.setdefault('difficulty', 'Médio')
            q.setdefault('base_points', 10); q.setdefault('explanation', '')
            if q.get('type') == 'multiple_choice' and (not isinstance(q.get('options'), list) or len(q['options']) < 2): continue
            if q.get('type') not in ('multiple_choice','true_false','code_output'): continue
            result.append(q)
        if not result: raise RuntimeError('O banco de perguntas está vazio ou inválido.')
        return result
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'Não foi possível carregar questions.json: {exc}') from exc

QUESTIONS = load_questions()

@app.before_request
def csrf_setup():
    if 'csrf_token' not in session: session['csrf_token'] = secrets.token_urlsafe(24)
    if request.method == 'POST':
        token = request.form.get('csrf_token', '')
        if not token or not secrets.compare_digest(token, session['csrf_token']): abort(400, 'Pedido inválido. Atualiza a página e tenta novamente.')

@app.context_processor
def inject_globals():
    return {'csrf_token': session.get('csrf_token', ''), 'modes': MODES}


def player_for(nickname):
    with connect_db() as db:
        return db.execute('SELECT * FROM players WHERE nickname=? COLLATE NOCASE', (nickname,)).fetchone()


def get_player():
    pid = session.get('player_id')
    if not pid: return None
    with connect_db() as db: return db.execute('SELECT * FROM players WHERE id=?', (pid,)).fetchone()


def level_for(xp):
    # Progressão crescente: 0, 150, 400, 800, 1500, 3000...
    thresholds = [0,150,400,800,1500,3000,5000,8000,12000]
    level = 1
    for i, threshold in enumerate(thresholds, 1):
        if xp >= threshold: level = i
    current = thresholds[level-1]
    nxt = thresholds[level] if level < len(thresholds) else current + 5000
    return level, current, nxt, max(0, min(100, round((xp-current)/(nxt-current)*100)))


def unlock_achievements(db, player_id):
    stats = db.execute('''SELECT COUNT(*) games, COALESCE(MAX(score),0) best,
        COALESCE(MAX(best_streak),0) streak, COALESCE(SUM(correct),0) correct,
        COALESCE(SUM(answered-correct),0) wrong,
        COALESCE(MAX(CASE WHEN answered>0 AND answered=correct THEN 1 ELSE 0 END),0) perfect FROM games WHERE player_id=? AND status='finished' ''',(player_id,)).fetchone()
    rules = {
        'first_game': stats['games'] >= 1, 'first_win': stats['correct'] >= 1,
        'streak_5': stats['streak'] >= 5, 'score_100': stats['best'] >= 100,
        'games_10': stats['games'] >= 10, 'perfect': stats['perfect'] == 1,
    }
    newly=[]
    for code, ok in rules.items():
        if ok:
            cur=db.execute('INSERT OR IGNORE INTO achievements(player_id,code,unlocked_at) VALUES(?,?,?)',(player_id,code,datetime.now(timezone.utc).isoformat()))
            if cur.rowcount: newly.append(code)
    return newly


def get_game():
    gid=session.get('game_id')
    if not gid: return None
    with connect_db() as db: row=db.execute('SELECT * FROM games WHERE id=?',(gid,)).fetchone()
    if not row: return None
    data=json.loads(row['state_json'])
    return row, data


def save_state(db, gid, state, **fields):
    assignments=['state_json=?']; values=[json.dumps(state, ensure_ascii=False)]
    for key,value in fields.items(): assignments.append(f'{key}=?'); values.append(value)
    values.append(gid)
    db.execute(f"UPDATE games SET {', '.join(assignments)} WHERE id=?", values)


def finish_game(gid, row, state):
    if row['status']=='finished': return
    now=datetime.now(timezone.utc).isoformat()
    xp=state['score']//5 + state['correct']*3
    with connect_db() as db:
        db.execute("UPDATE games SET status='finished',finished_at=?,xp_awarded=? WHERE id=?",(now,xp,gid))
        db.execute('UPDATE players SET xp=xp+? WHERE id=?',(xp,row['player_id']))
        newly=unlock_achievements(db,row['player_id'])
    state['new_achievements']=newly
    with connect_db() as db:
        save_state(db, gid, state)
    session.pop('game_id',None)


def build_question_pool(mode):
    pool=list(QUESTIONS)
    if mode=='expert':
        hard=[q for q in pool if str(q.get('difficulty','')).lower() in ('difícil','dificil','especialista')]
        if hard: pool=hard
    random.shuffle(pool)
    return pool

@app.get('/')
def index():
    player=get_player()
    with connect_db() as db:
        top=db.execute('''SELECT p.nickname, MAX(g.score) score FROM players p JOIN games g ON g.player_id=p.id
            WHERE g.status='finished' GROUP BY p.id ORDER BY score DESC LIMIT 5''').fetchall()
        recent=db.execute("SELECT COUNT(*) n FROM games WHERE status='finished'").fetchone()['n']
    categories=sorted({q.get('category','Geral') for q in QUESTIONS})
    return render_template('index.html',player=player,top=top,categories=categories,total=len(QUESTIONS),recent=recent)

@app.post('/profile')
def profile():
    nickname=' '.join(request.form.get('nickname','').split())
    if not re.fullmatch(r'[\wÀ-ÿ -]{2,16}',nickname,re.UNICODE):
        flash('O nickname deve ter entre 2 e 16 caracteres.','error'); return redirect(url_for('index'))
    with connect_db() as db:
        db.execute('INSERT OR IGNORE INTO players(nickname,created_at) VALUES(?,?)',(nickname,datetime.now(timezone.utc).isoformat()))
        player=db.execute('SELECT * FROM players WHERE nickname=? COLLATE NOCASE',(nickname,)).fetchone()
    session['player_id']=player['id']; flash(f'Olá, {player["nickname"]}! Perfil pronto.','success')
    return redirect(url_for('dashboard'))

@app.get('/dashboard')
def dashboard():
    player=get_player()
    if not player: return redirect(url_for('index'))
    level,low,high,pct=level_for(player['xp'])
    with connect_db() as db:
        stats=db.execute('''SELECT COUNT(*) games, COALESCE(SUM(correct),0) correct, COALESCE(SUM(answered),0) answered,
            COALESCE(MAX(score),0) best, COALESCE(MAX(best_streak),0) streak, COALESCE(SUM(xp_awarded),0) xp_earned
            FROM games WHERE player_id=? AND status='finished' ''',(player['id'],)).fetchone()
        history=db.execute("SELECT mode,score,correct,answered,finished_at FROM games WHERE player_id=? AND status='finished' ORDER BY finished_at DESC LIMIT 8",(player['id'],)).fetchall()
        achievements=db.execute('SELECT code FROM achievements WHERE player_id=?',(player['id'],)).fetchall()
    unlocked={r['code'] for r in achievements}
    return render_template('dashboard.html',player=player,level=level,low=low,high=high,pct=pct,stats=stats,history=history,achievements=ACHIEVEMENTS,unlocked=unlocked)

@app.post('/logout')
def logout():
    session.pop('player_id',None); session.pop('game_id',None); flash('Sessão terminada.','success'); return redirect(url_for('index'))

@app.post('/start')
def start():
    player=get_player()
    if not player:
        flash('Cria ou seleciona primeiro o teu perfil.','error'); return redirect(url_for('index'))
    mode=request.form.get('mode','classic')
    if mode not in MODES: abort(400)
    pool=build_question_pool(mode)
    rounds=min(MODES[mode]['rounds'],len(pool))
    selected=pool[:rounds]
    state={'question_ids':[q['id'] for q in selected], 'index':0,'score':0,'streak':0,'best_streak':0,'correct':0,'wrong':0,'answered':0,'lives':3,'feedback':None,'mode':mode,'deadline':time.time()+MODES[mode].get('seconds',3600),'started':time.time()}
    gid=secrets.token_hex(16); now=datetime.now(timezone.utc).isoformat()
    with connect_db() as db:
        db.execute('INSERT INTO games(id,player_id,mode,status,state_json,started_at) VALUES(?,?,? ,?,?,?)',(gid,player['id'],mode,'active',json.dumps(state,ensure_ascii=False),now))
    session['game_id']=gid
    return redirect(url_for('game'))

@app.get('/game')
def game():
    item=get_game()
    if not item: return redirect(url_for('index'))
    row,state=item
    if row['status']!='active': return redirect(url_for('result',gid=row['id']))
    if state['index']>=len(state['question_ids']) or (state['mode']=='timed' and time.time()>state['deadline']) or (state['mode']=='survival' and state['lives']<=0):
        finish_game(row['id'],row,state); return redirect(url_for('result',gid=row['id']))
    qmap={q['id']:q for q in QUESTIONS}; q=qmap[state['question_ids'][state['index']]]
    player=get_player()
    return render_template('game.html',q=q,state=state,player=player,mode=MODES[state['mode']],remaining=max(0,int(state['deadline']-time.time())))

@app.post('/answer')
def answer():
    item=get_game()
    if not item: return redirect(url_for('index'))
    row,state=item
    if row['status']!='active' or state['feedback'] is not None: return redirect(url_for('game'))
    if state['mode']=='timed' and time.time()>state['deadline']:
        finish_game(row['id'],row,state); return redirect(url_for('result',gid=row['id']))
    q=next((q for q in QUESTIONS if q['id']==state['question_ids'][state['index']]),None)
    if not q: abort(500)
    answer=request.form.get('answer','').strip()
    if not answer or len(answer)>200: flash('Introduz uma resposta válida.','error'); return redirect(url_for('game'))
    kind=q['type']; correct_text=str(q['correct_answer'])
    if kind=='multiple_choice':
        opts=q['options'];
        if answer.isdigit() and 1<=int(answer)<=len(opts): answer=opts[int(answer)-1]
        elif len(answer)==1 and answer.upper() in 'ABCDEFGH':
            ix='ABCDEFGH'.index(answer.upper()); answer=opts[ix] if ix<len(opts) else answer
        valid=any(answer.casefold()==str(x).casefold() for x in opts)
        if not valid: flash('Escolhe uma das opções disponíveis.','error'); return redirect(url_for('game'))
        correct=answer.casefold()==correct_text.casefold()
    elif kind=='true_false':
        true_words={'verdadeiro','v','true','sim'}; false_words={'falso','f','false','não','nao'}
        if answer.casefold() in true_words: normalized='Verdadeiro'; expected=correct_text.casefold() in true_words
        elif answer.casefold() in false_words: normalized='Falso'; expected=correct_text.casefold() in true_words
        else: flash('Responde Verdadeiro ou Falso.','error'); return redirect(url_for('game'))
        correct= (normalized=='Verdadeiro')==expected; answer=normalized; correct_text='Verdadeiro' if expected else 'Falso'
    else:
        correct=' '.join(answer.split())==' '.join(correct_text.split())
    base=int(q.get('base_points',10)); base=base*2 if kind=='code_output' else max(1,base//2) if kind=='true_false' else base
    mult=min(1+0.2*state['streak'],2.0)
    awarded=int(round(base*mult)) if correct else 0
    if correct:
        state['score']+=awarded; state['correct']+=1; state['streak']+=1; state['best_streak']=max(state['best_streak'],state['streak'])
    else:
        state['wrong']+=1; state['streak']=0
        if state['mode']=='survival': state['lives']-=1
    state['answered']+=1
    state['feedback']={'correct':correct,'points':awarded,'multiplier':mult,'chosen':answer,'correct_answer':correct_text,'explanation':q.get('explanation',''),'curiosity':q.get('curiosity','')}
    with connect_db() as db: save_state(db,row['id'],state,score=state['score'],correct=state['correct'],answered=state['answered'],best_streak=state['best_streak'])
    return redirect(url_for('game'))

@app.post('/next')
def next_question():
    item=get_game()
    if not item: return redirect(url_for('index'))
    row,state=item
    if state['feedback'] is None: return redirect(url_for('game'))
    state['index']+=1; state['feedback']=None
    with connect_db() as db: save_state(db,row['id'],state)
    return redirect(url_for('game'))

@app.post('/finish')
def finish():
    item=get_game()
    if not item: return redirect(url_for('index'))
    row,state=item; finish_game(row['id'],row,state)
    return redirect(url_for('result',gid=row['id']))

@app.get('/result/<gid>')
def result(gid):
    player=get_player()
    if not player: return redirect(url_for('index'))
    with connect_db() as db:
        row=db.execute('SELECT * FROM games WHERE id=? AND player_id=?',(gid,player['id'])).fetchone()
    if not row: return redirect(url_for('dashboard'))
    state=json.loads(row['state_json'])
    if row['status']=='active':
        finish_game(gid,row,state)
        with connect_db() as db: row=db.execute('SELECT * FROM games WHERE id=? AND player_id=?',(gid,player['id'])).fetchone()
    level,low,high,pct=level_for(player['xp'])
    with connect_db() as db:
        updated=db.execute('SELECT * FROM players WHERE id=?',(player['id'],)).fetchone()
        top=db.execute('''SELECT p.nickname, MAX(g.score) score FROM players p JOIN games g ON g.player_id=p.id WHERE g.status='finished' GROUP BY p.id ORDER BY score DESC LIMIT 10''').fetchall()
        ach=db.execute('SELECT code FROM achievements WHERE player_id=?',(player['id'],)).fetchall()
    return render_template('result.html',row=row,state=state,player=updated,level=level,pct=pct,top=top,new_achievements=state.get('new_achievements',[]),achievements=ACHIEVEMENTS)

@app.get('/leaderboard')
def leaderboard():
    with connect_db() as db:
        top=db.execute('''SELECT p.nickname, MAX(g.score) score, COUNT(g.id) games FROM players p JOIN games g ON g.player_id=p.id WHERE g.status='finished' GROUP BY p.id ORDER BY score DESC LIMIT 50''').fetchall()
    return render_template('leaderboard.html',top=top)

@app.errorhandler(404)
def not_found(_): return render_template('error.html',message='Esta página não existe.'),404

@app.errorhandler(400)
def bad_request(error): return render_template('error.html',message=getattr(error,'description','Pedido inválido.')),400

init_db()

if __name__=='__main__':
    app.run(host='127.0.0.1',port=int(os.environ.get('PORT',5000)),debug=os.environ.get('FLASK_DEBUG')=='1')
