"""Offline bridge contract tests; no SSH, browser or school HTTP."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import shutil
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'server/scripts/load'))
from natural_workload import build_workload
PATH=Path(__file__).with_name('school_browser_launcher.py')
launcher=None
if PATH.exists():
    spec=importlib.util.spec_from_file_location('transition_browser_launcher',PATH)
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class LauncherTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(launcher,'The transition browser adapter is not implemented')

    def fixture(self,root,stage='candidate10'):
        count={'diagnostic2':2,'candidate10':10,'baseline10':10,'candidate50':50,'login180':180,'natural180-r1':180,'natural180-r2':180,'diagnostic180':180}[stage]
        folder=root/stage;folder.mkdir()
        client=root/'client';client.mkdir(exist_ok=True)
        tools={key:str(root/(key+'.bin')) for key in launcher.REQUIRED_TOOLS}
        tools['launcher']=str(PATH)
        for key,path in tools.items():
            if key!='launcher':Path(path).write_bytes(('synthetic-'+key).encode())
        source={'source_sha256':'a'*64,'frontend_sha256':'b'*64,'config_sha256':'c'*64,
            'phase':'baseline' if stage=='baseline10' else 'candidate','baseline_commit':'e64535f'+'0'*33}
        if stage=='diagnostic180':source['diagnostic_180_authorized']=True
        names=[f'account-{i}' for i in range(count)]
        fixture={'source_config':source,'student_count':count,'item_count':20,'task_id':123,
            'usernames':names,'browser_usernames':names[:2],'protocol_usernames':names[2:],
            'scenario':'confidence-only-v1','confidence_scale_version':'v2','database':'kt_e2e',
            'load_mode':'login-only' if stage=='login180' else 'natural'}
        plan=build_workload(fixture,20261014 if stage=='natural180-r2' else 20261013)
        for name,value in (('fixture.json',fixture),('plan.json',plan)):
            (folder/name).write_text(json.dumps(value))
        binding={'source_config_sha256':canonical(source),'fixture_sha256':sha(folder/'fixture.json'),
            'plan_sha256':sha(folder/'plan.json'),'browser_tool_files':{key:sha(path) for key,path in tools.items()},
            'driver_files':{'server-driver':'d'*64},'teacher_probe_sha256':'e'*64}
        (folder/'fixture-binding.json').write_text(json.dumps(binding))
        (folder/'start.json').write_text(json.dumps({'stage':stage,'start_at':1000,'fixture_binding':binding}))
        config={'ssh_host':'kt-school','remote_bridge_dir':'/home/ktdata/transition-browser','evidence_dir':str(root),
            'client_dir':str(client),'node_executable':tools['node'],'browsers_path':str(root/'browsers'),'tool_files':tools}
        return folder,config,fixture,plan,binding

    def test_exact_binding_and_separate_two_browser_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp))
            value=launcher.validate_stage(config,'candidate10',folder,1000)
            self.assertEqual(value['binding'],b)
            self.assertEqual(len(value['fixture']['browser_usernames']),2)

    def test_single_diagnostic180_launches_178_plus_two_new_version_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp),'diagnostic180')
            value=launcher.validate_stage(config,'diagnostic180',folder,1000)
            self.assertEqual(len(value['fixture']['protocol_usernames']),178)
            self.assertEqual(len(value['fixture']['browser_usernames']),2)
            self.assertEqual(value['fixture']['source_config']['phase'],'candidate')
            command=launcher.browser_command(config,'diagnostic180',folder,value['start_at'])
            self.assertEqual(command[command.index('--mode')+1],'natural')
            self.assertEqual(command[command.index('--items')+1],'20')

    def test_mismatched_tool_prevents_process_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp))
            Path(config['tool_files']['browser_probe']).write_bytes(b'changed')
            with self.assertRaises(ValueError):launcher.validate_stage(config,'candidate10',folder,1000)

    def test_relative_evidence_paths_survive_node_client_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder,config,f,p,b=self.fixture(root)
            previous=Path.cwd()
            try:
                os.chdir(root)
                config['node_executable']=os.path.relpath(config['node_executable'])
                config['tool_files']={key:(path if key=='launcher' else os.path.relpath(path)) for key,path in config['tool_files'].items()}
                command=launcher.browser_command(config,'candidate10',Path('candidate10'),1000)
                self.assertTrue(Path(command[0]).is_absolute())
                self.assertTrue(Path(command[1]).is_absolute())
                for option in ('--manifest','--plan','--output','--stop-file','--visual-output'):
                    self.assertTrue(Path(command[command.index(option)+1]).is_absolute(),option)
                os.chdir(config['client_dir'])
                self.assertEqual(json.loads(Path(command[command.index('--manifest')+1]).read_text()),f)
            finally:os.chdir(previous)

    def test_armed_validation_does_not_need_start_and_diagnostic2_has_no_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp),'diagnostic2')
            (folder/'start.json').unlink()
            value=launcher.validate_stage(config,'diagnostic2',folder,require_start=False)
            self.assertEqual(value['fixture']['protocol_usernames'],[])
            self.assertIsNone(value['start_at'])
            command=launcher.browser_command(config,'diagnostic2',folder,None,armed_start=True)
            self.assertNotIn('--start-at',command)
            self.assertTrue(Path(command[command.index('--armed-file')+1]).is_absolute())
            self.assertTrue(Path(command[command.index('--start-file')+1]).is_absolute())

    def test_source_or_fixture_or_start_mismatch_blocks(self):
        for kind in ('fixture','source','start'):
            with tempfile.TemporaryDirectory() as tmp:
                folder,config,f,p,b=self.fixture(Path(tmp))
                if kind=='fixture':(folder/'fixture.json').write_text(json.dumps({**f,'task_id':999}))
                if kind=='source':
                    b['source_config_sha256']='f'*64
                    (folder/'fixture-binding.json').write_text(json.dumps(b))
                    (folder/'start.json').write_text(json.dumps({'stage':'candidate10','start_at':1000,'fixture_binding':b}))
                with self.assertRaises(ValueError):launcher.validate_stage(config,'candidate10',folder,1001 if kind=='start' else 1000)

    def test_login180_launches_exactly_two_login_only_browsers(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp),'login180')
            value=launcher.validate_stage(config,'login180',folder,1000)
            self.assertTrue(all(row['arrival_offset_seconds']==0 for row in value['plan']['students']))
            command=launcher.browser_command(config,'login180',folder,value['start_at'])
            self.assertEqual(command[command.index('--mode')+1],'login-only')
            self.assertEqual(len(value['fixture']['browser_usernames']),2)
            self.assertEqual(len(value['fixture']['protocol_usernames']),178)

    def test_frozen_roles_and_confidence_plan_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp))
            p['students'][0]['optional_tool_items']=[0]
            (folder/'plan.json').write_text(json.dumps(p));b['plan_sha256']=sha(folder/'plan.json')
            (folder/'fixture-binding.json').write_text(json.dumps(b))
            (folder/'start.json').write_text(json.dumps({'stage':'candidate10','start_at':1000,'fixture_binding':b}))
            with self.assertRaises(ValueError):launcher.validate_stage(config,'candidate10',folder,1000)

    def test_round_two_and_common_start_build_natural_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp),'natural180-r2')
            value=launcher.validate_stage(config,'natural180-r2',folder,1000)
            command=launcher.browser_command(config,'natural180-r2',folder,value['start_at'])
            self.assertEqual(command[command.index('--start-at')+1],'1000.0')
            self.assertEqual(command[command.index('--items')+1],'20')
            self.assertIn('--plan',command)
            self.assertIn('--visual-output',command)

    def test_atomic_final_bridge_refuses_overwrite(self):
        commands=[]
        def run(command,**kwargs):commands.append(command);return type('Result',(),{'returncode':0})()
        with patch.object(launcher.subprocess,'run',run):
            launcher.publish({'ssh_host':'kt-school','remote_bridge_dir':'/home/ktdata/bridge'},'candidate10',Path('local'), 'browser.json')
        self.assertEqual(commands[0][0],'scp')
        self.assertIn('test ! -e',commands[1][-1])
        self.assertIn('mv',commands[1][-1])

    def test_validation_mode_never_calls_transport_or_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,config,f,p,b=self.fixture(Path(tmp))
            config_path=Path(tmp)/'config.json';config_path.write_text(json.dumps(config))
            with patch.object(launcher.subprocess,'run',side_effect=AssertionError('Unexpected remote/process operation')),patch.object(launcher.subprocess,'Popen',side_effect=AssertionError('Unexpected browser operation')):
                self.assertEqual(launcher.main(['candidate10','--config',str(config_path),'--validate-only','--start-at','1000']),0)

    def test_stubbed_launch_transfers_binding_before_final_report(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);supply,config,f,p,b=self.fixture(root)
            folder=root/'local'/'candidate10'
            def run(command,**kwargs):
                calls.append(command)
                if command[0]=='scp' and command[1].startswith('kt-school:'):
                    shutil.copyfile(supply/Path(command[1]).name,command[2])
                return type('Result',(),{'returncode':1 if command[0]=='ssh' and command[-1].startswith('test -f ') else 0,'stdout':'900'})()
            class Process:
                returncode=None
                def __init__(self,command,**kwargs):
                    calls.append(command);self.ticks=0
                    (folder/'browser.json').write_text(json.dumps({'completed':True,'probes':[]}))
                def poll(self):
                    self.ticks+=1
                    if self.ticks>1:self.returncode=0
                    return self.returncode
            with patch.object(launcher.subprocess,'run',run),patch.object(launcher.subprocess,'Popen',Process),patch.object(launcher,'verify_browser_selection',lambda _:None),patch.object(launcher,'host_memory_sample',lambda:{'synthetic':True}),patch.object(launcher.time,'time',lambda:900),patch.object(launcher.time,'sleep',lambda _:None):
                result=launcher.launch(config,'candidate10',folder,1000)
            self.assertEqual(result['exit_code'],0)
            outgoing=[row for row in calls if row[0]=='scp' and not row[1].startswith('kt-school:')]
            self.assertTrue(outgoing[0][2].endswith('/browser-binding.json.incoming'))
            self.assertTrue(outgoing[-1][2].endswith('/browser.json.incoming'))
            self.assertEqual(json.loads((folder/'browser-binding.json').read_text()),b)

    def test_clock_calibration_retains_three_samples_and_shortest_rtt_bounds(self):
        times=iter((100,100.1,200,200.02,300,300.2));monos=iter((100,100.1,200,200.02,300,300.2))
        results=iter(('99.9','200.5','300'))
        def run(command,**kwargs):return type('Result',(),{'stdout':next(results),'returncode':0})()
        with patch.object(launcher.time,'time',lambda:next(times)),patch.object(launcher.time,'monotonic',lambda:next(monos)),patch.object(launcher.subprocess,'run',run):
            value=launcher.clock_calibration({'ssh_host':'kt-school'})
        self.assertEqual(len(value['samples']),3)
        self.assertEqual(value['selected_sample_id'],value['samples'][1]['sample_id'])
        self.assertAlmostEqual(value['server_minus_browser_lower_seconds'],.48)
        self.assertAlmostEqual(value['server_minus_browser_upper_seconds'],.5)

    def test_armed_launch_publishes_ready_binding_before_fetching_start_and_release(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);supply,config,f,p,b=self.fixture(root,'diagnostic2');folder=root/'local'/'diagnostic2'
            def run(command,**kwargs):
                calls.append(command)
                if command[0]=='scp' and command[1].startswith('kt-school:'):
                    if command[1].endswith('/start.json'):
                        self.assertTrue(any(row[0]=='scp' and row[2].endswith('/browser-armed.json.incoming') for row in calls))
                    shutil.copyfile(supply/Path(command[1]).name,command[2])
                stop=command[0]=='ssh' and command[-1].startswith('test -f ') and command[-1].endswith('/browser.stop')
                return type('Result',(),{'returncode':1 if stop else 0,'stdout':'900'})()
            class Process:
                pid=1234;returncode=None
                def __init__(self,command,**kwargs):
                    calls.append(command)
                    self.command=command
                    self.folder=folder
                    (folder/'browser.armed.json').write_text(json.dumps({'pid':self.pid,'probe_count':2,
                        'fixture_sha256':b['fixture_sha256'],'plan_sha256':b['plan_sha256']}))
                def poll(self):
                    if (folder/'browser.start.json').exists():
                        (folder/'browser.json').write_text(json.dumps({'completed':True,'probes':[]}))
                        self.returncode=0
                    return self.returncode
            calibration={'synthetic':True}
            with patch.object(launcher.subprocess,'run',run),patch.object(launcher.subprocess,'Popen',Process),patch.object(launcher,'verify_browser_selection',lambda _:None),patch.object(launcher,'clock_calibration',lambda _:calibration),patch.object(launcher.time,'time',lambda:900):
                result=launcher.launch(config,'diagnostic2',folder,armed_start=True)
            self.assertEqual(result['exit_code'],0)
            ready=launcher.read(folder/'browser-armed.json')
            self.assertEqual(ready['fixture_binding'],b)
            release=launcher.read(folder/'browser.start.json')
            self.assertEqual(release['start_at'],1000)
            self.assertEqual(release['fixture_sha256'],b['fixture_sha256'])
            self.assertFalse((folder/'browser-launch-error.json').exists())

    def test_armed_launch_failure_never_releases_and_publishes_concise_error(self):
        for kind in ('early-exit','wrong-fixture','timeout'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);supply,config,f,p,b=self.fixture(root);folder=root/'local'/'candidate10';outgoing=[]
                def run(command,**kwargs):
                    if command[0]=='scp' and command[1].startswith('kt-school:'):
                        shutil.copyfile(supply/Path(command[1]).name,command[2])
                    elif command[0]=='scp':outgoing.append(command)
                    return type('Result',(),{'returncode':0})()
                class Process:
                    pid=1234;returncode=23 if kind=='early-exit' else None
                    def __init__(self,*args,**kwargs):
                        if kind=='wrong-fixture':
                            (folder/'browser.armed.json').write_text(json.dumps({'pid':self.pid,'probe_count':2,
                                'fixture_sha256':'wrong','plan_sha256':b['plan_sha256']}))
                    def poll(self):return self.returncode
                    def wait(self,timeout):self.returncode=23
                ticks=iter((0,181))
                with patch.object(launcher.subprocess,'run',run),patch.object(launcher.subprocess,'Popen',Process),patch.object(launcher,'verify_browser_selection',lambda _:None),patch.object(launcher.time,'monotonic',lambda:next(ticks)):
                    with self.assertRaises((RuntimeError,ValueError,TimeoutError)):
                        launcher.launch(config,'candidate10',folder,armed_start=True)
                self.assertFalse((folder/'browser.start.json').exists())
                self.assertTrue((folder/'browser.stop').exists())
                self.assertEqual(launcher.read(folder/'browser-launch-error.json')['released'],False)
                self.assertTrue(outgoing[-1][2].endswith('/browser-launch-error.json.incoming'))


if __name__=='__main__':unittest.main()
