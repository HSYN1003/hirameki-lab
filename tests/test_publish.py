import json,tempfile,unittest
from pathlib import Path
from fx_atlas.render import public_site
class PublishTests(unittest.TestCase):
 def test_preserves_original_index(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'index.html').write_text('original');(root/'fx-sentiment.html').write_text('new FX')
   public_site(root,root/'_site');self.assertEqual((root/'_site/index.html').read_text(),'original')
 def test_state_not_in_public_output(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'fx-sentiment-data').mkdir();(root/'fx-sentiment-data/state.json').write_text('{}');(root/'fx-sentiment-data/snapshot.json').write_text('{}')
   public_site(root,root/'_site');self.assertFalse((root/'_site/fx-sentiment-data/state.json').exists());self.assertTrue((root/'_site/fx-sentiment-data/snapshot.json').exists())
 def test_source_and_environment_not_exposed(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'.env').write_text('secret');(root/'private-raw').mkdir();(root/'private-raw/a.html').write_text('raw');(root/'tests').mkdir();(root/'tests/a.html').write_text('fixture')
   public_site(root,root/'_site');self.assertFalse((root/'_site/.env').exists());self.assertFalse((root/'_site/private-raw').exists());self.assertFalse((root/'_site/tests').exists())
 def test_does_not_follow_symlinks(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'.env').write_text('secret');(root/'leak.html').symlink_to(root/'.env')
   public_site(root,root/'_site');self.assertFalse((root/'_site/leak.html').exists())
