"""Offline water meter identity and counter capture."""
import json
from .store import connect, source, repeated, at


def register(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'water_meter', file)
        if repeated(db, 'water_meter', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        segment = db.execute('SELECT * FROM segment WHERE id=?', (d['segment_id'],)).fetchone()
        if not segment or d['household'] not in json.loads(segment['households_json']) or int(d['modulus']) <= 0:
            raise ValueError('invalid_meter_identity')
        db.execute('INSERT INTO water_meter VALUES(?,?,?,?,?)',
                   (d['id'], d['segment_id'], d['household'], int(d['modulus']), sid))
        return {'id': d['id'], 'household': d['household']}


def reading(path, file):
    with connect(path) as db, db:
        sid, d, _ = source(db, 'meter_reading', file)
        if repeated(db, 'meter_reading', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        meter = db.execute('SELECT * FROM water_meter WHERE id=?', (d['meter_id'],)).fetchone()
        counter = int(d['counter_liters'])
        if not meter or counter < 0 or counter >= meter['modulus']:
            raise ValueError('invalid_meter_reading')
        moment = at(d['observed_at'])
        prior = db.execute('SELECT * FROM meter_reading WHERE meter_id=? ORDER BY observed_at DESC LIMIT 1',
                           (meter['id'],)).fetchone()
        if prior and (moment <= prior['observed_at'] or counter < prior['counter_liters']):
            raise ValueError('meter_reading_not_monotonic')
        db.execute('INSERT INTO meter_reading VALUES(?,?,?,?,?)',
                   (d['id'], meter['id'], moment, counter, sid))
        return {'id': d['id'], 'counter_liters': counter}
