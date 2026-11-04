"""Only offline fake Node processes; no installed CLI or credential access."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
SHIM=ROOT/'skills/shared/scripts/redbook_read.py'
NOTE_ID='64abcdef1234567890abcdef'
URL=f'https://www.xiaohongshu.com/explore/{NOTE_ID}'


def module():
    spec=importlib.util.spec_from_file_location('redbook_read',SHIM)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def shim(monkeypatch):
    m=module()
    monkeypatch.setenv('EASEL_XHS_EXPECTED_ACCOUNT','1234567890abcdef12345678')
    monkeypatch.setattr(m,'_profile_cookie_string',lambda deadline:'a1=TEST_A1; web_session=TEST_SESSION')
    return m


def fake_node(tmp_path,monkeypatch,shim,payload,stderr='',code=0):
    f=tmp_path/'node'
    f.write_text('#!'+sys.executable+'\nimport sys\nprint('+repr(json.dumps(payload))+')\n'
                 +'sys.stderr.write('+repr(stderr)+')\nsys.exit('+str(code)+')\n')
    f.chmod(0o700)
    monkeypatch.setattr(shim,'_node',lambda:str(f))
    return f


def invoke(shim,capsys,args):
    code=shim.main(args)
    out=capsys.readouterr()
    assert out.err==''
    return code,json.loads(out.out)


def test_status_maps_real_get_me_fields_only(shim,monkeypatch,tmp_path,capsys):
    fake_node(tmp_path,monkeypatch,shim,{'user_id':NOTE_ID,'guest':False,'nickname':'PRIVATE','web_session':'SECRET'})
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==0 and result=={'ok':True,'data':{'authenticated':True,'user':{'id':NOTE_ID,'guest':False}}}
    assert 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('raw',[{},None,[],{'authenticated':True},
    {'user_id':NOTE_ID},{'user_id':NOTE_ID,'guest':None},
    {'user_id':NOTE_ID,'guest':0},
    {'user_id':NOTE_ID,'guest':True},{'user_id':NOTE_ID,'guest':'false'}])
def test_unknown_or_guest_identity_is_never_authenticated(shim,monkeypatch,tmp_path,capsys,raw):
    fake_node(tmp_path,monkeypatch,shim,raw)
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==1 and result['error']['code']=='not_authenticated'


@pytest.mark.parametrize('args', [['publish','--json'],['auth','export','--json'],
    ['search','--private','--json'],['search','a'*101,'--json'],['search','q','--sort','latest','--json'],
    ['read','file:///etc/passwd','--json'],['read','https://127.0.0.1/x','--json'],
    ['status'],['whoami','--json'],['search','x\x00','--json']])
def test_forbidden_arguments_never_spawn(shim,monkeypatch,capsys,args):
    monkeypatch.setattr(shim.subprocess,'Popen',lambda *a,**k:pytest.fail('forbidden argv must not spawn'))
    code,result=invoke(shim,capsys,args)
    assert code==1 and result['error']['code']=='unknown_error'


@pytest.mark.parametrize('args,expected',[
    (['status','--json'],['whoami','--cookie-source','chrome','--platform','xhs','--json']),
    (['search','保险','--json'],['search','保险','--sort','popular','--cookie-source','chrome','--platform','xhs','--json']),
    (['read',URL,'--json'],['read',URL,'--cookie-source','chrome','--platform','xhs','--json'])])
def test_exact_readonly_node_argv_inherits_outer_group(shim,monkeypatch,tmp_path,capsys,args,expected):
    payload={'user_id':NOTE_ID,'guest':False} if args[0]=='status' else {'items':[]}
    node=fake_node(tmp_path,monkeypatch,shim,payload)
    original=shim.subprocess.Popen
    calls=[]
    def spawn(argv,**kw):calls.append((argv,kw));return original(argv,**kw)
    monkeypatch.setattr(shim.subprocess,'Popen',spawn)
    code,result=invoke(shim,capsys,args)
    assert code==0
    if args[0]=='search':
        assert calls[0][0]==[str(node),'--input-type=module','--eval',shim._SEARCH_BOOTSTRAP,str(shim._ENTRY)]
        assert calls[0][1]['env']['EASEL_XHS_SEARCH_KEYWORD']=='保险'
    else:
        assert calls[0][0]==[str(node),str(shim._ENTRY),*expected]
    assert calls[0][1]['shell'] is False and calls[0][1]['start_new_session'] is False
    assert calls[0][1]['stdin']==shim.subprocess.DEVNULL


@pytest.mark.parametrize('stderr,want',[
    ('Error: Session expired PRIVATE\nCode: -100\n','not_authenticated'),
    ('Error: Missing cookies PRIVATE\nCode: -101\n','not_authenticated'),
    ('Error: Captcha required: type=PRIVATE, uuid=SECRET\n','verification_required'),
    ('Error: IP blocked PRIVATE\nCode: 300012\n','ip_blocked'),
    ('Error: signature PRIVATE\nCode: 300015\n','signature_error'),
    ('Error: API failed PRIVATE\nCode: 429\n','api_error'),
    ('Traceback PRIVATE /local/path','unknown_error')])
def test_stderr_classification_never_discloses_raw_text(shim,monkeypatch,tmp_path,capsys,stderr,want):
    fake_node(tmp_path,monkeypatch,shim,{'SECRET_TOKEN':'PRIVATE'},stderr,1)
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==1 and result['error']['code']==want
    assert 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)


def test_search_and_read_raw_json_go_only_to_existing_normalizer(shim,monkeypatch,tmp_path,capsys):
    raw={'items':[{'id':NOTE_ID,'model_type':'note','xsec_token':'INTERNAL','note_card':{'display_title':'保险'}}]}
    fake_node(tmp_path,monkeypatch,shim,raw)
    code,result=invoke(shim,capsys,['search','保险','--json'])
    assert code==0 and result=={'ok':True,'data':raw}


def test_raw_node_output_is_bounded(shim,monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(shim,'_MAX_BYTES',100)
    fake_node(tmp_path,monkeypatch,shim,{'payload':'x'*1000})
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==1 and result['error']['code']=='unknown_error'


def test_fake_node_through_bundled_shim_and_existing_normalizer(tmp_path,monkeypatch,capsys):
    from easel import xhs_notes as notes
    import os
    fake=tmp_path/'node'
    account={'user_id':'1234567890abcdef12345678','guest':False,'nickname':'PRIVATE_ACCOUNT'}
    search={'items':[{'model_type':'note','id':NOTE_ID,'xsec_token':'PRIVATE_TOKEN',
        'webUrl':URL+'?xsec_token=PRIVATE_TOKEN','note_card':{
            'display_title':'家庭保险','interact_info':{'liked_count':'1.2万'}}}]}
    detail={'noteId':NOTE_ID,'title':'家庭保险','desc':'正文'}
    fake.write_text('#!'+sys.executable+'\nimport json,sys\n'
        +'op=("search" if "searchNotes" in sys.argv[3] else "read") if "--eval" in sys.argv else sys.argv[2]\n'
        +'data='+repr(account)+' if op=="whoami" else ('+repr(search)+' if op=="search" else '+repr(detail)+')\n'
        +'print(json.dumps(data))\n')
    fake.chmod(0o700)
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    wrapper=tmp_path/'offline-redbook-shim'
    wrapper.write_text('#!'+sys.executable+'\nimport importlib.util\n'
        +'spec=importlib.util.spec_from_file_location("offline_redbook",'+repr(str(SHIM))+')\n'
        +'m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
        +'m._profile_cookie_string=lambda deadline:"a1=TEST_A1; web_session=TEST_SESSION"\n'
        +'raise SystemExit(m.main())\n')
    wrapper.chmod(0o700)
    monkeypatch.setenv('EASEL_XHS_CLI',str(wrapper))
    monkeypatch.setattr(notes,'_BUNDLED_SHIM',wrapper)
    monkeypatch.setattr(notes,'_CACHE',{})
    monkeypatch.setattr(notes,'_STOP_ERROR',None)
    result=notes.search_notes('家庭保险')
    assert result['error'] is None
    assert result['items'][0]['content_status']=='summary'
    assert result['items'][0]['metrics']=={'likes':12000,'saves':None,'comments':None}
    assert result['items'][0]['url']==URL
    detail_result=notes.read_note(URL)
    assert detail_result['error'] is None and detail_result['items'][0]['body']=='正文'
    exposed=json.dumps(result)+json.dumps(detail_result)+str(capsys.readouterr())
    assert 'PRIVATE' not in exposed and 'xsec_token' not in exposed


def session_rows():
    return [{'name':name,'value':value,'domain':'.xiaohongshu.com','path':'/','expires':-1}
            for name,value in [('a1','TEST_A1'),('web_session','TEST_SESSION'),('webId','TEST_WEB')]]


def test_project_session_cookie_allowlist_and_required_values():
    m=module()
    assert m._select_cookies(session_rows())=='a1=TEST_A1; web_session=TEST_SESSION; webId=TEST_WEB'
    for rows in [[],session_rows()[1:],session_rows()[:1],
                 [dict(row,domain='xiaohongshu.com.evil.example') for row in session_rows()],
                 [dict(row,value='BAD; injected=SECRET') for row in session_rows()],
                 [*session_rows(),dict(session_rows()[0],value='CONFLICT')]]:
        with pytest.raises(m._SessionError) as exc:m._select_cookies(rows)
        assert exc.value.code=='not_authenticated'


def test_foreign_names_domains_and_paths_cannot_enter_node_header():
    m=module()
    rows=session_rows()+[
        {'name':'FOREIGN_SECRET','value':'PRIVATE','domain':'.xiaohongshu.com','path':'/'},
        {'name':'a1','value':'ATTACK','domain':'creator.xiaohongshu.com','path':'/'},
        {'name':'web_session','value':'ATTACK','domain':'.xiaohongshu.com','path':'/creator'}]
    header=m._select_cookies(rows)
    assert 'PRIVATE' not in header and 'ATTACK' not in header


def profile_dependencies(monkeypatch,m,*,fail_at=None):
    from types import SimpleNamespace
    calls=[]
    class Context:
        def cookies(self,url):
            calls.append(('cookies',url))
            if fail_at=='cookies':raise KeyboardInterrupt()
            return session_rows()
        def close(self):
            calls.append(('close',))
            if fail_at=='close':raise RuntimeError('PRIVATE_CLOSE')
    class Manager:
        def __enter__(self):calls.append(('manager_enter',));return 'playwright'
        def __exit__(self,*a):calls.append(('manager_exit',))
    class Lock:
        def __init__(self,path):calls.append(('profile',path))
        def acquire(self,timeout):
            calls.append(('lock',timeout))
            if fail_at=='lock':raise TimeoutError('PRIVATE_PATH')
        def release(self):calls.append(('release',))
    def launch(p,**kw):
        calls.append(('launch',kw))
        if fail_at=='launch':raise RuntimeError('PRIVATE_LAUNCH')
        return Context()
    monkeypatch.setattr(m,'_dependencies',lambda:(Manager,Lock,launch,lambda base:'OWNED_PROFILE',lambda:'COMMUNITY_BASE'))
    return calls


def test_session_uses_fixed_profile_no_navigation_and_closes_before_return(monkeypatch):
    m=module()
    calls=profile_dependencies(monkeypatch,m)
    header=m._profile_cookie_string(m.time.monotonic()+30)
    assert header.startswith('a1=TEST_A1;')
    assert calls[0]==('profile','OWNED_PROFILE') and ('lock',1) in calls
    launch=next(value for tag,*value in calls if tag=='launch')[0]
    assert launch['headed'] is False and launch['base'] == 'COMMUNITY_BASE' and launch['proxy'] is None
    assert launch['login_assets'] is False and launch['timeout_ms']<=8000 and launch['retry_target_closed'] is False
    assert ('cookies','https://www.xiaohongshu.com') in calls
    assert [x[0] for x in calls][-3:]==['close','manager_exit','release']


@pytest.mark.parametrize('phase',['lock','launch','cookies','close'])
def test_failure_and_cancellation_clean_only_owned_resources(monkeypatch,phase):
    m=module()
    calls=profile_dependencies(monkeypatch,m,fail_at=phase)
    with pytest.raises((m._SessionError,KeyboardInterrupt)):
        m._profile_cookie_string(m.time.monotonic()+30)
    names=[x[0] for x in calls]
    if phase=='lock':
        assert 'launch' not in names and 'manager_enter' not in names
    else:
        assert names[-2:]==['manager_exit','release']
    if phase in ('cookies','close'):assert 'close' in names


def test_exhausted_budget_does_not_acquire_profile_or_spawn(monkeypatch,capsys):
    m=module()
    monkeypatch.setenv('EASEL_XHS_READ_BUDGET_MS','0')
    monkeypatch.setattr(m,'_dependencies',lambda:pytest.fail('expired must not touch profile'))
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**k:pytest.fail('expired must not spawn'))
    code,result=invoke(m,capsys,['status','--json'])
    assert code==1 and result['error']['code']=='read_timeout'


def test_node_environment_ignores_external_auth_without_mutating_parent(shim,monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('REDBOOK_COOKIE_STRING','OTHER_ACCOUNT')
    monkeypatch.setenv('REDBOOK_COOKIE_FILE','/PRIVATE_OTHER_PROFILE')
    monkeypatch.setattr(shim,'_profile_cookie_string',lambda deadline:'a1=TEST_A1; web_session=TEST_SESSION')
    fake_node(tmp_path,monkeypatch,shim,{'user_id':NOTE_ID,'guest':False})
    original=shim.subprocess.Popen
    inspected=[]
    def spawn(argv,**kw):
        inspected.append({'argv':argv,'cookie':kw.get('env',{}).get('REDBOOK_COOKIE_STRING'),
                          'has_cookie_file':'REDBOOK_COOKIE_FILE' in kw.get('env',{})})
        return original(argv,**kw)
    monkeypatch.setattr(shim.subprocess,'Popen',spawn)
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==0
    assert inspected[0]['cookie']=='a1=TEST_A1; web_session=TEST_SESSION'
    assert inspected[0]['has_cookie_file'] is False
    assert all('TEST_' not in value for value in inspected[0]['argv'])
    import os
    assert os.environ['REDBOOK_COOKIE_STRING']=='OTHER_ACCOUNT'
    assert os.environ['REDBOOK_COOKIE_FILE']=='/PRIVATE_OTHER_PROFILE'
    assert 'TEST_' not in json.dumps(result)


def test_node_is_not_started_if_profile_step_exhausts_budget(shim,monkeypatch,capsys):
    clock={'now':100.0}
    monkeypatch.setattr(shim.time,'monotonic',lambda:clock['now'])
    monkeypatch.setattr(shim,'_node',lambda:'/offline/node')
    def acquire(deadline):
        clock['now']+=31
        return 'a1=TEST; web_session=TEST'
    monkeypatch.setattr(shim,'_profile_cookie_string',acquire)
    monkeypatch.setattr(shim.subprocess,'Popen',lambda *a,**k:pytest.fail('exhausted must not spawn'))
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==1 and result['error']['code']=='read_timeout'


def test_node_deadline_stops_waiting_without_exposing_environment(shim,monkeypatch,tmp_path,capsys):
    f=tmp_path/'sleep-node'
    f.write_text('#!'+sys.executable+'\nimport time\ntime.sleep(5)\n')
    f.chmod(0o700)
    monkeypatch.setattr(shim,'_node',lambda:str(f))
    monkeypatch.setenv('EASEL_XHS_READ_BUDGET_MS','100')
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==1 and result['error']['code']=='read_timeout'
    assert 'TEST_' not in json.dumps(result)


def test_missing_project_session_never_falls_back_to_other_browser(shim,monkeypatch,capsys):
    monkeypatch.setattr(shim,'_node',lambda:'/offline/node')
    def missing(deadline):raise shim._SessionError('not_authenticated')
    monkeypatch.setattr(shim,'_profile_cookie_string',missing)
    monkeypatch.setattr(shim.subprocess,'Popen',lambda *a,**k:pytest.fail('must not fallback'))
    code,result=invoke(shim,capsys,['status','--json'])
    assert code==1 and result['error']['code']=='not_authenticated'


def test_sigterm_unwinds_project_session_before_returning_safe_cancelled(tmp_path):
    import signal,subprocess,time
    ready=tmp_path/'ready';closed=tmp_path/'closed'
    script=('import importlib.util,time\nfrom pathlib import Path\n'
        +'spec=importlib.util.spec_from_file_location("offline_bridge",'+repr(str(SHIM))+')\n'
        +'m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
        +'m._node=lambda:"/offline/node"\n'
        +'def profile(deadline):\n'
        +' Path('+repr(str(ready))+').write_text("ready")\n'
        +' try:time.sleep(10)\n'
        +' finally:Path('+repr(str(closed))+').write_text("closed")\n'
        +'m._profile_cookie_string=profile\n'
        +'raise SystemExit(m.main(["status","--json"]))\n')
    p=subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        end=time.monotonic()+3
        while not ready.exists() and time.monotonic()<end:time.sleep(.01)
        assert ready.exists()
        p.send_signal(signal.SIGTERM)
        out,err=p.communicate(timeout=3)
        assert closed.exists()
        assert p.returncode==1 and json.loads(out)['error']['code']=='cancelled'
        assert err==''
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=2)


def test_official_platform_hosts_cannot_be_overridden_by_parent_environment(monkeypatch):
    import os
    m=module()
    polluted={'REDBOOK_EDITH_HOST':'https://other-host.invalid/private',
              'REDBOOK_CREATOR_HOST':'https://another-host.invalid/private'}
    for name,value in polluted.items():monkeypatch.setenv(name,value)
    derived=m._child_env('a1=TEST_A1; web_session=TEST_SESSION')
    remaining_overrides=sorted(name for name in polluted if name in derived)
    assert remaining_overrides==[]
    assert derived['REDBOOK_COOKIE_STRING']=='a1=TEST_A1; web_session=TEST_SESSION'
    for name,value in polluted.items():assert os.environ[name]==value


def locator_context():
    return {'note_id':NOTE_ID,'token':'PRIVATE_NOTE_TOKEN','source':'pc_search',
            'account_id':'1234567890abcdef12345678'}


def test_trusted_note_context_uses_fixed_original_client_bootstrap_not_secret_argv(shim,monkeypatch,tmp_path,capsys):
    context=locator_context()
    monkeypatch.setenv('EASEL_XHS_NOTE_CONTEXT',json.dumps(context))
    fake_node(tmp_path,monkeypatch,shim,{'items':[]})
    original=shim.subprocess.Popen
    seen=[]
    def spawn(argv,**kw):
        seen.append((list(argv),kw['env'].get('EASEL_XHS_NOTE_CONTEXT')))
        return original(argv,**kw)
    monkeypatch.setattr(shim.subprocess,'Popen',spawn)
    code,result=invoke(shim,capsys,['read',URL+'?xsec_token=USER_UNTRUSTED','--json'])
    assert code==0
    assert '--eval' in seen[0][0]
    assert all('PRIVATE_NOTE_TOKEN' not in value and 'USER_UNTRUSTED' not in value for value in seen[0][0])
    assert json.loads(seen[0][1])==context
    assert 'PRIVATE_NOTE_TOKEN' not in json.dumps(result)


@pytest.mark.parametrize('change',[{'note_id':'a'*24},{'source':'pc_other'},
    {'token':'bad\nSECRET'},{'account_id':''}])
def test_mismatched_or_invalid_note_context_never_runs_profile_or_node(shim,monkeypatch,capsys,change):
    context={**locator_context(),**change}
    monkeypatch.setenv('EASEL_XHS_NOTE_CONTEXT',json.dumps(context))
    monkeypatch.setattr(shim,'_profile_cookie_string',lambda *_:pytest.fail('invalid locator must not touch profile'))
    monkeypatch.setattr(shim.subprocess,'Popen',lambda *a,**kw:pytest.fail('invalid locator must not spawn'))
    code,result=invoke(shim,capsys,['read',URL,'--json'])
    assert code==1 and result['error']['code']=='unknown_error'
    assert 'SECRET' not in json.dumps(result)


def test_search_does_not_pass_an_inherited_note_locator_to_node(shim,monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('EASEL_XHS_NOTE_CONTEXT',json.dumps(locator_context()))
    fake_node(tmp_path,monkeypatch,shim,{'items':[]})
    original=shim.subprocess.Popen
    seen=[]
    def spawn(argv,**kw):
        seen.append('EASEL_XHS_NOTE_CONTEXT' in kw['env'])
        return original(argv,**kw)
    monkeypatch.setattr(shim.subprocess,'Popen',spawn)
    code,_=invoke(shim,capsys,['search','保险','--json'])
    assert code==0 and seen==[False]


@pytest.mark.parametrize('matching_account',[True,False])
def test_original_client_bootstrap_preserves_search_source_and_refuses_account_switch(monkeypatch,matching_account):
    import os,subprocess
    m=module()
    account=locator_context()['account_id'] if matching_account else 'b'*24
    expected=1 if matching_account else 0
    prefix='''
let feeds=0;
process.on('exit',()=>{if(feeds!==EXPECTED_FEEDS)process.exitCode=23;});
globalThis.fetch=async (url,options)=>{
  const parsed=new URL(url);
  if(parsed.origin!=='https://edith.xiaohongshu.com')throw new Error('Unexpected host');
  if(parsed.pathname==='/api/sns/web/v2/user/me')
    return new Response(JSON.stringify({success:true,data:{guest:false,user_id:ACCOUNT}}));
  if(parsed.pathname==='/api/sns/web/v1/feed'){
    feeds++;
    const body=JSON.parse(options.body);
    if(body.source_note_id!==NOTE_ID || body.xsec_source!=='pc_search'
        || body.xsec_token!==JSON.parse(process.env.EASEL_XHS_NOTE_CONTEXT).token)
      throw new Error('Wrong note context');
    return new Response(JSON.stringify({success:true,data:{items:[{note_card:{
      note_id:NOTE_ID,title:'测试详情',desc:'已获取正文'}}]}}));
  }
  throw new Error('Unmocked request blocked');
};
'''.replace('EXPECTED_FEEDS',str(expected)).replace('ACCOUNT',json.dumps(account)).replace('NOTE_ID',json.dumps(NOTE_ID))
    env=os.environ.copy()
    env['REDBOOK_COOKIE_STRING']='a1=TEST_A1; web_session=TEST_SESSION; webId=TEST_WEB'
    env['EASEL_XHS_NOTE_CONTEXT']=json.dumps(locator_context())
    env['REDBOOK_EDITH_HOST']='https://untrusted.invalid'
    result=subprocess.run(['node','--input-type=module','--eval',prefix+m._NOTE_READ_BOOTSTRAP,str(m._ENTRY)],
        env=env,text=True,capture_output=True,timeout=10)
    if matching_account:
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['items'][0]['note_card']['desc']=='已获取正文'
    else:
        assert result.returncode==1
        assert result.stdout=='' and result.stderr=='Code: -100\n'
    assert 'PRIVATE_NOTE_TOKEN' not in result.stdout+result.stderr


@pytest.mark.parametrize('matching_account',[True,False])
def test_search_uses_one_original_client_and_verifies_expected_account_before_search(monkeypatch,matching_account):
    import os,subprocess
    m=module()
    account=locator_context()['account_id'] if matching_account else 'b'*24
    expected=1 if matching_account else 0
    prefix='''
let searches=0;
process.on('exit',()=>{if(searches!==EXPECTED_SEARCHES)process.exitCode=23;});
globalThis.fetch=async(url,options)=>{
  const parsed=new URL(url);
  if(parsed.origin!=='https://edith.xiaohongshu.com')throw new Error('Unexpected host');
  if(parsed.pathname==='/api/sns/web/v2/user/me')
    return new Response(JSON.stringify({success:true,data:{guest:false,user_id:ACCOUNT}}));
  if(parsed.pathname==='/api/sns/web/v1/search/notes'){
    searches++;
    const body=JSON.parse(options.body);
    if(body.keyword!=='家庭保险' || body.sort!=='popularity_descending' || body.page!==1
        || body.page_size!==20 || body.image_formats.join(',')!=='jpg,webp,avif')
      throw new Error('Wrong search options');
    return new Response(JSON.stringify({success:true,data:{items:[]}}));
  }
  throw new Error('Unmocked request blocked');
};
'''.replace('EXPECTED_SEARCHES',str(expected)).replace('ACCOUNT',json.dumps(account))
    env=os.environ.copy()
    env['REDBOOK_COOKIE_STRING']='a1=TEST_A1; web_session=TEST_SESSION; webId=TEST_WEB'
    env['EASEL_XHS_EXPECTED_ACCOUNT']=locator_context()['account_id']
    env['EASEL_XHS_SEARCH_KEYWORD']='家庭保险'
    result=subprocess.run(['node','--input-type=module','--eval',prefix+m._SEARCH_BOOTSTRAP,str(m._ENTRY)],
        env=env,text=True,capture_output=True,timeout=10)
    if matching_account:
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)=={'items':[]}
    else:
        assert result.returncode==1 and result.stdout=='' and result.stderr=='Code: -100\n'


def test_search_without_verified_expected_account_never_touches_profile_or_http(shim,monkeypatch,capsys):
    monkeypatch.delenv('EASEL_XHS_EXPECTED_ACCOUNT',raising=False)
    monkeypatch.setattr(shim,'_profile_cookie_string',lambda *_:pytest.fail('unverified search must not touch profile'))
    monkeypatch.setattr(shim.subprocess,'Popen',lambda *a,**k:pytest.fail('unverified search must not spawn'))
    code,result=invoke(shim,capsys,['search','保险','--json'])
    assert code==1 and result['error']['code']=='not_authenticated'
