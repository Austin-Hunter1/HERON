import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

# Both write modes share SegmentFiles; the threaded mode wraps it in Writer (D-078).
HARNESS = '''
int main(int argc,char** argv){
 fs::create_directories(argv[1]);
 auto c8=[](std::initializer_list<int> v){std::vector<char> b;for(int x:v)b.push_back(char(int8_t(x)));return b;};
 Frame a{{c8({-128,127,4,-4,0,0}),c8({2,3,4,5,6,7})},3,10.0,0};
 Frame b{{c8({8,9,10,11}),c8({12,13,14,15})},2,10.75,0};
 uint64_t written=0,clipped=0;
 if(std::string(argv[2])=="threaded"){
  Writer w(argv[1],true,4,1,2,2,0);
  w.push(std::move(a));w.push(std::move(b));w.finish();
  written=w.files.written;clipped=w.files.clipped;
 }else{
  SegmentFiles f(argv[1],true,4,1,2,4096);
  f.write({a.channels[0].data(),a.channels[1].data()},a.count,a.time,a.error);
  f.write({b.channels[0].data(),b.channels[1].data()},b.count,b.time,b.error);
  f.finish();written=f.written;clipped=f.clipped;
 }
 return (written==20&&clipped==2)?0:2;
}
'''


class WriterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler required')
    def test_segmentation_clipping_and_short_buffers_in_both_write_modes(self):
        source = (Path(__file__).resolve().parents[1] / 'recorder/main.cpp').read_text()
        source = source[:source.index('\nint main(')]
        source = '\n'.join(line for line in source.splitlines() if not line.startswith('#include <uhd/')
                           and not line.startswith('#include <boost/') and line != 'namespace pt=boost::property_tree;')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'writer.cpp').write_text(source + HARNESS)
            subprocess.run(['c++', '-std=c++17', '-pthread', str(root / 'writer.cpp'), '-o', str(root / 'writer')],
                           check=True, capture_output=True)
            for mode in ('direct', 'threaded'):
                with self.subTest(mode=mode):
                    out = root / mode
                    subprocess.run([str(root / 'writer'), str(out), mode], check=True)
                    # UHD already scaled to int8 (sc8 wire): the bytes go to disk unchanged.
                    self.assertEqual((out / 'channel_0_000000.sc8').read_bytes(), bytes([128, 127, 4, 252, 0, 0, 8, 9]))
                    self.assertEqual((out / 'channel_0_000001.sc8').read_bytes(), bytes([10, 11]))
                    rows = [json.loads(line) for line in (out / 'samples.jsonl').read_text().splitlines()]
                    self.assertEqual([r['count'] for r in rows], [3, 1, 1])
                    self.assertEqual([r['sample_offset'] for r in rows], [0, 3, 4])
                    self.assertEqual(rows[-1]['device_time'], 11.0)
