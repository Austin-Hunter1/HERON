import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
class WriterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++'),'C++ compiler required')
    def test_segmentation_sc8_scaling_clipping_and_short_receive_buffers(self):
        source=(Path(__file__).resolve().parents[1]/'recorder/main.cpp').read_text()
        source=source[:source.index('\nint main(')]
        source='\n'.join(line for line in source.splitlines() if not line.startswith('#include <uhd/') and not line.startswith('#include <boost/') and line!='namespace pt=boost::property_tree;')
        source+='''
int main(int argc,char** argv){
 fs::create_directories(argv[1]);
 Writer w(argv[1],true,4,1,2,0.25);
 Frame a{{{{-32768,32767},{256,-256},{0,0}},{{512,768},{1024,1280},{1536,1792}}},10.0,0};
 Frame b{{{{2048,2304},{2560,2816}},{{3072,3328},{3584,3840}}},10.75,0};
 w.push(std::move(a));w.push(std::move(b));w.finish();
 return (w.written==20&&w.clipped==2)?0:2;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'writer.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-pthread',str(root/'writer.cpp'),'-o',str(root/'writer')],check=True,capture_output=True)
            subprocess.run([str(root/'writer'),str(root/'out')],check=True)
            self.assertEqual((root/'out/channel_0_000000.sc8').read_bytes(),bytes([128,127,4,252,0,0,32,36]))
            self.assertEqual((root/'out/channel_0_000001.sc8').read_bytes(),bytes([40,44]))
            rows=[json.loads(line) for line in (root/'out/samples.jsonl').read_text().splitlines()]
            self.assertEqual([r['count'] for r in rows],[3,1,1])
            self.assertEqual([r['sample_offset'] for r in rows],[0,3,4])
            self.assertEqual(rows[-1]['device_time'],11.0)
