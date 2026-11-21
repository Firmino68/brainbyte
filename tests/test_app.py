import os, tempfile, unittest
from pathlib import Path

class BrainByteWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['BRAINBYTE_DB'] = str(Path(cls.tmp.name) / 'test.sqlite3')
        import app as brainbyte
        cls.app_module = brainbyte

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        os.environ.pop('BRAINBYTE_DB', None)

    def setUp(self):
        self.client = self.app_module.app.test_client()

    def token(self):
        with self.client.session_transaction() as sess:
            return sess['csrf_token']

    def test_home_loads(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'BrainByte', response.data)

    def test_profile_and_dashboard(self):
        self.client.get('/')
        response = self.client.post('/profile', data={'csrf_token':self.token(),'nickname':'JogadorTeste'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('O meu progresso'.encode(), response.data)

    def test_start_game_and_answer(self):
        self.client.get('/')
        self.client.post('/profile', data={'csrf_token':self.token(),'nickname':'TestePartida'})
        response = self.client.post('/start', data={'csrf_token':self.token(),'mode':'classic'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'QUEST', response.data)
        # Submit one valid answer (a valid option index for MC, otherwise a valid TF/code response).
        game = self.app_module.get_game()
        row, state = game
        qid = state['question_ids'][state['index']]
        q = next(q for q in self.app_module.QUESTIONS if q['id']==qid)
        answer = '1' if q['type']=='multiple_choice' else ('Verdadeiro' if q['type']=='true_false' else q['correct_answer'])
        response = self.client.post('/answer', data={'csrf_token':self.token(),'answer':answer}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Pr\xc3\xb3xima pergunta', response.data)

    def test_csrf_rejects_missing_token(self):
        response = self.client.post('/profile', data={'nickname':'SemToken'})
        self.assertEqual(response.status_code, 400)

if __name__ == '__main__': unittest.main()
