"""Tariff version and invoice from captured meter readings."""
from .store import connect, source, repeated, at


def tariff(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'water_tariff', file)
        if repeated(db, 'water_tariff', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        rate = int(d['cents_per_1000_liters'])
        if rate < 0:
            raise ValueError('invalid_tariff')
        db.execute('INSERT INTO water_tariff VALUES(?,?,?,?)',
                   (d['id'], at(d['effective_from']), rate, sid))
        return {'id': d['id'], 'rate': rate}


def issue(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'water_invoice', file)
        if repeated(db, 'water_invoice', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        opened = db.execute('SELECT * FROM meter_reading WHERE id=?', (d['opening_read_id'],)).fetchone()
        closed = db.execute('SELECT * FROM meter_reading WHERE id=?', (d['closing_read_id'],)).fetchone()
        if not opened or not closed or opened['meter_id'] != closed['meter_id'] or opened['observed_at'] >= closed['observed_at']:
            raise ValueError('invoice_readings_invalid')
        used = closed['counter_liters'] - opened['counter_liters']
        if used < 0:
            raise ValueError('invoice_counter_regressed')
        rate = db.execute('SELECT * FROM water_tariff WHERE effective_from<=? ORDER BY effective_from DESC LIMIT 1',
                          (opened['observed_at'],)).fetchone()
        if not rate:
            raise ValueError('tariff_missing')
        amount = (used * rate['cents_per_1000_liters'] + 999) // 1000
        db.execute('INSERT INTO water_invoice VALUES(?,?,?,?,?,?,?)',
                   (d['id'], opened['meter_id'], opened['id'], closed['id'], used, amount, sid))
        return {'id': d['id'], 'used_liters': used, 'amount_cents': amount}
