import tempfile,unittest
from pathlib import Path
from waterrepair import operations,performance
from waterrepair.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class PerformanceTests(unittest.TestCase):
    def test_capture_before_and_after_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            operations.pump(db,FIX/'pump-ok.json')
            operations.run(db,FIX/'pump-run-ok.json')
            operations.run(db,FIX/'pump-run-after.json')
            performance.test_point(db,FIX/'pump-test-before.json')
            performance.test_point(db,FIX/'pump-test-after.json')
            self.assertEqual([p['head_kpa'] for p in performance.points(db)],[300,250])
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            operations.pump(db,FIX/'pump-ok.json')
            operations.run(db,FIX/'pump-run-ok.json')
            performance.test_point(db,FIX/'pump-test-before.json')
            self.assertTrue(performance.test_point(db,FIX/'pump-test-before.json')['replayed'])
