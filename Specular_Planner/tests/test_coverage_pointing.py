import copy
import unittest
from shapely.geometry import Point, shape
from engine.coverage import swept_coverage
from engine.export import qgc_wpl, parse_wpl
from engine.flight import flight_items, compare_items, FlightError

def spec(lon, wet=True):
    return dict(prn='G14',lat=40,lon=lon,az=0,el=45,
                fresnel_along_m=5,fresnel_across_m=5,on_water=wet,aimed=True)

class CoveragePointingTests(unittest.TestCase):
    def test_separate_water_runs_do_not_fill_the_gap(self):
        frames=[{'speculars':[spec(-105)]},{'speculars':[spec(-104.995,False)]},
                {'speculars':[spec(-104.99)]}]
        g=shape(swept_coverage(frames)['lake']['G14'])
        self.assertFalse(g.covers(Point(-104.995,40)))

    def test_missing_satellite_breaks_track(self):
        frames=[{'speculars':[spec(-105)]},{'speculars':[]},
                {'speculars':[spec(-104.99)]}]
        g=shape(swept_coverage(frames)['lake']['G14'])
        self.assertFalse(g.covers(Point(-104.995,40)))

    def test_track_is_cut_at_the_shoreline(self):
        # Water ends at lon -104.995; the dry sample sits beyond it.
        water={'type':'FeatureCollection','features':[{'type':'Feature','properties':{},
               'geometry':{'type':'Polygon','coordinates':[[[-105.01,39.99],[-104.995,39.99],
               [-104.995,40.01],[-105.01,40.01],[-105.01,39.99]]]}}]}
        frames=[{'speculars':[spec(-105)]},{'speculars':[spec(-104.99,False)]}]
        cov=swept_coverage(frames,water=water)
        wet=shape(cov['lake']['G14'])
        dry=shape(cov['land']['G14'])
        self.assertTrue(wet.covers(Point(-104.9952,40)))
        self.assertTrue(dry.covers(Point(-104.9948,40)))
        self.assertFalse(wet.covers(Point(-104.993,40)))

    def test_body_yaw_is_paired_with_nav_and_verified_on_readback(self):
        plan={'meta':{'h_agl':120,'roi_mode':'body_yaw'},'pad':{'lat':40,'lon':-105},
              'hovers':[dict(lat=40.001,lon=-105,loiter_s=0,yaw_deg=55),
                        dict(lat=40.002,lon=-105,loiter_s=0,yaw_deg=56)]}
        items=flight_items(qgc_wpl(plan))
        for i,item in enumerate(items):
            if item['command']==115:
                self.assertEqual(items[i-1]['command'],16)
                self.assertEqual(item['p4'],0)
        self.assertEqual(sum(i['command']==115 for i in items),2)
        rois=[i for i in items if i['command']==201]
        self.assertEqual(len(rois),1)
        self.assertEqual((rois[0]['lat'],rois[0]['lon']),(0,0))
        compare_items(items,copy.deepcopy(items))
        bad=copy.deepcopy(items)
        next(i for i in bad if i['command']==115)['p1']+=10
        with self.assertRaises(FlightError):compare_items(items,bad)

    def test_roi_follows_its_navigation_command(self):
        plan={'meta':{'h_agl':120,'roi_mode':'per_point'},'pad':{'lat':40,'lon':-105},
              'hovers':[dict(lat=40.001,lon=-105,loiter_s=0,splash_lat=40.002,splash_lon=-105)]}
        items=parse_wpl(qgc_wpl(plan))
        roi=next(i for i,x in enumerate(items) if x['command']==201)
        self.assertEqual(items[roi-1]['command'],16)

if __name__=='__main__':unittest.main()
