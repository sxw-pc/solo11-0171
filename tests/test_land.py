import tempfile,unittest
from pathlib import Path
from waterrepair import land
from waterrepair.report import audit

FIX=Path(__file__).resolve().parents[1]/'fixtures'

class LandTests(unittest.TestCase):
    def test_route_capture_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            land.worksite(db,FIX/'worksite-ok.json')
            land.parcel(db,FIX/'parcel-ok.json')
            land.corridor(db,FIX/'corridor-ok.json')
            land.consent(db,FIX/'land-consent-ok.json')
            self.assertEqual(land.route_report(db)['corridors'][0]['width_cm'],200)
            self.assertTrue(audit(db)['replay_ok'])
    def test_replay_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite'
            land.consent(db,FIX/'land-consent-ok.json')
            self.assertTrue(land.consent(db,FIX/'land-consent-ok.json')['replayed'])
