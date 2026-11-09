import os
import subprocess
import sys
import unittest


class CLITests(unittest.TestCase):
    def test_help_and_missing_token(self):
        env = dict(os.environ)
        env.pop('READWISE_TOKEN', None)
        help_result = subprocess.run([sys.executable, '-m', 'reader_opds', '--help'], capture_output=True, text=True, env=env)
        self.assertEqual(help_result.returncode, 0)
        result = subprocess.run([sys.executable, '-m', 'reader_opds'], capture_output=True, text=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('READWISE_TOKEN', result.stderr)

    def test_fly_requires_authentication(self):
        env = dict(os.environ, READWISE_TOKEN='test', FLY_APP_NAME='test-app')
        env.pop('OPDS_USER', None)
        env.pop('OPDS_PASSWORD', None)
        result = subprocess.run([sys.executable, '-m', 'reader_opds'], capture_output=True, text=True, env=env, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OPDS_USER', result.stderr)


if __name__ == '__main__':
    unittest.main()
