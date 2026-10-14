"""Two real natural browser contexts with frozen, atomic coordinator bridge evidence.

--validate-only checks already staged local material without SSH, Node or browsers.
Actual launch is explicit and reads the coordinator's common start.json timestamp.
The isolated loopback tunnel and remote bridge permissions are provisioned externally.
"""
import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import time
import uuid

STAGES = {'diagnostic2':2,'baseline10':10,'candidate10':10,'candidate50':50,'login180':180,'natural180-r1':180,'natural180-r2':180,'diagnostic180':180}
REQUIRED_TOOLS = frozenset(('launcher','node','chromium','chromium_headless_shell','browser_probe',
    'browser_network_witness','browser_storage_witness','capture_protocol_witness','package_lock',
    'playwright_test','playwright','playwright_core','playwright_core_bundle','playwright_browsers'))


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha256(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def finite(value):return type(value) in (int,float) and math.isfinite(value)


def resolve_config(config):
    value=dict(config)
    for key in ('evidence_dir','client_dir','node_executable','browsers_path'):
        value[key]=str(Path(config[key]).resolve())
    value['tool_files']={name:str(Path(path).resolve()) for name,path in config['tool_files'].items()}
    return value


def require_browser_stage(stage):
    if stage not in STAGES:raise ValueError('Only explicitly counted two-browser stages can launch')


def tool_hashes(config):
    tools=config['tool_files']
    if not REQUIRED_TOOLS.issubset(tools):raise ValueError('Required browser/tool hash closure is incomplete')
    if Path(tools['launcher']).resolve()!=Path(__file__).resolve():raise ValueError('Binding points to a different launcher')
    if Path(tools['node']).resolve()!=Path(config['node_executable']).resolve():raise ValueError('Binding points to a different Node executable')
    return {name:sha256(path) for name,path in tools.items()}


def validate_stage(config,stage,folder,start_at=None,*,require_start=True):
    require_browser_stage(stage)
    fixture=read(folder/'fixture.json');plan=read(folder/'plan.json')
    binding=read(folder/'fixture-binding.json')
    start=read(folder/'start.json') if require_start else None
    source=fixture.get('source_config',{})
    if any(not re.fullmatch('[a-f0-9]{64}',source.get(key,'')) for key in ('source_sha256','frontend_sha256','config_sha256')):
        raise ValueError('Source/frontend/config binding is incomplete')
    if (binding.get('fixture_sha256')!=sha256(folder/'fixture.json') or binding.get('plan_sha256')!=sha256(folder/'plan.json')
        or binding.get('source_config_sha256')!=canonical(source) or binding.get('browser_tool_files')!=tool_hashes(config)):
        raise ValueError('Frozen source/fixture/plan/browser tool binding differs')
    if require_start and (start.get('stage')!=stage or start.get('fixture_binding')!=binding or not finite(start.get('start_at'))
        or start['start_at']<=0 or (start_at is not None and start_at!=start['start_at'])):
        raise ValueError('Coordinator start marker, binding or shared timestamp differs')
    names=fixture.get('usernames',[]);browsers=fixture.get('browser_usernames',[]);protocol=fixture.get('protocol_usernames',[])
    if (fixture.get('database')!='kt_e2e' or fixture.get('student_count')!=STAGES[stage]
        or fixture.get('item_count')!=20 or len(names)!=STAGES[stage] or len(set(names))!=len(names)
        or len(browsers)!=2 or len(set(browsers))!=2 or len(protocol)!=len(names)-2
        or set(browsers)&set(protocol) or set(names)!=set(browsers)|set(protocol)
        or source.get('phase')!=('baseline' if stage=='baseline10' else 'candidate')):
        raise ValueError('Fresh stage requires exactly two browser accounts and a disjoint full protocol subset')
    if stage=='diagnostic180' and source.get('diagnostic_180_authorized') is not True:
        raise ValueError('Single 180 diagnostic must be explicitly authorized')
    students=plan.get('students',[])
    if stage=='login180' and (fixture.get('load_mode')!='login-only' or any(row.get('arrival_offset_seconds')!=0 for row in students)):
        raise ValueError('Login burst requires frozen zero-arrival login-only plan')
    if (plan.get('workload_version')!='natural-180-v1' or plan.get('scenario')!='confidence-only-v1'
        or fixture.get('scenario')!='confidence-only-v1' or plan.get('task_id')!=fixture['task_id']
        or plan.get('total_students')!=len(names) or plan.get('item_count')!=20
        or plan.get('seed')!=(20261014 if stage=='natural180-r2' else 20261013)
        or len(students)!=len(names) or [row.get('username') for row in students]!=names):
        raise ValueError('Frozen full natural plan or round seed differs')
    bounds={'fast':(35,55),'medium':(45,75),'slow':(65,95)}
    scale=plan.get('confidence_scale',{})
    for row in students:
        actions=row.get('confidence_actions',[]);values=row.get('confidence_values',[])
        dwell=row.get('item_dwell_seconds',[]);think=row.get('confidence_think_seconds',[])
        speed=bounds.get(row.get('speed_stratum'))
        if (row.get('role')!=('browser' if row['username'] in browsers else 'protocol') or not speed
            or not finite(row.get('arrival_offset_seconds')) or not 0<=row['arrival_offset_seconds']<=60
            or len(dwell)!=20 or any(not finite(v) or not speed[0]<=v<=speed[1] for v in dwell)
            or len(actions)!=20 or actions.count('fill')!=16 or actions.count('skip')!=4
            or len(values)!=20 or len(think)!=20 or any(not finite(v) or not 1<=v<=3 for v in think)
            or row.get('optional_tool_items')!=[]):raise ValueError('Natural role, arrival, dwell or confidence-only contract differs')
        for action,value in zip(actions,values):
            if action=='skip' and value is None:continue
            if (action!='fill' or type(value) is not int or not scale.get('step')
                or not scale.get('minimum',math.inf)<=value<=scale.get('maximum',-math.inf)
                or (value-scale['minimum'])%scale['step']):raise ValueError('Confidence decision/value differs from frozen scale')
    return {'fixture':fixture,'plan':plan,'binding':binding,'start_at':float(start['start_at']) if require_start else None}


def environment(config):
    value=os.environ.copy()
    value['PATH']=str(Path(config['node_executable']).parent)+os.pathsep+value.get('PATH','')
    value['PLAYWRIGHT_BROWSERS_PATH']=config['browsers_path']
    value.pop('PWDEBUG',None)
    return value


def browser_command(config,stage,folder,start_at,*,armed_start=False):
    require_browser_stage(stage)
    folder=Path(folder).resolve()
    timing=(['--armed-file',str(folder/'browser.armed.json'),'--start-file',str(folder/'browser.start.json')]
        if armed_start else ['--start-at',str(float(start_at))])
    return [str(Path(config['node_executable']).resolve()),str(Path(config['tool_files']['browser_probe']).resolve()),'--base-url','http://127.0.0.1:18080',
        '--manifest',str(folder/'fixture.json'),'--plan',str(folder/'plan.json'),'--output',str(folder/'browser.json'),
        '--phase','load','--mode','login-only' if stage=='login180' else 'natural','--items','20',
        '--stop-file',str(folder/'browser.stop'),'--visual-output',str(folder/'visual'),*timing]


def remote(config,stage,name):
    host=config['ssh_host'];root=config['remote_bridge_dir']
    if not re.fullmatch('[A-Za-z0-9._-]+',host) or not re.fullmatch('/[A-Za-z0-9._/-]+',root):
        raise ValueError('Simple SSH alias and absolute bridge path required')
    if '..' in PurePosixPath(root).parts:raise ValueError('Bridge must not contain parent traversal')
    return host,str(PurePosixPath(root)/stage/name)


def publish(config,stage,path,name,*,replace=False):
    host,target=remote(config,stage,name)
    subprocess.run(['scp',str(path),host+':'+target+'.incoming'],check=True,timeout=30,
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    command=('' if replace else 'test ! -e '+shlex.quote(target)+' && ')
    command+='mv '+shlex.quote(target+'.incoming')+' '+shlex.quote(target)
    subprocess.run(['ssh',host,command],check=True,timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)


def read_shared_progress(path):
    if os.name!='nt':return Path(path).read_bytes()
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.ReadFile.argtypes=[wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateFileW(str(path),0x80000000,7,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    chunks=[]
    try:
        while True:
            buffer=ctypes.create_string_buffer(4096);length=wintypes.DWORD()
            if not kernel.ReadFile(handle,buffer,4096,ctypes.byref(length),None):raise ctypes.WinError(ctypes.get_last_error())
            if not length.value:return b''.join(chunks)
            chunks.append(buffer.raw[:length.value])
    finally:kernel.CloseHandle(handle)


def host_memory_sample():
    if os.name!='nt':return {'at':time.time(),'host_memory':'not_windows'}
    class MemoryStatus(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load_percent',ctypes.c_ulong),*[(name,ctypes.c_ulonglong) for name in
            ('total_physical','available_physical','total_pagefile','available_pagefile','total_virtual','available_virtual','available_extended')]]
    value=MemoryStatus();value.length=ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):raise ctypes.WinError()
    return {'at':time.time(),'host_total_physical_bytes':value.total_physical,
        'host_available_physical_bytes':value.available_physical,'host_memory_load_percent':value.load_percent}


def verify_browser_selection(config):
    script="""const {chromium}=require('@playwright/test');const {registry}=require('playwright-core/lib/coreBundle');
process.stdout.write(JSON.stringify({chromium:chromium.executablePath(),chromium_headless_shell:registry.registry.findExecutable('chromium-headless-shell').executablePath()}));"""
    result=subprocess.run([config['node_executable'],'-e',script],cwd=config['client_dir'],env=environment(config),
        text=True,capture_output=True,timeout=20,check=True)
    paths=json.loads(result.stdout)
    if any(Path(paths[key]).resolve()!=Path(config['tool_files'][key]).resolve() for key in ('chromium','chromium_headless_shell')):
        raise ValueError('Playwright selects different Chromium binaries from frozen tool binding')


def clock_calibration(config):
    host=config['ssh_host']
    if not re.fullmatch('[A-Za-z0-9._-]+',host):raise ValueError('Simple SSH alias required')
    samples=[]
    for _ in range(3):
        t0=time.time();m0=time.monotonic()
        result=subprocess.run(['ssh',host,'python3 -c '+shlex.quote('import time; print(time.time())')],
            text=True,capture_output=True,check=True,timeout=10)
        t1=time.time();m1=time.monotonic();server=float(result.stdout.strip())
        if not all(finite(value) for value in (t0,t1,server)) or t1<t0 or abs((t1-t0)-(m1-m0))>.01:
            raise ValueError('Invalid or discontinuous calibration clocks')
        samples.append({'sample_id':uuid.uuid4().hex,'browser_t0':t0,'browser_t1':t1,
            'server_epoch':server,'roundtrip_seconds':t1-t0})
    selected=min(samples,key=lambda row:row['roundtrip_seconds'])
    return {'selected_sample_id':selected['sample_id'],'samples':samples,
        'server_minus_browser_lower_seconds':selected['server_epoch']-selected['browser_t1'],
        'server_minus_browser_upper_seconds':selected['server_epoch']-selected['browser_t0'],
        'boundary':'Server epoch sampled inside browser host request/response bracket; shortest of three RTTs',
        'clock_assumption':'No wall-clock step between calibration and observed browser operations'}


def wait_for_browser_start(config,stage,folder,validated,process,start_at=None,timeout=180):
    deadline=time.monotonic()+min(timeout,180)
    armed_path=folder/'browser.armed.json'
    while not armed_path.exists():
        if process.poll() is not None:raise RuntimeError('Browser exited before readiness')
        if time.monotonic()>=deadline:raise TimeoutError('Two-context browser readiness timed out')
        if (folder/'browser.stop').exists():raise RuntimeError('Browser stopped before readiness')
        time.sleep(.1)
    marker=read(armed_path);binding=validated['binding']
    if (process.poll() is not None or marker.get('pid')!=process.pid or marker.get('probe_count')!=2
        or marker.get('fixture_sha256')!=binding['fixture_sha256'] or marker.get('plan_sha256')!=binding['plan_sha256']):
        raise ValueError('Browser readiness fixture, plan, process or two-context binding differs')
    armed={**marker,'stage':stage,'fixture_binding':binding}
    armed_bridge=folder/'browser-armed.json'
    with armed_bridge.open('x',encoding='utf-8') as stream:json.dump(armed,stream)
    publish(config,stage,armed_bridge,'browser-armed.json')
    host,start_remote=remote(config,stage,'start.json')
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('Browser exited before start release')
        host,stop=remote(config,stage,'browser.stop')
        stopped=subprocess.run(['ssh',host,'test -f '+shlex.quote(stop)],timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if stopped.returncode==0:raise RuntimeError('Browser stopped before start release')
        if stopped.returncode!=1:raise RuntimeError('Remote stop-file observation failed')
        available=subprocess.run(['ssh',host,'test -f '+shlex.quote(start_remote)],timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if available.returncode==0:
            subprocess.run(['scp',host+':'+start_remote,str(folder/'start.json')],check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            released=validate_stage(config,stage,folder,start_at)
            if released['binding']!=binding:raise ValueError('Coordinator changed binding after browser readiness')
            return released
        if available.returncode!=1:raise RuntimeError('Remote start-file observation failed')
        time.sleep(.5)
    raise TimeoutError('Common start release timed out')


def launch(config,stage,folder,start_at=None,*,armed_start=False):
    require_browser_stage(stage)
    config=resolve_config(config);folder=Path(folder).resolve()
    folder.mkdir(parents=True)
    process=None;calibration=None;validated=None;released=False
    try:
        for name in ('fixture.json','plan.json','fixture-binding.json')+(() if armed_start else ('start.json',)):
            host,target=remote(config,stage,name)
            subprocess.run(['scp',host+':'+target,str(folder/name)],check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        validated=validate_stage(config,stage,folder,start_at,require_start=not armed_start)
        verify_browser_selection(config)
        if not armed_start:
            calibration=clock_calibration(config)
            with (folder/'browser-clock-calibration.json').open('x',encoding='utf-8') as stream:json.dump(calibration,stream)
            if validated['start_at']<time.time()+2:raise ValueError('Shared stage start is already too late for coordinated browser launch')
        with (folder/'browser-binding.json').open('x',encoding='utf-8') as stream:json.dump(validated['binding'],stream)
        publish(config,stage,folder/'browser-binding.json','browser-binding.json')
        with (folder/'browser.log').open('x',encoding='utf-8') as log:
            process=subprocess.Popen(browser_command(config,stage,folder,validated['start_at'],armed_start=armed_start),cwd=config['client_dir'],
                env=environment(config),stdout=log,stderr=subprocess.STDOUT)
            if armed_start:
                validated=wait_for_browser_start(config,stage,folder,validated,process,start_at)
                calibration=clock_calibration(config)
                with (folder/'browser-clock-calibration.json').open('x',encoding='utf-8') as stream:json.dump(calibration,stream)
                if process.poll() is not None:raise RuntimeError('Browser exited before calibrated start release')
                if validated['start_at']<time.time()+2:raise ValueError('Shared stage start is already too late for coordinated browser launch')
                local_start={'start_at':validated['start_at'],'fixture_sha256':validated['binding']['fixture_sha256'],
                    'plan_sha256':validated['binding']['plan_sha256']}
                with (folder/'browser.start.json.tmp').open('x',encoding='utf-8') as stream:json.dump(local_start,stream)
                (folder/'browser.start.json.tmp').rename(folder/'browser.start.json')
            released=True
            while process.poll() is None:
                with (folder/'generator-host.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(host_memory_sample())+'\n')
                progress=folder/'browser.progress.json'
                if progress.exists():
                    transfer=folder/'browser.progress.transfer.json';transfer.write_bytes(read_shared_progress(progress))
                    json.loads(transfer.read_text(encoding='utf-8'))
                    publish(config,stage,transfer,'browser.progress.json',replace=True)
                host,stop=remote(config,stage,'browser.stop')
                stopped=subprocess.run(['ssh',host,'test -f '+shlex.quote(stop)],timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                if stopped.returncode==0:(folder/'browser.stop').touch()
                elif stopped.returncode!=1:raise RuntimeError('Remote stop-file observation failed')
                if time.time()>validated['start_at']+(300 if stage=='login180' else 2700):
                    (folder/'browser.stop').touch()
                    raise TimeoutError('Browser workload exceeded independent 2700-second budget')
                time.sleep(2)
    except BaseException as error:
        (folder/'browser.stop').touch()
        if process is not None and process.poll() is None:
            try:process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        if armed_start and not released:
            error_path=folder/'browser-launch-error.json'
            reason=('readiness_or_release_timeout' if isinstance(error,TimeoutError) else
                'binding_or_start_validation_failed' if isinstance(error,ValueError) else
                'transport_or_process_failed')
            with error_path.open('x',encoding='utf-8') as stream:json.dump({'stage':stage,'type':type(error).__name__,'reason':reason,'released':False},stream)
            publish(config,stage,error_path,'browser-launch-error.json')
        raise
    finally:
        for name in ('browser.progress.json','browser.json'):
            if (folder/name).exists():
                path=folder/name
                if name=='browser.json' and calibration is not None:
                    bound={**read(path),'clock_calibration':calibration,'original_browser_report_sha256':sha256(path)}
                    path=folder/'browser.clock-bound.json'
                    with path.open('x',encoding='utf-8') as stream:json.dump(bound,stream)
                publish(config,stage,path,name,replace=name=='browser.progress.json')
    return {'stage':stage,'exit_code':process.returncode,'output_exists':(folder/'browser.json').exists(),
        'counted_browser_students':2,'start_at':validated['start_at']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=tuple(STAGES))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--start-at',type=float)
    parser.add_argument('--validate-only',action='store_true')
    parser.add_argument('--print-tool-hashes',action='store_true')
    parser.add_argument('--armed-start',action='store_true')
    args=parser.parse_args(argv);config=resolve_config(read(args.config))
    if args.print_tool_hashes:
        print(json.dumps(tool_hashes(config),indent=2));return 0
    folder=Path(config['evidence_dir'])/args.stage
    if args.validate_only:
        validated=validate_stage(config,args.stage,folder,args.start_at,require_start=not args.armed_start)
        print(json.dumps({'stage':args.stage,'validated':True,'counted_browser_students':2,'start_at':validated['start_at'],'browser_launched':False}));return 0
    result=launch(config,args.stage,folder,args.start_at,armed_start=args.armed_start);print(json.dumps(result));return result['exit_code']


if __name__=='__main__':raise SystemExit(main())
