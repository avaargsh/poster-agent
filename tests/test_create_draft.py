"""Exercise browser draft persistence without accessing user browser storage."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_draft_module_served_and_restore_actions_present():
    client = TestClient(app)
    assert client.get('/static/create-draft.js').status_code == 200
    page = client.get('/').text
    for element in ('createDraftStatus', 'restoreCreateDraft', 'saveCreateDraft',
                    'discardCreateDraft'):
        assert f'id="{element}"' in page


def test_draft_storage_conflicts_and_restricted_payload():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed to exercise the browser storage module')
    script = r'''
const assert = require('node:assert/strict');
const {createStore, decode} = require('./app/static/create-draft.js');
let raw=null;
const storage={getItem:()=>raw,setItem:(_,v)=>{raw=v},removeItem:()=>{raw=null}};
const one=createStore(storage),two=createStore(storage);
const data={fields:{title:'手动填写',price:'',layout_id:'hero',review_run_id:'secret',
  background_mode:'ai'},brief:'尚未完成的需求',files:{product:'商品.png',logo:''}};
assert.equal(one.save(data).state,'saved');
assert.equal(two.save({...data,brief:'另一标签页'}).state,'conflict');
assert.equal(two.discard().state,'conflict');
assert.equal(one.read().draft.fields.title,'手动填写');
assert.equal(one.read().draft.fields.price,'');
assert.equal(one.read().draft.fields.review_run_id,undefined);
assert.equal(one.read().draft.fields.background_mode,undefined);
two.accept();
assert.equal(two.save({...data,brief:'已明确选择保留此页'}).state,'saved');
assert.equal(one.save(data).state,'conflict');
assert.equal(decode('not-json'),null);
assert.equal(decode(JSON.stringify({version:999})),null);
assert.equal(decode(JSON.stringify({version:1,savedAt:1,fields:{title:4}})),null);
assert.equal(decode(JSON.stringify({...data,version:1,savedAt:Number.MAX_SAFE_INTEGER})),null);
assert.equal(decode('x'.repeat(25000)),null);
const broken=createStore({getItem:()=>{throw Error('blocked')}});
assert.equal(broken.save(data).state,'unavailable');
assert.equal(broken.read().state,'unavailable');
assert.equal(broken.discard().state,'unavailable');
const full=createStore({getItem:()=>null,setItem:()=>{throw Error('quota exceeded')}});
assert.equal(full.save(data).state,'unavailable');
'''
    result = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def _run_node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed to exercise the browser storage module')
    result = subprocess.run([node, '-e', script],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_draft_field_bounds_and_whitelist():
    _run_node(r'''
const assert = require('node:assert/strict');
const {createStore} = require('./app/static/create-draft.js');
let raw=null;
const storage={getItem:()=>raw,setItem:(_,v)=>{raw=v},removeItem:()=>{raw=null}};
const store=createStore(storage);
function payload(overrides){return {fields:Object.assign({title:'正常标题',price:'9.9'},overrides||{}),brief:'需求',files:{product:'a.png',logo:''}};}
assert.equal(store.save(payload({title:'x'.repeat(35)})).state,'saved');
assert.equal(store.save(payload({title:'x'.repeat(36)})).state,'invalid');
assert.equal(store.save(payload({subtitle:'x'.repeat(45)})).state,'saved');
assert.equal(store.save(payload({subtitle:'x'.repeat(46)})).state,'invalid');
assert.equal(store.save(payload({cta:'x'.repeat(12)})).state,'saved');
assert.equal(store.save(payload({cta:'x'.repeat(13)})).state,'invalid');
assert.equal(store.save(payload({style:'x'.repeat(80)})).state,'saved');
assert.equal(store.save(payload({style:'x'.repeat(81)})).state,'invalid');
const longBrief=payload();longBrief.brief='x'.repeat(8000);
assert.equal(store.save(longBrief).state,'saved');
longBrief.brief='x'.repeat(8001);
assert.equal(store.save(longBrief).state,'invalid');
const longFile=payload();longFile.files={product:'p'.repeat(256),logo:'l'.repeat(256)};
assert.equal(store.save(longFile).state,'saved');
longFile.files={product:'p'.repeat(257),logo:''};
assert.equal(store.save(longFile).state,'invalid');
assert.equal(store.save(payload({layout_id:'classic'})).state,'saved');
assert.equal(store.save(payload({layout_id:'hero'})).state,'saved');
assert.equal(store.save(payload({layout_id:'mosaic'})).state,'invalid');
assert.equal(store.save(payload({product_align:'top'})).state,'saved');
assert.equal(store.save(payload({product_align:'left'})).state,'invalid');
assert.equal(store.save(payload({product_scale:'compact'})).state,'saved');
assert.equal(store.save(payload({product_scale:'thumb'})).state,'invalid');
assert.equal(store.save(payload({price:12})).state,'invalid');
assert.equal(store.save({fields:{title:'只有标题'},brief:''}).state,'saved');
const read=store.read();
assert.equal(read.state,'available');
assert.equal(read.draft.files.product,'');
assert.equal(read.draft.files.logo,'');
''')


def test_draft_lifecycle_states():
    _run_node(r'''
const assert = require('node:assert/strict');
const {createStore} = require('./app/static/create-draft.js');
let raw=null;
const storage={getItem:()=>raw,setItem:(_,v)=>{raw=v},removeItem:()=>{raw=null}};
const store=createStore(storage);
const data={fields:{title:'生命周期',price:'9.9'},brief:'',files:{product:'a.png',logo:''}};
assert.equal(store.read().state,'empty');
assert.equal(store.save(data).state,'saved');
assert.equal(store.read().state,'available');
assert.equal(store.discard().state,'empty');
assert.equal(store.read().state,'empty');
assert.equal(store.save(data).state,'saved');
assert.equal(store.read().state,'available');
raw='{broken json';
assert.equal(store.read().state,'invalid');
raw='null';
assert.equal(store.read().state,'invalid');
raw=null;
assert.equal(store.read().state,'empty');
''')


def test_draft_persists_only_whitelisted_copy_and_file_names():
    _run_node(r'''
const assert = require('node:assert/strict');
const {createStore, KEY} = require('./app/static/create-draft.js');
let raw=null;
const storage={getItem:()=>raw,setItem:(_,v)=>{raw=v},removeItem:()=>{raw=null}};
const store=createStore(storage);
const data={fields:{title:'带图片的草稿',price:'9.9',background_mode:'ai',
  review_run_id:'internal-id',background_image:'data:image/png;base64,AAAA'},
  brief:'',files:{product:'海报原图.jpg',logo:'品牌logo.png'}};
assert.equal(store.save(data).state,'saved');
const stored=JSON.parse(raw);
assert.deepEqual(Object.keys(stored).sort(),['brief','fields','files','savedAt','version']);
assert.equal(stored.version,1);
assert.equal(Number.isSafeInteger(stored.savedAt),true);
assert.deepEqual(stored.files,{product:'海报原图.jpg',logo:'品牌logo.png'});
assert.ok(!raw.includes('background_mode'));
assert.ok(!raw.includes('review_run_id'));
assert.ok(!raw.includes('base64'));
assert.ok(!raw.includes('AAAA'));
const read=store.read();
assert.equal(read.draft.fields.background_mode,undefined);
assert.equal(read.draft.fields.review_run_id,undefined);
''')


def test_create_draft_page_wires_module_and_state_copy():
    client = TestClient(app)
    page = client.get('/').text
    assert '<script src="/static/create-draft.js"></script>' in page
    assert ("const createDraftFields=['title','subtitle','price','original_price','cta',"
            "'style','layout_id','product_align','product_scale']") in page
    for copy in ('文案与版式会自动保存在此浏览器', '另一页面更新了草稿',
                 '草稿已恢复，已使用免费配色背景', '已删除浏览器中的草稿',
                 '已存草稿格式无法读取', '正在保存文案与版式'):
        assert copy in page
    assert "field('background_mode').value='local'" in page
    assert "field('product').value=''" in page
    assert "field('logo').value=''" in page


def test_static_mount_serves_only_static_directory():
    client = TestClient(app)
    assert client.get('/static/create-draft.js').status_code == 200
    response = client.get('/static/%2e%2e/app/main.py')
    assert response.status_code in (400, 404)
