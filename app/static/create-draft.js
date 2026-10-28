/* Local draft storage. Only editable copy, layout and file names are persisted. */
(function(root) {
  'use strict';
  const KEY='poster-create-draft-v1';
  const limits={title:35,subtitle:45,price:100,original_price:100,cta:12,style:80,
    layout_id:20,product_align:20,product_scale:20};
  const choices={layout_id:['classic','hero'],product_align:['top','center','bottom'],
    product_scale:['full','compact']};
  function clean(data) {
    if(!data||typeof data!=='object'||!data.fields||typeof data.fields!=='object')return null;
    const fields={};
    for(const [name,max] of Object.entries(limits)) {
      const value=data.fields[name];
      if(value===undefined)continue;
      if(typeof value!=='string'||value.length>max)return null;
      if(choices[name]&&!choices[name].includes(value))return null;
      fields[name]=value;
    }
    if(typeof data.brief!=='string'||data.brief.length>8000)return null;
    const files={};
    for(const name of ['product','logo']) {
      const value=data.files?.[name]??'';
      if(typeof value!=='string'||value.length>256)return null;
      files[name]=value;
    }
    return {fields,brief:data.brief,files};
  }
  function decode(raw) {
    if(typeof raw!=='string'||raw.length>20000)return null;
    try {
      const data=JSON.parse(raw),content=clean(data);
      if(!content||data.version!==1||!Number.isSafeInteger(data.savedAt)||data.savedAt<0||
          data.savedAt>8640000000000000)return null;
      return {...content,version:1,savedAt:data.savedAt};
    } catch {return null;}
  }
  function createStore(storage) {
    let expected=null;
    try {expected=storage.getItem(KEY);} catch { /* report on read/write */ }
    function read() {
      try {
        const raw=storage.getItem(KEY);
        return {state:raw===null?'empty':decode(raw)?'available':'invalid',draft:decode(raw),raw};
      } catch {return {state:'unavailable',draft:null};}
    }
    function accept() {
      const result=read();
      if(result.state!=='unavailable')expected=result.raw;
      return result;
    }
    function save(data) {
      const content=clean(data);
      if(!content)return {state:'invalid'};
      try {
        if(storage.getItem(KEY)!==expected)return {state:'conflict'};
        const raw=JSON.stringify({...content,version:1,savedAt:Date.now()});
        storage.setItem(KEY,raw);expected=raw;
        return {state:'saved',draft:decode(raw)};
      } catch {return {state:'unavailable'};}
    }
    function discard() {
      try {
        if(storage.getItem(KEY)!==expected)return {state:'conflict'};
        storage.removeItem(KEY);expected=null;
        return {state:'empty'};
      } catch {return {state:'unavailable'};}
    }
    return {read,accept,save,discard};
  }
  const api={KEY,decode,createStore};
  if(typeof module!=='undefined')module.exports=api;
  else root.PosterCreateDraft=api;
})(typeof window!=='undefined'?window:globalThis);
