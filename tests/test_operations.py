import tempfile,unittest
from pathlib import Path
from waterrepair import operations
from waterrepair.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class OperationsTests(unittest.TestCase):
    def test_pump_and_demand_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            operations.pump(db,FIX/'pump-ok.json')
            operations.tank(db,FIX/'tank-ok.json')
            operations.run(db,FIX/'pump-run-ok.json')
            operations.demand(db,FIX/'zone-demand-ok.json')
            self.assertEqual(operations.operation_report(db)['runs'][0]['volume_l'],72000)
            self.assertTrue(audit(db)['replay_ok'])
    def test_replay_pump(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            operations.pump(db,FIX/'pump-ok.json')
            self.assertTrue(operations.pump(db,FIX/'pump-ok.json')['replayed'])
