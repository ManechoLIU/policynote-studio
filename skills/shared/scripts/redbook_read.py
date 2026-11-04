#!/usr/bin/env python3
"""Readonly argv/envelope shim for the bundled Redbook CLI.

Run under easel.xhs_notes' bounded process-group owner. Node stays in that group.
The authorized workbench context supplies official-domain cookies in memory via
Playwright's public API; no auth files, other browsers or login jobs are used.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

_ROOT=Path(__file__).resolve().parents[3]
_ENTRY=_ROOT/'skills/builtin/skill-xhs-analyzer/dist/cli.js'
_MAX_BYTES=2*1024*1024


class _Invalid(Exception):
    pass


class _SessionError(Exception):
    def __init__(self,code,stage=None):
        self.stage = stage if stage in ('cookie_read','cookie_select','node_resolve','node_exit','json_parse','identity_shape') else None
        self.code=code if code in ('not_authenticated','profile_busy','timeout','cancelled','unknown_error',
                                  'verification_required','ip_blocked','signature_error','api_error') else 'unknown_error'
        super().__init__(self.code)


def _deadline():
    try:
        budget=int(os.environ.get('EASEL_XHS_READ_BUDGET_MS','30000'))
    except (TypeError,ValueError):
        raise _SessionError('timeout') from None
    if budget<=0:raise _SessionError('timeout')
    return time.monotonic()+min(budget,30000)/1000


def _remaining(deadline):
    value=int((deadline-time.monotonic())*1000)
    if value<=0:raise _SessionError('timeout')
    return value


def _dependencies():
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from playwright.sync_api import sync_playwright
    from xhs_publish import _ProfileLock, _launch, _profile_dir, _community_profile_base
    return sync_playwright,_ProfileLock,_launch,_profile_dir,_community_profile_base


def _select_cookies(rows):
    if not isinstance(rows,list):raise _SessionError('not_authenticated')
    selected={}
    for row in rows:
        if not isinstance(row,dict):continue
        name=row.get('name')
        if (name not in ('a1','web_session','webId') or row.get('domain') not in
                ('.xiaohongshu.com','xiaohongshu.com','www.xiaohongshu.com') or row.get('path')!='/'):
            continue
        value=row.get('value')
        if (not isinstance(value,str) or not value or len(value)>8192 or not value.isascii()
                or re.search(r'[\x00-\x20\x7f;,"\\]',value)):
            raise _SessionError('not_authenticated')
        expires=row.get('expires',-1)
        if (not isinstance(expires,(int,float)) or isinstance(expires,bool)
                or not math.isfinite(expires) or expires < -1):
            raise _SessionError('not_authenticated')
        if expires>=0 and expires<=time.time():continue
        if name in selected and selected[name]!=value:
            raise _SessionError('not_authenticated')
        selected[name]=value
    if not selected.get('a1') or not selected.get('web_session'):
        raise _SessionError('not_authenticated')
    # webId is forwarded only when the authorized context actually provides it.
    return '; '.join(f'{key}={selected[key]}' for key in ('a1','web_session','webId') if key in selected)


def _profile_cookie_string(deadline):
    # Leave five seconds for HTTP and two for orderly context/driver cleanup.
    if _remaining(deadline)<=7000:raise _SessionError('timeout')
    factory,Lock,launch,profile_dir,community_profile_base=_dependencies()
    base=community_profile_base()
    lock=Lock(profile_dir(base))
    locked=False
    try:
        try:lock.acquire(1)
        except TimeoutError:raise _SessionError('profile_busy') from None
        locked=True
        available=_remaining(deadline)-7000
        if available<=0:raise _SessionError('timeout')
        manager=factory()
        context=None
        p=manager.__enter__()
        try:
            available=_remaining(deadline)-7000
            if available<=0:raise _SessionError('timeout')
            context=launch(p,headed=False,base=base,proxy=None,login_assets=False,
                           timeout_ms=min(8000,available),retry_target_closed=False)
            _remaining(deadline)
            return _select_cookies(context.cookies('https://www.xiaohongshu.com'))
        finally:
            primary=sys.exc_info()
            cleanup=None
            if context is not None:
                try:context.close()
                except BaseException as error:cleanup=error
            try:manager.__exit__(*primary)
            except BaseException as error:
                if cleanup is None:cleanup=error
            if primary[0] is None and cleanup is not None:
                if isinstance(cleanup,KeyboardInterrupt):raise cleanup
                raise _SessionError('unknown_error') from None
    except (_SessionError,KeyboardInterrupt):
        raise
    except Exception as error:
        raise _SessionError('timeout' if type(error).__name__=='TimeoutError' else 'unknown_error') from None
    finally:
        if locked:lock.release()


def _child_env(header):
    env=os.environ.copy()
    # resolvePlatform honors these host overrides even with --platform xhs.
    # Project credentials must only reach the original fixed official hosts.
    for name in ('REDBOOK_COOKIE_STRING','REDBOOK_COOKIE_FILE',
                 'REDBOOK_EDITH_HOST','REDBOOK_CREATOR_HOST','EASEL_XHS_NOTE_CONTEXT',
                 'EASEL_XHS_EXPECTED_ACCOUNT','EASEL_XHS_SEARCH_KEYWORD'):
        env.pop(name,None)
    env['REDBOOK_COOKIE_STRING']=header
    return env


def _note_context(target):
    raw=os.environ.get('EASEL_XHS_NOTE_CONTEXT')
    if not raw:return None
    if len(raw)>8192:raise _Invalid()
    try:context=json.loads(raw)
    except (ValueError,TypeError):raise _Invalid() from None
    if (not isinstance(context,dict) or set(context)!= {'note_id','token','source','account_id'}
            or context.get('source') not in ('pc_search','pc_feed')
            or not isinstance(context.get('note_id'),str)
            or not re.fullmatch(r'[a-f0-9]{24}',context['note_id'])
            or target!='https://www.xiaohongshu.com/explore/'+context['note_id']
            or not isinstance(context.get('account_id'),str)
            or not re.fullmatch(r'[a-fA-F0-9]{24}',context['account_id'])
            or not isinstance(context.get('token'),str)
            or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,4096}',context['token'])):
        raise _Invalid()
    return context


def _expected_account():
    value=os.environ.get('EASEL_XHS_EXPECTED_ACCOUNT','')
    if not re.fullmatch(r'[a-fA-F0-9]{24}',value):
        raise _SessionError('not_authenticated')
    return value


_SEARCH_BOOTSTRAP=r'''
import {pathToFileURL} from 'node:url';
try {
  const entry=pathToFileURL(process.argv[1]);
  const [{XhsClient},{parseCookieString},{PLATFORMS}]=await Promise.all([
    import(new URL('./lib/client.js',entry)),
    import(new URL('./lib/cookies.js',entry)),
    import(new URL('./lib/platform.js',entry))]);
  const client=new XhsClient(parseCookieString(process.env.REDBOOK_COOKIE_STRING || ''),PLATFORMS.xhs);
  const me=await client.getSelfInfo();
  if (!me || me.guest!==false || me.user_id!==process.env.EASEL_XHS_EXPECTED_ACCOUNT) {
    process.stderr.write('Code: -100\n'); process.exitCode=1;
  } else {
    const data=await client.searchNotes(process.env.EASEL_XHS_SEARCH_KEYWORD,1,20,'popularity_descending',0);
    process.stdout.write(JSON.stringify(data)+'\n');
  }
} catch(error) {
  if (error?.name==='NeedVerifyError') process.stderr.write('Error: Captcha required:\n');
  else {
    const code=String(error?.code ?? '');
    if (['-100','-101','300012','300015','429','461','471'].includes(code))
      process.stderr.write('Code: '+code+'\n');
    else process.stderr.write('Error: Search failed\n');
  }
  process.exitCode=1;
}
'''


_FEED_BOOTSTRAP = _SEARCH_BOOTSTRAP.replace(
    "client.searchNotes(process.env.EASEL_XHS_SEARCH_KEYWORD,1,20,'popularity_descending',0)",
    "client.getHomeFeed('homefeed_recommend')")


# Static code only: all per-note access context stays in the derived environment,
# never OS argv. Use the original exported client and fixed mainland platform.
_NOTE_READ_BOOTSTRAP=r'''
import {pathToFileURL} from 'node:url';
try {
  const entry=pathToFileURL(process.argv[1]);
  const context=JSON.parse(process.env.EASEL_XHS_NOTE_CONTEXT || 'null');
  const [{XhsClient},{parseCookieString},{PLATFORMS}]=await Promise.all([
    import(new URL('./lib/client.js',entry)),
    import(new URL('./lib/cookies.js',entry)),
    import(new URL('./lib/platform.js',entry))]);
  const client=new XhsClient(parseCookieString(process.env.REDBOOK_COOKIE_STRING || ''),PLATFORMS.xhs);
  const me=await client.getSelfInfo();
  if (!me || me.guest!==false || me.user_id!==context.account_id) {
    process.stderr.write('Code: -100\n'); process.exitCode=1;
  } else {
    const data=await client.getNoteById(context.note_id,context.token,context.source);
    process.stdout.write(JSON.stringify(data)+'\n');
  }
} catch(error) {
  if (error?.name==='NeedVerifyError') process.stderr.write('Error: Captcha required:\n');
  else {
    const code=String(error?.code ?? '');
    if (['-100','-101','300012','300015','429','461','471'].includes(code))
      process.stderr.write('Code: '+code+'\n');
    else process.stderr.write('Error: Note read failed\n');
  }
  process.exitCode=1;
}
'''


def _node():
    node=shutil.which('node')
    if not node or not _ENTRY.is_file():
        raise _Invalid()
    return str(Path(node).absolute())


def _arguments(args):
    common=['--cookie-source','chrome','--platform','xhs','--json']
    if args==['feed','--json']:
        return 'feed',[]
    if args==['status','--json']:
        return 'status',['whoami',*common]
    if len(args)!=3 or args[0] not in ('search','read') or args[2]!='--json':
        raise _Invalid()
    target=args[1]
    if not target or target.startswith('-') or re.search(r'[\x00-\x1f\x7f]',target):
        raise _Invalid()
    if args[0]=='search':
        if not target.strip() or len(target)>100:
            raise _Invalid()
        return 'search',['search',target,'--sort','popular',*common]
    try:
        p=urlsplit(target)
        if (len(target)>4096 or re.search(r'[\x00-\x20\\]',target) or p.scheme!='https'
                or p.hostname not in ('xiaohongshu.com','www.xiaohongshu.com')
                or p.username or p.password or p.port not in (None,443)
                or not re.fullmatch(r'/(?:explore|discovery/item)/[a-fA-F0-9]{24}/?',p.path)):
            raise _Invalid()
    except ValueError:
        raise _Invalid() from None
    # The existing adapter has already canonicalized user URLs; never forward a
    # token/query supplied by a direct caller of this shim.
    target='https://www.xiaohongshu.com/explore/'+p.path.rstrip('/').split('/')[-1].lower()
    return 'read',['read',target,*common]


def _run(argv,env,deadline):
    _remaining(deadline)
    process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,shell=False,start_new_session=False,env=env)
    stdout,stderr=bytearray(),bytearray()
    total=0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ,stdout)
            selector.register(process.stderr,selectors.EVENT_READ,stderr)
            while selector.get_map():
                remaining=_remaining(deadline)
                for key,_ in selector.select(min(.1,remaining/1000)):
                    chunk=os.read(key.fileobj.fileno(),65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total+=len(chunk)
                    if total>_MAX_BYTES:
                        raise _Invalid()
                    key.data.extend(chunk)
        try:return process.wait(timeout=_remaining(deadline)/1000),stdout,stderr
        except subprocess.TimeoutExpired:raise _SessionError('timeout') from None
    finally:
        # Only the outer existing runner owns group termination. If an internal
        # failure occurs, stop this Node leader and return a safe error so the
        # outer owner also cleans its descendants; do not create another session.
        if process.returncode is None:
            process.kill()
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:pass
        process.stdout.close()
        process.stderr.close()


def _error_code(stderr):
    text=re.sub(r'\x1b\[[0-9;]*m','',stderr.decode('utf-8','replace'))
    codes=re.findall(r'^Code:\s*(-?\d+)\s*$',text,re.M)
    if len(set(codes))>1:
        return 'unknown_error'
    code=codes[0] if codes else None
    if code in ('-100','-101'):
        return 'not_authenticated'
    if code=='300012':return 'ip_blocked'
    if code=='300015':return 'signature_error'
    if code in ('461','471') or re.search(r'^Error: Captcha required:',text,re.M):
        return 'verification_required'
    return 'api_error' if code is not None else 'unknown_error'


def _status(raw):
    if not isinstance(raw,dict) or raw.get('guest') is not False:
        raise _Invalid()
    identity=raw.get('user_id')
    if not isinstance(identity,str) or not re.fullmatch(r'[a-fA-F0-9]{24}',identity):
        raise _Invalid()
    return {'authenticated':True,'user':{'id':identity,'guest':False}}


def community_status_from_context(context, timeout_s=6):
    """Return False only for known unauthenticated state; fail closed otherwise."""
    stage = 'cookie_read'
    try:
        deadline = time.monotonic() + max(0, min(float(timeout_s), 6))
        rows = context.cookies('https://www.xiaohongshu.com')
        stage = 'cookie_select'
        header = _select_cookies(rows)
        stage = 'node_resolve'
        node = _node()
        stage = 'node_exit'
        code, stdout, stderr = _run([node, str(_ENTRY), 'whoami', '--cookie-source',
                                     'chrome', '--platform', 'xhs', '--json'],
                                    _child_env(header), deadline)
        if code != 0:
            raise _SessionError(_error_code(stderr))
        stage = 'json_parse'
        raw = json.loads(stdout.decode('utf-8'))
        stage = 'identity_shape'
        if isinstance(raw, dict) and raw.get('guest') is True:
            return False
        _status(raw)
        return True
    except _SessionError as error:
        if error.code == 'not_authenticated':
            return False
        raise _SessionError(error.code, stage) from None
    except Exception:
        raise _SessionError('unknown_error', stage) from None


def _main(args=None):
    try:
        operation,argv=_arguments(list(sys.argv[1:] if args is None else args))
        deadline=_deadline()
        node=_node()
        context=_note_context(argv[1]) if operation=='read' else None
        expected_account=_expected_account() if operation in ('search','feed') else None
        header=_profile_cookie_string(deadline)
        child_env=_child_env(header)
        command=[node,str(_ENTRY),*argv]
        if context:
            child_env['EASEL_XHS_NOTE_CONTEXT']=json.dumps(context,separators=(',',':'))
            command=[node,'--input-type=module','--eval',_NOTE_READ_BOOTSTRAP,str(_ENTRY)]
        elif operation=='feed':
            child_env['EASEL_XHS_EXPECTED_ACCOUNT']=expected_account
            command=[node,'--input-type=module','--eval',_FEED_BOOTSTRAP,str(_ENTRY)]
        elif operation=='search':
            child_env['EASEL_XHS_EXPECTED_ACCOUNT']=expected_account
            child_env['EASEL_XHS_SEARCH_KEYWORD']=argv[1]
            command=[node,'--input-type=module','--eval',_SEARCH_BOOTSTRAP,str(_ENTRY)]
        code,stdout,stderr=_run(command,child_env,deadline)
        if code!=0:
            error=_error_code(stderr)
        else:
            try:
                raw=json.loads(stdout.decode('utf-8'))
            except (ValueError,UnicodeError):
                raise _Invalid() from None
            if operation=='status':
                try:data=_status(raw)
                except _Invalid:
                    error='not_authenticated'
                else:
                    print(json.dumps({'ok':True,'data':data},ensure_ascii=False))
                    return 0
            else:
                print(json.dumps({'ok':True,'data':raw},ensure_ascii=False))
                return 0
    except _SessionError as exc:
        error='read_timeout' if exc.code=='timeout' else exc.code
    except KeyboardInterrupt:
        error='cancelled'
    except Exception:
        error='unknown_error'
    print(json.dumps({'ok':False,'error':{'code':error,'message':'小红书数据源读取未确认。'}},ensure_ascii=False))
    return 1


def _cancel_signal(_signum,_frame):
    raise KeyboardInterrupt()


def main(args=None):
    previous={}
    try:
        for name in (signal.SIGTERM,signal.SIGINT):
            try:
                old=signal.signal(name,_cancel_signal)
                previous[name]=old
            except ValueError:
                break  # Direct library calls on a non-main thread install no handlers.
        return _main(args)
    finally:
        for name,handler in previous.items():
            signal.signal(name,handler)


if __name__=='__main__':
    raise SystemExit(main())
