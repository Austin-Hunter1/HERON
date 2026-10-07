import math
import unittest
from shapely.geometry import Polygon, box, Point
from shapely.affinity import rotate, affine_transform
from engine.survey import sweep_rows, build_survey
from engine.export import qgc_wpl, parse_wpl

def geographic(g):
    return affine_transform(g, [1/(111132.92*math.cos(math.radians(40))),0,0,1/111132.92,-105,40])

class SweepTests(unittest.TestCase):
    def test_rotated_long_axis_and_spacing(self):
        poly=geographic(rotate(box(-300,-50,300,50),35,origin=(0,0)))
        rows,bearing,pitch=sweep_rows(poly,20,None,500)
        self.assertAlmostEqual(bearing,55,places=3)
        self.assertEqual(len(rows),5)
        self.assertLessEqual(pitch,20.001)
        self.assertEqual(sum(map(len,rows)),15)
        for i,row in enumerate(rows):
            self.assertEqual(row[-1][1]>row[0][1],i%2==0)
            for lat,lon in row:
                self.assertTrue(poly.buffer(1e-10).covers(Point(lon,lat)))

    def test_manual_cross_axis(self):
        poly=geographic(box(-300,-50,300,50))
        rows,bearing,_=sweep_rows(poly,20,0,500)
        self.assertEqual(bearing,0)
        self.assertEqual(len(rows),30)

    def test_hole_keeps_distinct_spans(self):
        poly=geographic(Polygon([(-100,-50),(100,-50),(100,50),(-100,50)],holes=[[(-20,-30),(-20,30),(20,30),(20,-30)]]))
        rows,_,_=sweep_rows(poly,20,90,500)
        self.assertGreater(len(rows),5)
        for row in rows:
            from shapely.geometry import LineString
            self.assertTrue(poly.buffer(1e-10).covers(LineString([(lon,lat) for lat,lon in row])))

    def test_continuous_no_hold_and_cap_does_not_thin(self):
        poly=geographic(box(-300,-50,300,50))
        kw=dict(h_agl=120,az=0,el=45,prn='G14',loiter_s=30,spacing_m=20,survey_style='continuous',max_leg_m=500)
        h,info=build_survey(poly,max_wp=100,**kw)
        self.assertEqual(len(h),15)
        self.assertTrue(all(w['loiter_s']==0 for w in h))
        self.assertEqual(info['n_passes'],5)
        with self.assertRaisesRegex(ValueError,'waypoint limit'):
            build_survey(poly,max_wp=10,**kw)

    def test_auto_lanes_follow_coverage(self):
        poly=geographic(box(-300,-50,300,50))
        kw=dict(h_agl=120,az=0,el=45,prn='G14',loiter_s=0,spacing_m=None,survey_style='continuous',max_leg_m=500)
        _,full=build_survey(poly,coverage_pct=100,**kw)
        _,half=build_survey(poly,coverage_pct=50,**kw)
        self.assertAlmostEqual(full['wanted_m'],full['footprint_m'],places=2)
        self.assertAlmostEqual(half['wanted_m'],2*full['footprint_m'],places=2)
        self.assertLessEqual(full['lane_spacing_m'],full['footprint_m']+1e-6)
        self.assertLess(half['n_passes'],full['n_passes'])

    def test_no_roi_export(self):
        p={'meta':{'h_agl':120,'roi_mode':'none'},'pad':{'lat':40,'lon':-105},'hovers':[{'lat':40.01,'lon':-105,'splash_lat':40.02,'splash_lon':-105,'loiter_s':0}]}
        items=parse_wpl(qgc_wpl(p))
        self.assertNotIn(201,[i['command'] for i in items])
        self.assertEqual(items[-2]['p1'],0)
        p['meta']['roi_mode']='per_point'
        self.assertEqual(sum(i['command']==201 for i in parse_wpl(qgc_wpl(p))),2)

if __name__=='__main__': unittest.main()
