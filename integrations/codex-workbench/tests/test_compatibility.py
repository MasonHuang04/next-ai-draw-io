import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        spec=importlib.util.spec_from_file_location('test_compatibility_module',ROOT/'compatibility_bridge.py')
        cls.bridge=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'CODEX_BRIDGE_STATE':cls.temporary.name}):spec.loader.exec_module(cls.bridge)

    @classmethod
    def tearDownClass(cls):
        cls.bridge.EXECUTOR.shutdown(wait=True)
        cls.temporary.cleanup()

    def execute_mock(self,answer,body):
        def complete(command,**kwargs):
            Path(command[command.index('-o')+1]).write_text(json.dumps(answer),encoding='utf-8')
            self.assertIn('--ephemeral',command)
            self.assertEqual(command[command.index('-s')+1],'read-only')
            self.assertIn('shell_tool',command)
            return subprocess.CompletedProcess(command,0,'{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":3}}\n','')
        with patch.object(self.bridge,'codex_settings',return_value=(sys.executable,None)),patch.object(self.bridge.subprocess,'run',side_effect=complete),contextlib.redirect_stdout(io.StringIO()):
            return self.bridge.run_completion(body)

    def test_text_completion_does_not_require_a_committed_model_or_key(self):
        result=self.execute_mock({'text':'example','tool_calls':[]},{'model':'codex-current','messages':[{'role':'user','content':'example'}]})
        self.assertEqual(result['choices'][0]['message']['content'],'example')
        self.assertEqual(result['usage']['total_tokens'],5)

    def test_application_tool_call_is_returned_to_the_canvas(self):
        result=self.execute_mock({'text':'','tool_calls':[{'name':'draw_box','arguments':'{"label":"example"}'}]},
            {'messages':[],'tools':[{'type':'function','function':{'name':'draw_box','parameters':{'type':'object'}}}]})
        self.assertEqual(result['choices'][0]['finish_reason'],'tool_calls')
        self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['function']['name'],'draw_box')

    def test_unrequested_tool_is_rejected(self):
        with self.assertRaises(ValueError):
            self.execute_mock({'text':'','tool_calls':[{'name':'not_offered','arguments':'{}'}]},{'messages':[]})

    def test_model_list_requires_local_connection_key_and_origin(self):
        bridge=self.bridge
        server=bridge.ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url='http://127.0.0.1:'+str(server.server_port)+'/v1/models'
        try:
            with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                opener.open(url)
            self.assertEqual(unauthorized.exception.code,401)
            request=urllib.request.Request(url,headers={'Authorization':'Bearer '+bridge.TOKEN})
            with opener.open(request) as response:self.assertEqual(json.load(response)['data'][0]['id'],'codex-current')
            request=urllib.request.Request(url,headers={'Authorization':'Bearer '+bridge.TOKEN,'Origin':'https://example.invalid'})
            with self.assertRaises(urllib.error.HTTPError) as external:
                opener.open(request)
            self.assertEqual(external.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':unittest.main()
