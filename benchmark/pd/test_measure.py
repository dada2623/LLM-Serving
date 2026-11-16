"""Check SSE counting and timing before placing load on either GPU."""
import gzip
import json
from pathlib import Path
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import measure

class Response:
    status=200
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def __iter__(self):
        for count,text,finish in [(0,'',None),(1,'',None),(2,'hello',None),(4,'hello world',{'type':'length','length':4})]:
            time.sleep(.002)
            yield ('data: '+json.dumps({'text':text,'meta_info':{'prompt_tokens':128,
                    'completion_tokens':count,'finish_reason':finish}})+'\n').encode()
        yield b'data: [DONE]\n'

class MeasurementTest(unittest.TestCase):
    def test_empty_text_does_not_start_visible_ttft_and_counts_are_cumulative(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'request.jsonl.gz'
            with patch.object(measure.opener,'open',return_value=Response()):
                row=measure.consume('127.0.0.1',{},dest,measure.clock(),0)
            self.assertEqual(row['completion_tokens'],4)
            self.assertTrue(row['done_marker'])
            self.assertLess(row['first_token_s'],row['first_content_s'])
            self.assertGreater(row['tpot_s'],0)
            events=[json.loads(x) for x in gzip.open(dest,'rt')]
            self.assertEqual(len(events),5)
            self.assertEqual(events[-1]['result']['text'],'hello world')

    def test_transport_error_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(measure.opener,'open',side_effect=TimeoutError('probe timeout')):
                row=measure.consume('127.0.0.1',{},Path(directory)/'error.jsonl.gz',measure.clock(),0)
            self.assertIn('probe timeout',row['error'])
            self.assertIsNone(row['ttft_s'])
            self.assertEqual(row['completion_tokens'],0)

    def test_drain_deadline_interrupts_an_open_stream(self):
        released=threading.Event()
        class IdleResponse(Response):
            fp=SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(shutdown=lambda *_:released.set())))
            def __iter__(self):
                released.wait(timeout=3)
                return iter([])
        cancel=threading.Event()
        timer=threading.Timer(.05,cancel.set);timer.start()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(measure.opener,'open',return_value=IdleResponse()):
                row=measure.consume('127.0.0.1',{},Path(directory)/'drain.jsonl.gz',measure.clock(),0,cancel=cancel)
        timer.join()
        self.assertTrue(released.is_set())
        self.assertIn('Drain deadline exceeded',row['error'])

if __name__=='__main__': unittest.main()
