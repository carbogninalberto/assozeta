"""Small independent JSON-lines client for owned subprocess protocol tests."""
import json
import os
import selectors
import subprocess
import time


class StdioClient:
    def __init__(self,command,env,stderr,cwd):
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,env=env,cwd=cwd,bufsize=0)
        self.selector=selectors.DefaultSelector();self.selector.register(self.process.stdout,selectors.EVENT_READ)
        self.buffer=b''

    def send(self,method,params=None,identifier=None):
        value={'jsonrpc':'2.0','method':method}
        if params is not None:value['params']=params
        if identifier is not None:value['id']=identifier
        self.raw(json.dumps(value,ensure_ascii=False).encode()+b'\n')

    def raw(self,data):
        self.process.stdin.write(data);self.process.stdin.flush()

    def receive(self,timeout=20):
        deadline=time.monotonic()+timeout
        while b'\n' not in self.buffer:
            remaining=deadline-time.monotonic()
            if remaining<=0 or not self.selector.select(remaining):raise TimeoutError('MCP response deadline exceeded')
            data=os.read(self.process.stdout.fileno(),65536)
            if not data:raise RuntimeError('MCP process closed stdout before responding')
            self.buffer+=data
            if len(self.buffer)>8*1024*1024:raise RuntimeError('MCP response exceeded fixture bound')
        line,self.buffer=self.buffer.split(b'\n',1)
        return json.loads(line)

    def call(self,method,params,identifier):
        self.send(method,params,identifier)
        result=self.receive()
        assert result.get('id')==identifier,'Unexpected response or non-protocol stdout'
        return result

    def close(self):
        running=self.process.poll() is None
        unexpected=bool(self.buffer)
        deadline=time.monotonic()+10
        try:
            if self.process.stdin and not self.process.stdin.closed:
                try:self.process.stdin.close()
                except BrokenPipeError:pass
            # Drain while waiting so trailing output cannot be discarded or
            # block a child whose stdout pipe fills during shutdown.
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0 or not self.selector.select(remaining):
                    raise RuntimeError('MCP process did not exit cleanly after EOF')
                data=os.read(self.process.stdout.fileno(),65536)
                if not data:break
                unexpected=True
            try:code=self.process.wait(timeout=max(0,deadline-time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise RuntimeError('MCP process did not exit cleanly after EOF') from error
            if not running:raise RuntimeError('MCP process exited before EOF')
            if unexpected:raise RuntimeError('Unexpected MCP stdout after final response')
            return code
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
            self.selector.close();self.process.stdout.close()
