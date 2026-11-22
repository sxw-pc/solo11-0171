import tempfile,unittest
from pathlib import Path
from waterrepair import operations,vibration
from waterrepair.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class VibrationCaptureTests(unittest.TestCase):
    def test_capture_waveform_and_tach(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            operations.pump(db,FIX/'pump-ok.json')
            operations.run(db,FIX/'pump-run-ok.json')
            vibration.sample(db,FIX/'vibration-sample-ok.json')
            vibration.tach(db,FIX/'tachometer-ok.json')
            self.assertEqual(vibration.captured(db)['samples'][0]['sample_rate_hz'],200)
            self.assertTrue(audit(db)['replay_ok'])
    def test_duplicate_waveform(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            operations.pump(db,FIX/'pump-ok.json')
            operations.run(db,FIX/'pump-run-ok.json')
            vibration.sample(db,FIX/'vibration-sample-ok.json')
            self.assertTrue(vibration.sample(db,FIX/'vibration-sample-ok.json')['replayed'])
