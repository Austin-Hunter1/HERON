import copy
import math
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from engine.geometry import lla_to_ecef, haversine_m
from engine.planner import plan, WATER_H
from engine.science import normalize_stages
from engine.export import qgc_wpl, parse_wpl, validate_wpl, run_card, plan_route_line, annotate_wpl, route_from_wpl

START = datetime(2026, 11, 16, 17, 15, tzinfo=timezone.utc)
PAD = dict(lat=40.002, lon=-105.005)
WATER = {'type':'FeatureCollection', 'features':[{'type':'Feature', 'properties':{}, 'geometry':{
    'type':'Polygon', 'coordinates':[[[-105.02,39.98],[-104.98,39.98],[-104.98,40],[-105.02,40],[-105.02,39.98]]]}}]}

def satellite(sid, az, el):
    lat, lon = math.radians(40), math.radians(-105)
    base = lla_to_ecef(40, -105, WATER_H)
    def ecef(dt):
        a = math.radians(az + .001 * (dt - START).total_seconds())
        e = math.radians(el)
        east, north, up = math.sin(a)*math.cos(e), math.cos(a)*math.cos(e), math.sin(e)
        x = -math.sin(lon)*east-math.sin(lat)*math.cos(lon)*north+math.cos(lat)*math.cos(lon)*up
        y = math.cos(lon)*east-math.sin(lat)*math.sin(lon)*north+math.cos(lat)*math.sin(lon)*up
        z = math.cos(lat)*north+math.sin(lat)*up
        return tuple(b + 23e6 * v for b,v in zip(base,(x,y,z)))
    return SimpleNamespace(sid=sid, health=0, constellation=sid[0], gps=None, epoch=None, ecef=ecef)

SATS = [satellite('G09',45,55),satellite('E05',47,56),satellite('C12',44,54),satellite('E23',135,50)]

def phases():
    return [dict(type='hover', name='Group', satellites=['G09','E05','C12'],duration_s=120,
                 points=[dict(lat=39.9975,lon=-105.002)]),
            dict(type='hover',name='Other',satellites=['E23'],duration_s=120,
                 points=[dict(lat=39.997,lon=-105.004)]),
            dict(type='passes',name='Shore',satellites=['G09','E05','C12'],count=3,spacing_m=25,speed_mps=3,
                 points=[dict(lat=39.998,lon=-105),dict(lat=40.001,lon=-105)])]

def build(stages=None, sats=None):
    return plan(sats or SATS,WATER,start=START,duration_min=40,pad=PAD,h_agl=121.92,speed_mps=8,
        elev_mask=20,mode='science',roi_mode='body_yaw',science_stages=stages or phases(),max_wp=400)

class ScienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.result = build()

    def test_two_stationary_hovers_and_exactly_three_passes(self):
        p = self.result
        for phase in (0,1):
            h = [w for w in p['hovers'] if w['phase_id']==phase]
            # Arrive facing travel, turn and settle, then the 120 s observation.
            self.assertEqual([w['role'] for w in h[:2]], ['arrive', 'settle'])
            self.assertEqual(h[0]['loiter_s'], 0)
            self.assertGreaterEqual(h[1]['loiter_s'], 3)
            obs = h[2:]
            self.assertEqual(sum(w['loiter_s'] for w in obs),120)
            self.assertEqual(len({(w['lat'],w['lon']) for w in h}),1)
            self.assertGreater(len({w['yaw_deg'] for w in obs}),1)
            self.assertAlmostEqual(obs[-1]['arrive_s']+obs[-1]['loiter_s']-obs[0]['arrive_s'],120)
            summary = p['meta']['survey']['phases'][phase]
            self.assertEqual(summary['record_from_wp'], summary['first_wp'] + 1)
            self.assertAlmostEqual(summary['start_s'], obs[0]['arrive_s'])
        self.assertEqual(len({w['pass_id'] for w in p['hovers'] if w['pass_id'] is not None}),3)
        self.assertEqual(p['meta']['survey']['phases'][0]['satellites'],['G09','E05','C12'])

    def test_export_keeps_heading_updates_speeds_and_all_holds(self):
        p=self.result
        text=qgc_wpl(p)
        items=parse_wpl(text)
        nav=[x for x in items if x['command']==16 and x['seq']>0]
        self.assertEqual(len(nav),len(p['hovers']))
        settle = sum(w['loiter_s'] for w in p['hovers'] if w.get('role') == 'settle')
        self.assertEqual(sum(x['p1'] for x in nav),240 + settle)
        self.assertTrue(any(x['command']==178 and x['p2']==3 for x in items))
        # Passes and the hops between them stay at pass speed. The return is RTL, which flies
        # at the aircraft's own return speed, so there is no speed change back to transit.
        self.assertEqual([x['p2'] for x in items if x['command']==178], [8, 3])
        self.assertEqual((items[-2]['command'], items[-1]['command']), (16, 20))   # last pass point, then RTL
        self.assertEqual(items[-1]['command'],20)
        # ArduPilot starts the commands between two NAVs when the first one finishes (checked in
        # SITL), so a heading or speed sits right before the NAV it is for.
        prev_yaw = None
        for idx, item in enumerate(items):
            if item['command'] == 115:
                self.assertIn(items[idx+1]['command'],(178,16))
            if item['command'] == 178 and idx > 3:
                self.assertIn(items[idx+1]['command'],(115,16))
                # An unchanged heading goes first: ArduPilot runs one command at a time, and a
                # heading started after the speed change began from the look-ahead heading.
                nxt = items[idx+1]
                if nxt['command'] == 115 and prev_yaw is not None:
                    self.assertGreater(abs((nxt['p1'] - prev_yaw + 180) % 360 - 180), 20)
            if item['command'] == 115:
                prev_yaw = item['p1']
        pass_starts = [i for i, x in enumerate(items) if x['command'] == 178 and x['p2'] == 3]
        self.assertTrue(pass_starts)
        for i in pass_starts:
            self.assertEqual((items[i-1]['command'], items[i+1]['command']), (115, 16))
        self.assertEqual(len(plan_route_line(p)),len(p['hovers'])+2)
        check=validate_wpl(text,p)
        self.assertTrue(check['ok'],check)
        self.assertFalse(any('fly-to' in w for w in check['warnings']))
        self.assertIn('Group: hover 120 s',run_card(p))
        self.assertIn('Shore: 3 passes at 3 m/s',run_card(p))

    def test_saved_route_retains_hold_headings_and_pass_speeds(self):
        p = self.result
        saved = annotate_wpl(qgc_wpl(p), START, p)
        route = route_from_wpl(saved, keep_holds=True)
        self.assertEqual(len(route['waypoints']), len(p['hovers']))
        for original, loaded in zip(p['hovers'], route['waypoints']):
            self.assertAlmostEqual(loaded['speed_mps'], original['speed_mps'])
            self.assertAlmostEqual(loaded['yaw_deg'], original['yaw_deg'], places=5)
            self.assertEqual(loaded['loiter_s'], original['loiter_s'])
        self.assertEqual(sum(w['loiter_s'] for w in route['waypoints']), sum(w['loiter_s'] for w in p['hovers']))

    def test_payload_science_window_excludes_return_leg(self):
        from engine.payload import survey_waypoints
        from engine.flight import FlightService
        service = FlightService({'demo'})
        pid = service.register_plan(qgc_wpl(self.result), self.result['meta'])
        selected = survey_waypoints(service, pid)
        nav = [x for x in parse_wpl(qgc_wpl(self.result)) if x['command']==16 and x['seq']>0]
        self.assertEqual(selected['start_seq'], nav[0]['seq'])
        self.assertEqual(selected['stop_seq'], nav[-2]['seq'])

    def test_api_registers_complete_science_export(self):
        import app
        from unittest.mock import patch
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'OUTPUT', Path(tmp)), patch.object(app, 'sats', return_value=SATS), patch.object(app, 'lake_for', return_value=WATER):
            response=app.app.test_client().post('/api/plan', json=dict(mode='science', science_stages=phases(),
                start=START.isoformat(), pad=PAD, h_agl=121.92, speed_mps=8, elev_mask=20,
                roi_mode='body_yaw', max_wp=400))
            self.assertEqual(response.status_code,200,response.get_json())
            data=response.get_json()
            self.assertTrue(data['exports']['mission']['ok'])
            self.assertEqual(data['meta']['survey']['n_passes'],3)
            self.assertIn('Group',run_card(data))

    def test_timing_includes_previous_phases_and_per_leg_speed(self):
        p=self.result
        for a,b in zip(p['hovers'],p['hovers'][1:]):
            expected=a['arrive_s']+a['loiter_s']+haversine_m(a['lat'],a['lon'],b['lat'],b['lon'])/b['speed_mps']
            self.assertAlmostEqual(b['arrive_s'],expected,places=5)
            self.assertAlmostEqual(b['arrive_s'],b['geometry_s'],places=4)
        for fr in p['frames']:
            if fr['measuring'] and fr['phase'].startswith('to_'):
                self.assertEqual(fr['drone']['speed_mps'],3)
        surfaces=p['meta']['survey']['phases'][2]['reflection_surfaces']
        self.assertTrue(all(v==['land','water'] for v in surfaces.values()),surfaces)

    def test_order_count_duration_and_primary_are_configurable(self):
        stages=phases()
        stages[0]['duration_s']=25
        stages[0]['satellites']=['E05','G09']
        stages[2]['count']=2
        stages=[stages[2],stages[0]]
        p=build(stages)
        self.assertEqual(p['meta']['survey']['n_passes'],2)
        self.assertEqual(p['meta']['survey']['phases'][1]['primary'],'E05')
        self.assertEqual(sum(w['loiter_s'] for w in p['hovers'] if w.get('role') == 'sample' and w['pass_id'] is None),25)

    def test_missing_hidden_and_invalid_targets_fail_without_substitution(self):
        stages=phases(); stages[0]['satellites']=['G31']
        with self.assertRaisesRegex(ValueError,'not in the enabled'): build(stages)
        with self.assertRaisesRegex(ValueError,'below the elevation mask'): build(sats=[satellite('G09',45,5),*SATS[1:]])
        stages=phases(); stages[2]['points'][1]['lat']=39.999
        with self.assertRaisesRegex(ValueError,'one endpoint over water'): build(stages)
        stages=phases(); stages[2]['count']=2.5
        with self.assertRaisesRegex(ValueError,'whole number'): normalize_stages(stages)
        stages=phases(); stages[0]['duration_s']=float('nan')
        with self.assertRaisesRegex(ValueError,'Hover duration'): normalize_stages(stages)
        with self.assertRaisesRegex(ValueError,'between 1 and 20'): normalize_stages([])

if __name__=='__main__': unittest.main()
