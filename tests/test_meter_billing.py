import unittest
import tempfile
from pathlib import Path
from waterrepair import pressure, metering, billing
from waterrepair.report import report, audit

FIX = Path(__file__).resolve().parents[1] / 'fixtures'


class MeterBillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'meter.sqlite'
        pressure.segment(self.db, FIX / 'segment-ok.json')
        metering.register(self.db, FIX / 'meter-ok.json')
        metering.reading(self.db, FIX / 'read-open-ok.json')
        billing.tariff(self.db, FIX / 'tariff-water-ok.json')

    def test_invoice_and_audit(self):
        metering.reading(self.db, FIX / 'read-close-ok.json')
        result = billing.issue(self.db, FIX / 'invoice-water-ok.json')
        self.assertEqual(result['used_liters'], 1500)
        self.assertEqual(result['amount_cents'], 450)
        self.assertTrue(billing.issue(self.db, FIX / 'invoice-water-ok.json')['replayed'])
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_incomplete_pair_cannot_invoice(self):
        with self.assertRaisesRegex(ValueError, 'invoice_readings_invalid'):
            billing.issue(self.db, FIX / 'invoice-water-ok.json')
        self.assertEqual(report(self.db)['state']['water_invoice'], [])
        self.assertTrue(audit(self.db)['replay_ok'])


if __name__ == '__main__':
    unittest.main()
