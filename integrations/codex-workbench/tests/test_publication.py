import unittest
from check_publishable import inspect_files


class PublicationChecks(unittest.TestCase):
    def test_source_uses_runtime_resolution_without_user_data(self):
        self.assertEqual(inspect_files({'client.py':b'home = Path.home()\nkey = secrets.token_urlsafe(32)\n'}),[])

    def test_runtime_artifacts_are_rejected_even_if_forced_into_git(self):
        found=inspect_files({'state/token.txt':b'generated','tests/example-result.json':b'{}'})
        self.assertEqual(sum(item['kind']=='runtime_or_private_artifact' for item in found),2)

    def test_secret_match_is_redacted(self):
        value='gh'+'p_'+'x'*36
        found=inspect_files({'bad.py':value.encode()})
        self.assertTrue(any(item['kind']=='credential_value' for item in found))
        self.assertNotIn(value,str(found))

    def test_home_path_is_rejected(self):
        value='C:/'+ 'Users/'+'sample/file'
        self.assertTrue(any(item['kind']=='literal_user_home' for item in inspect_files({'bad.py':value.encode()})))

    def test_saved_session_identifier_is_rejected(self):
        value='01abcdef'+'-0000-0000-0000-'+'0'*12
        self.assertTrue(any(item['kind']=='saved_conversation_id' for item in inspect_files({'bad.py':value.encode()})))


if __name__=='__main__':unittest.main()
